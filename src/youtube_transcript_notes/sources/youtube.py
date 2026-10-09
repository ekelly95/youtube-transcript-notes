"""Videos on YouTube, via yt-dlp.

yt-dlp is pure transport; keeping up with YouTube is left to the people who do
it full time. One `extract_info(download=False)` call yields both the citation
metadata and the caption track list, so discovery costs one request and
downloads no captions.

**Trust tiers.** A video offers a few human-written tracks and often over a
hundred automatic ones, nearly all machine translations of the automatic
transcript. Automatic captions in the video's declared language are
`ASR_PLATFORM`; the rest are `TRANSLATED`.
"""

from __future__ import annotations

import importlib.metadata
import json
import re
import shutil
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import islice
from math import isfinite
from typing import Any, NoReturn, cast
from urllib.parse import SplitResult, parse_qs, urlsplit

from ..cache import Cache
from ..errors import (
    AcquisitionFailed,
    AgeRestricted,
    BotCheck,
    LectureUnavailable,
    MalformedCaptions,
    NoCaptionsAvailable,
    PayloadTooLarge,
    PlaylistEmpty,
    PlaylistNotSupported,
    PlaylistTooLarge,
    RateLimited,
    RegionBlocked,
    SourceError,
    TranscriptError,
    TransportContractChanged,
    TransportNotInstalled,
)
from ..limits import MAX_PAYLOAD_BYTES, MAX_PLAYLIST_ITEMS, describe_size
from ..models import Chapter, LectureMeta, TrustTier
from ..parse import parsers
from ..redact import redact, redact_url
from ..resolve import (
    UNKNOWN_LANGUAGE,
    Track,
    TrackHandle,
    TrackManifest,
    primary_subtag,
)
from .base import Expansion, SourceProvider, providers

__all__ = ["YouTubeProvider"]

#: One yt-dlp result. `Any` on purpose: a third party controls its shape, which
#: is why `_require_shape` checks it at runtime instead.
Info = dict[str, Any]

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")

#: Paths naming a channel or search: refused by name, never expanded.
_CHANNEL_PATHS = ("/channel/", "/c/", "/user/", "/results", "/@")

#: Everything that names a collection rather than a video.
_COLLECTION_PATHS = ("/playlist", *_CHANNEL_PATHS)

#: Path prefixes carrying the video id in the path. `youtu.be` has no prefix
#: and is handled separately.
_VIDEO_PATHS = ("/shorts/", "/live/", "/embed/", "/v/")

#: ``/embed/videoseries?list=PL...`` embeds a playlist. Eleven legal id
#: characters, so it must be excluded by name.
_PLAYLIST_EMBED = "videoseries"

#: yt-dlp's suffix for the original automatic track on a translated video.
_ORIGINAL_SUFFIX = "-orig"

#: Part of the cache *key*, so a shape change simply misses and rewrites.
_MANIFEST_VERSION = "1"
_EXPANSION_VERSION = "1"

#: Seconds to wait on a stalled socket; yt-dlp's default is unbounded.
_SOCKET_TIMEOUT = 30.0

#: Retries for a transient failure. Small, because silent minutes of retrying
#: look like a hang.
_RETRIES = 2

#: JavaScript runtimes yt-dlp may use to solve YouTube's challenges, in its own
#: order of preference. yt-dlp enables only Deno by default; Node and Bun are
#: as good, and far more often already installed.
_JS_RUNTIMES = ("deno", "node", "bun")

#: Said when no runtime is on PATH. yt-dlp has deprecated YouTube support
#: without one (2025.11.12); captions still arrive today, but formats may not.
_JS_RUNTIME_HINT = (
    "No JavaScript runtime was found on PATH, and yt-dlp needs one for full "
    "YouTube support. Install Deno "
    "(https://docs.deno.com/runtime/getting_started/installation/) or Node.js "
    "20 or newer, then retry."
)

#: Where caption tracks are read from. Absent means the contract moved;
#: present and empty means the video has no captions.
_CAPTION_KEYS = ("subtitles", "automatic_captions")

#: Phrases meaning *the transport is broken*, not *the video is unusable*. The
#: first two are yt-dlp's own suffixes for its bugs; the rest are what a player
#: change breaks first. Checked before `_FAILURE_PATTERNS`, because a broken
#: extractor says things that look like facts about the video.
_TRANSPORT_BROKEN_PATTERNS: tuple[str, ...] = (
    "please report this issue",
    "confirm you are on the latest version",
    "unable to extract player response",
    "unable to extract yt initial data",
    "unable to extract video data",
    "signature extraction failed",
    "nsig extraction failed",
)

#: Well-known failure phrases. Deliberately shallow: anything unrecognised
#: becomes `AcquisitionFailed` with the original message.
_FAILURE_PATTERNS: tuple[tuple[str, type[SourceError]], ...] = (
    # About the connection, not the video. Matching "not a bot" sidesteps
    # the curly apostrophe YouTube writes in "confirm you're".
    ("not a bot", BotCheck),
    ("http error 429", RateLimited),
    ("too many requests", RateLimited),
    ("confirm your age", AgeRestricted),
    ("age-restricted", AgeRestricted),
    ("inappropriate for some users", AgeRestricted),
    # yt-dlp: "The uploader has not made this video available in your country".
    ("available in your country", RegionBlocked),
    ("blocked it in your country", RegionBlocked),
    ("video unavailable", LectureUnavailable),
    ("video is unavailable", LectureUnavailable),
    # Qualified with "video": "ffmpeg is not available" is a broken install.
    ("this video is not available", LectureUnavailable),
    ("private video", LectureUnavailable),
    ("has been removed", LectureUnavailable),
)


@dataclass(frozen=True)
class YouTubeTrackRef:
    """What `load` needs to fetch one track, and what the cache keys on.

    The URL is signed and expires, so it is carried but never keyed on.
    """

    video_id: str
    language: str
    tier: TrustTier
    caption_format: str
    url: str

    offline_reason: TranscriptError | None = None
    """Set when rebuilt from a cached manifest after a transport failure. The
    URL is then empty, so only a cached payload can satisfy `load`, and this is
    the failure to report if there is none."""

    def cache_key(self) -> str:
        return Cache.key(
            "youtube",
            self.video_id,
            self.tier.value,
            self.language,
            self.caption_format,
        )

    @property
    def label(self) -> str:
        """How this track is named in a failure — never by its signed URL."""
        return f"{self.video_id} ({self.language}, {self.caption_format})"


@providers.register("youtube")
class YouTubeProvider(SourceProvider):
    """Fetches captions and citation metadata from YouTube."""

    name = "youtube"
    remote = True

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        cache: Cache | None = None,
        extractor: Callable[[str], Info] | None = None,
        opener: Callable[..., str] | None = None,
        flat_extractor: Callable[[str], Info] | None = None,
    ) -> None:
        """`extractor`, `opener` and `flat_extractor` let tests drive the
        provider from captured fixtures, without the network."""
        super().__init__(clock=clock, cache=cache)
        self._extract = extractor or _extract_info
        self._open = opener or _open_url
        self._extract_flat = flat_extractor or _extract_flat_info

    @classmethod
    def handles(cls, source: str) -> bool:
        if _is_youtube_url(source):
            return True
        return _VIDEO_ID.fullmatch(source) is not None

    def expand(self, source: str) -> Expansion:
        """Turn a playlist into its videos; anything else comes back alone.

        One flat request names the videos without visiting them. Channels and
        searches pass through so `list` can refuse them by name.
        """
        if not _is_playlist(source):
            return Expansion(sources=(source,))

        playlist_id = _playlist_id(source)
        try:
            ids = _require_playlist_ids(self._extract_flat(source), source)
        except SourceError as error:
            remembered = self._recall_expansion(source, playlist_id, error)
            if remembered is None:
                raise
            return remembered

        # Outside the fallback: size and emptiness are facts about the playlist
        # now, and must not be answered from the cache.
        if len(ids) > MAX_PLAYLIST_ITEMS:
            raise PlaylistTooLarge(source=source, limit=MAX_PLAYLIST_ITEMS)
        if not ids:
            raise PlaylistEmpty(source=source)

        self._remember_expansion(playlist_id, ids)
        # Watch URLs, not bare ids, which a same-named local file could capture.
        return Expansion(
            sources=tuple(_watch_url(video) for video in ids), origin=source
        )

    def list(self, source: str) -> TrackManifest:
        # `list` answers for one video; expansion happened a step earlier.
        if _is_collection(source):
            raise PlaylistNotSupported(source=source)

        try:
            info = _require_shape(self._extract(_watch_url(source)), source)
            meta = _meta_from(info)
            tracks = tuple(self._tracks_from(info, meta))
            if not tracks:
                _explain_empty_manifest(info, meta.source_id)
        except SourceError as error:
            # A `SourceError` means the source could not be reached or read, so
            # the cached manifest is the best answer. A `CaptionError` means it
            # was reached and has nothing usable — that must not be overridden.
            remembered = self._recall(source, error)
            if remembered is None:
                raise
            return remembered

        manifest = TrackManifest(meta=meta, tracks=tracks)
        self._remember(manifest)
        return manifest

    def load(self, ref: Any) -> str:
        key = ref.cache_key()
        cached = self.cache.read(key)
        if cached is not None:
            return cached

        if ref.offline_reason is not None:
            # Listed from cache, but this track was never fetched.
            raise ref.offline_reason

        try:
            payload = self._open(ref.url)
        except SourceError as error:
            # Name the track rather than the redacted endpoint; same class.
            error.context["source"] = ref.label
            raise

        self.cache.write(key, payload)
        return payload

    def _manifest_key(self, video_id: str) -> str:
        return Cache.key("youtube-manifest", _MANIFEST_VERSION, video_id)

    def _remember(self, manifest: TrackManifest) -> None:
        """Store what this video offers, so an outage cannot hide cached captions.

        Signed caption URLs are not stored: they expire within hours.
        """
        self.cache.write(
            self._manifest_key(manifest.meta.source_id),
            json.dumps(
                {
                    "meta": manifest.meta.to_dict(),
                    "tracks": [handle.track.to_dict() for handle in manifest],
                }
            ),
        )

    def _recall(self, source: str, reason: SourceError) -> TrackManifest | None:
        """The last manifest stored for this video, or None if there is none."""
        video_id = _video_id(source)
        if video_id is None:
            return None

        stored = self.cache.read(self._manifest_key(video_id))
        if stored is None:
            return None

        try:
            data = json.loads(stored)
            meta = LectureMeta.from_dict(data["meta"])
            tracks = [Track.from_dict(entry) for entry in data["tracks"]]
        except (ValueError, KeyError, TypeError):
            # An unreadable entry is a miss; the caller raises the real failure.
            return None

        return TrackManifest(
            meta=meta,
            tracks=tuple(
                TrackHandle(
                    track=track,
                    meta=meta,
                    provider=self,
                    ref=YouTubeTrackRef(
                        video_id=meta.source_id,
                        language=track.raw_language,
                        tier=track.tier,
                        caption_format=track.caption_format,
                        url="",
                        offline_reason=reason,
                    ),
                )
                for track in tracks
            ),
            stale_reason=reason,
        )

    def _expansion_key(self, playlist_id: str) -> str:
        return Cache.key("youtube-playlist", _EXPANSION_VERSION, playlist_id)

    def _remember_expansion(self, playlist_id: str | None, ids: Sequence[str]) -> None:
        """Store this playlist's videos, keyed on its id. No id, no cache."""
        if playlist_id is None:
            return
        self.cache.write(
            self._expansion_key(playlist_id), json.dumps({"videos": list(ids)})
        )

    def _recall_expansion(
        self, source: str, playlist_id: str | None, reason: SourceError
    ) -> Expansion | None:
        """The last roster stored for this playlist, or None if there is none."""
        if playlist_id is None:
            return None

        stored = self.cache.read(self._expansion_key(playlist_id))
        if stored is None:
            return None

        try:
            videos = json.loads(stored)["videos"]
        except (ValueError, KeyError, TypeError):
            return None

        if (
            not videos
            or not isinstance(videos, list)
            or not all(isinstance(video, str) for video in videos)
        ):
            # An empty roster is never written, so this entry is corrupt.
            return None

        return Expansion(
            sources=tuple(_watch_url(video) for video in videos),
            origin=source,
            stale_reason=reason,
        )

    def _tracks_from(self, info: Info, meta: LectureMeta) -> Iterator[TrackHandle]:
        spoken = (info.get("language") or UNKNOWN_LANGUAGE).lower()

        groups: list[tuple[Info, str | None]] = [
            (info.get("subtitles") or {}, None),
            (info.get("automatic_captions") or {}, spoken),
        ]

        for captions, spoken_language in groups:
            # Originals first, so they win ties in `find`: a plain `en` beside
            # `en-orig` is served through YouTube's translation endpoint
            # (`tlang`), which is rate-limited far sooner than the original.
            ordered = sorted(
                captions.items(), key=lambda item: not _is_original(item[0])
            )
            for raw_language, entries in ordered:
                tier = _tier_for(raw_language, spoken_language)
                for entry in entries or ():
                    # Skipped, not trusted; `_explain_empty_manifest` reports
                    # it if nothing usable is left.
                    if not isinstance(entry, dict):
                        continue
                    fmt = entry.get("ext", "")
                    if fmt not in parsers or not entry.get("url"):
                        continue
                    yield TrackHandle(
                        track=Track(
                            language=primary_subtag(raw_language),
                            raw_language=raw_language,
                            tier=tier,
                            caption_format=fmt,
                            label=entry.get("name"),
                        ),
                        meta=meta,
                        provider=self,
                        ref=YouTubeTrackRef(
                            video_id=meta.source_id,
                            language=raw_language,
                            tier=tier,
                            caption_format=fmt,
                            url=entry["url"],
                        ),
                    )


def _yt_dlp_version() -> str:
    """The installed transport version, read from metadata without importing it."""
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return "(not installed)"


def _js_runtime_hint() -> tuple[str, ...]:
    """The install hint, when no JavaScript runtime yt-dlp could use is on PATH."""
    if any(shutil.which(runtime) for runtime in _JS_RUNTIMES):
        return ()
    return (_JS_RUNTIME_HINT,)


def _contract_changed(source: str, detail: str) -> TransportContractChanged:
    return TransportContractChanged(
        source=source,
        detail=detail,
        version=_yt_dlp_version(),
        also_try=_js_runtime_hint(),
    )


def _require_shape(info: Any, source: str) -> Info:
    """Check the extractor returned something the tool can still read.

    This replaces a version ceiling on yt-dlp: it fires exactly when the shape
    moved, on the machine with the problem, and names what moved. Membership
    tests, never truthiness — caption keys *absent* is a fact about the tool,
    *present and empty* a fact about the video.
    """
    if info is None:
        raise _contract_changed(source, "the extractor returned nothing at all")

    if not isinstance(info, dict):
        raise _contract_changed(
            source, f"the metadata came back as {type(info).__name__}, not an object"
        )

    # The id names the file and keys the cache, so an empty one is refused.
    identifier = info.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise _contract_changed(
            source,
            f"the metadata carries no usable 'id' ({identifier!r:.40}) — and "
            "the source id is what names a lecture's file and keys its cache, "
            "so one without it cannot be told apart from any other",
        )

    if not any(key in info for key in _CAPTION_KEYS):
        raise _contract_changed(
            source,
            "the metadata carries neither a 'subtitles' nor an "
            "'automatic_captions' key, which is where caption tracks are read "
            "from",
        )

    for key in _CAPTION_KEYS:
        value = info.get(key)
        if value is not None and not isinstance(value, dict):
            raise _contract_changed(
                source,
                f"{key!r} came back as {type(value).__name__} rather than an "
                "object keyed by language",
            )

    return info


def _require_playlist_ids(info: Any, source: str) -> tuple[str, ...]:
    """Check the flat extraction still has the shape playlists are read from.

    ``entries`` absent is a contract change; present and empty is judged by the
    caller. Reads at most one entry past `MAX_PLAYLIST_ITEMS`, enough to tell
    "at the limit" from "over it". A deleted video still has an id and fails
    later, per video, where that failure belongs.
    """
    if info is None:
        raise _contract_changed(source, "the extractor returned nothing at all")

    if not isinstance(info, dict):
        raise _contract_changed(
            source, f"the metadata came back as {type(info).__name__}, not an object"
        )

    if "entries" not in info:
        raise _contract_changed(
            source,
            "the metadata carries no 'entries' key, which is where a "
            "playlist's videos are read from",
        )

    entries = info["entries"]
    if isinstance(entries, (str, bytes, dict)) or not isinstance(entries, Iterable):
        raise _contract_changed(
            source,
            f"'entries' came back as {type(entries).__name__} rather than a "
            "sequence of videos",
        )

    ids = []
    for position, entry in enumerate(islice(entries, MAX_PLAYLIST_ITEMS + 1), start=1):
        if not isinstance(entry, dict):
            raise _contract_changed(
                source,
                f"playlist entry {position} came back as "
                f"{type(entry).__name__}, not an object",
            )
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not _is_video_id(identifier):
            raise _contract_changed(
                source,
                f"playlist entry {position} carries no usable video id "
                f"({identifier!r:.40}) — and the id is the only thing an "
                "entry is expanded into",
            )
        ids.append(identifier)

    return tuple(ids)


def _explain_empty_manifest(info: Info, source: str) -> NoReturn:
    """Say why nothing usable came back, without guessing.

    No tracks listed is a fact about the video (`NoCaptionsAvailable`). Tracks
    listed but none usable — no recognised ``ext`` or no ``url`` — looks like a
    renamed field, so it is a contract change naming what was offered.
    """
    entries = [
        entry
        for key in _CAPTION_KEYS
        for group in (info.get(key) or {}).values()
        for entry in group or ()
    ]
    if not entries:
        raise NoCaptionsAvailable(source=source)

    offered = sorted(
        {str(entry.get("ext")) for entry in entries if isinstance(entry, dict)}
        - {"None"}
    )
    raise _contract_changed(
        source,
        f"{len(entries)} caption tracks are listed, but not one of them is "
        f"usable. Offered: {', '.join(offered) or 'nothing naming a format'}. "
        f"This tool reads: {', '.join(parsers.names())}",
    )


def _is_original(raw_language: str) -> bool:
    """yt-dlp's marker for the transcription the other automatic tracks translate."""
    return raw_language.lower().endswith(_ORIGINAL_SUFFIX)


def _tier_for(raw_language: str, spoken_language: str | None) -> TrustTier:
    """Classify one caption track.

    `spoken_language` is None for human-written tracks. For automatic ones it
    is the video's declared language; anything else is a translation.
    """
    if spoken_language is None:
        return TrustTier.MANUAL

    language = raw_language.lower()
    if _is_original(language):
        # yt-dlp's marker for the original transcription — trusted even when
        # the video declares no language to compare against.
        return TrustTier.ASR_PLATFORM

    if primary_subtag(language) == primary_subtag(spoken_language):
        return TrustTier.ASR_PLATFORM
    return TrustTier.TRANSLATED


def _meta_from(info: Info) -> LectureMeta:
    """Citation metadata, read forgivingly: a strange chapter list should not
    cost the transcript. Caption tracks are `_require_shape`'s job."""
    return LectureMeta(
        source_id=info.get("id", ""),
        title=info.get("title") or info.get("id", ""),
        url=info.get("webpage_url"),
        channel=info.get("channel") or info.get("uploader"),
        published=_published(info),
        duration=_optional_float(info.get("duration")),
        chapters=_chapters(info.get("chapters")),
    )


def _chapters(raw: Any) -> tuple[Chapter, ...]:
    """Published chapters, skipping any without a usable start."""
    chapters = []
    for chapter in raw or ():
        if not isinstance(chapter, dict):
            continue
        start = _optional_float(chapter.get("start_time"))
        if start is None:
            continue
        chapters.append(
            Chapter(
                title=str(chapter.get("title", "")),
                start=start,
                end=_optional_float(chapter.get("end_time")),
            )
        )
    return tuple(chapters)


def _published(info: Info) -> date | None:
    """The publication date, or None if it does not read as one.

    The constructor sits inside the `try` because ``"00000000"`` passes every
    shape test and is still not a date.
    """
    stamp = info.get("upload_date") or info.get("release_date")
    if not isinstance(stamp, str) or len(stamp) != 8:
        return None

    try:
        return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
    except ValueError:
        return None


def _optional_float(value: Any) -> float | None:
    """A finite number, or None. JSON admits ``NaN`` and ``Infinity``, which
    would later crash `format_timestamp`."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _split(source: str) -> SplitResult:
    """Parse a URL, tolerating a missing scheme (``youtube.com/watch?v=X``)."""
    return urlsplit(source if "//" in source else f"https://{source}")


def _is_youtube_url(source: str) -> bool:
    """Whether `source` is a URL whose *host* is YouTube's — not a substring match."""
    try:
        host = _split(source).hostname or ""
    except ValueError:
        return False

    return any(host == known or host.endswith(f".{known}") for known in _HOSTS)


def _is_video_id(segment: str) -> bool:
    """Whether one path segment is a video ID."""
    return segment != _PLAYLIST_EMBED and _VIDEO_ID.fullmatch(segment) is not None


def _video_id_in_path(parts: SplitResult) -> str | None:
    """The video ID this address carries in its path, if any.

    ``youtu.be/<id>`` and the ``/shorts/``, ``/live/``, ``/embed/`` and ``/v/``
    forms have no ``v=``. Knowing that keeps ``youtu.be/<id>?list=...`` — what
    the share button produces — a single video rather than a playlist.
    """
    host = (parts.hostname or "").lower()
    segments = [segment for segment in parts.path.split("/") if segment]

    if host == "youtu.be" or host.endswith(".youtu.be"):
        candidate = segments[0] if segments else ""
    elif parts.path.startswith(_VIDEO_PATHS):
        candidate = segments[1] if len(segments) > 1 else ""
    else:
        return None

    return candidate if _is_video_id(candidate) else None


def _names_a_video_in_its_path(parts: SplitResult) -> bool:
    return _video_id_in_path(parts) is not None


def _video_id(source: str) -> str | None:
    """Which video this source names, however it was addressed.

    The manifest cache keys on this, so every spelling of one video shares an
    entry.
    """
    if _VIDEO_ID.fullmatch(source):
        return source

    if not _is_youtube_url(source):
        return None

    parts = _split(source)
    for candidate in parse_qs(parts.query).get("v", ()):
        if _is_video_id(candidate):
            return candidate

    return _video_id_in_path(parts)


def _is_collection(source: str) -> bool:
    """Whether this URL names a playlist or channel rather than one video.

    ``watch?v=ID&list=PL...`` is one video that happens to sit in a playlist;
    only an address with no video in it at all is a collection.
    """
    if not _is_youtube_url(source):
        return False

    parts = _split(source)
    if parts.path.startswith(_COLLECTION_PATHS):
        return True

    query = parse_qs(parts.query)
    if "v" in query or _names_a_video_in_its_path(parts):
        return False
    return "list" in query


def _is_playlist(source: str) -> bool:
    """Whether this collection is a playlist — the kind `expand` can unroll."""
    return _is_collection(source) and not _split(source).path.startswith(_CHANNEL_PATHS)


def _playlist_id(source: str) -> str | None:
    """Which playlist this source names, for the expansion cache."""
    candidates = parse_qs(_split(source).query).get("list", ())
    return candidates[0] if candidates else None


def _watch_url(source: str) -> str:
    if _is_youtube_url(source):
        return source
    return f"https://www.youtube.com/watch?v={source}"


def _extract_info(url: str) -> Info:
    """One yt-dlp metadata call. No captions are downloaded here."""
    ydl = _youtube_dl()
    try:
        # `cast`, not a typed local, so the return stays inside the `try` the
        # offline suite can cover. `_require_shape` does the checking.
        return cast(Info, ydl.extract_info(url, download=False))
    except Exception as error:
        raise _classify(url, error) from error


def _extract_flat_info(url: str) -> Info:
    """One yt-dlp playlist call. The videos are named, never visited."""
    ydl = _youtube_dl(flat=True)
    try:
        return cast(Info, ydl.extract_info(url, download=False))
    except Exception as error:
        raise _classify(url, error) from error


def _open_url(url: str, source: str = "youtube") -> str:
    """Fetch a caption payload through yt-dlp, so its transport settings apply.

    Read in bounded chunks, so the far end cannot choose how much memory this
    process uses.
    """
    ydl = _youtube_dl()
    try:
        response = ydl.urlopen(url)
    except Exception as error:
        raise _classify(url, error) from error

    # Outside the `try`: `PayloadTooLarge` is about the payload, not transport.
    # Closed either way; guarded because response shapes vary.
    try:
        return _read_capped(response, source)
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


#: Read size: a handful of reads for an ordinary caption file.
_CHUNK_BYTES = 64 * 1024


def _read_capped(response: Any, source: str) -> str:
    """Read at most `MAX_PAYLOAD_BYTES`, refusing anything past it.

    ``Content-Length`` only allows an early refusal; the streaming cap is the
    real defence, since the header can be absent or wrong.
    """
    declared = _declared_length(response)
    if declared is not None and declared > MAX_PAYLOAD_BYTES:
        raise PayloadTooLarge(
            source=source,
            measured=describe_size(declared),
            limit=describe_size(MAX_PAYLOAD_BYTES),
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        # One byte past the limit tells "exactly at it" from "more coming".
        chunk = response.read(min(_CHUNK_BYTES, MAX_PAYLOAD_BYTES + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_PAYLOAD_BYTES:
            raise PayloadTooLarge(
                source=source,
                measured=f"more than {describe_size(MAX_PAYLOAD_BYTES)}",
                limit=describe_size(MAX_PAYLOAD_BYTES),
            )
        chunks.append(chunk)

    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as error:
        # Retrying will not read it differently, so it is a typed refusal.
        raise MalformedCaptions(
            source=source,
            fmt="downloaded",
            detail=f"not UTF-8 text ({error.reason})",
        ) from error


def _declared_length(response: Any) -> int | None:
    """``Content-Length``, if the response offers one that is a number."""
    headers = getattr(response, "headers", None)
    raw: Any = headers.get("Content-Length") if headers is not None else None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _youtube_dl(flat: bool = False) -> Any:
    """A configured `YoutubeDL`, typed `Any` because yt-dlp ships no types."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:  # pragma: no cover - depends on install extras
        raise TransportNotInstalled(source="youtube") from error

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noprogress": True,
        # A URL naming a video is one video even with `list=`; yt-dlp's default
        # would extract the whole playlist. Inert on the flat path.
        "noplaylist": True,
        "socket_timeout": _SOCKET_TIMEOUT,
        "retries": _RETRIES,
        "extractor_retries": _RETRIES,
        "js_runtimes": {runtime: {} for runtime in _JS_RUNTIMES},
    }
    if flat:
        # Name every video in one request without visiting any. `playlistend`
        # is a request, so `_require_playlist_ids` caps its own read too.
        options["extract_flat"] = "in_playlist"
        options["playlistend"] = MAX_PLAYLIST_ITEMS + 1

    return YoutubeDL(options)


def _classify(source: str, error: Exception) -> SourceError:
    """Map a transport failure onto the taxonomy, or admit we do not know.

    Matching happens on the original message; both the source and the message
    are redacted, since yt-dlp quotes signed URLs in its errors.
    """
    message = str(error)
    lowered = message.lower()
    source = redact_url(source)
    message = redact(message)

    for phrase in _TRANSPORT_BROKEN_PATTERNS:
        if phrase in lowered:
            return _contract_changed(
                source, f"yt-dlp failed inside its own extraction code — {message}"
            )

    for phrase, failure in _FAILURE_PATTERNS:
        if phrase in lowered:
            return failure(source=source)

    return AcquisitionFailed(source=source, detail=message, also_try=_js_runtime_hint())
