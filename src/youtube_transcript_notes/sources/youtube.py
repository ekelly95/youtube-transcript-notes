"""Lectures on YouTube, via yt-dlp.

yt-dlp is used purely as transport. Everything this project is actually about
— the two-stage API, trust tiers, reassembly, provenance — is built on top,
and the one genuinely thankless job, keeping up with YouTube's extraction
changes, is left to people who do it full time.

A single `extract_info(download=False)` call yields both the metadata needed to
cite the lecture and the list of caption tracks, which is why discovery here
costs exactly one request and downloads no captions at all.

**On trust tiers.** A typical lecture offers a handful of human-written tracks
and around 157 automatic ones, nearly all of which are machine translations of
the automatic transcript rather than transcripts in their own right. The video's
declared language separates them: automatic captions in that language are
`ASR_PLATFORM`, and the rest are `TRANSLATED` — a translation of a
transcription, and the least trustworthy thing on offer.
"""

from __future__ import annotations

import importlib.metadata
import json
import re
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
    LectureUnavailable,
    MalformedCaptions,
    NoCaptionsAvailable,
    PayloadTooLarge,
    PlaylistEmpty,
    PlaylistNotSupported,
    PlaylistTooLarge,
    RegionBlocked,
    SourceError,
    TranscriptError,
    TransportContractChanged,
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

#: One yt-dlp result. The values stay `Any` deliberately: this is the only
#: dictionary in the project whose shape a third party controls and may change
#: without warning, which is the entire reason `_require_shape` exists. An
#: annotation claiming to know the shape would be a promise this module is not
#: in a position to keep, and would quietly disable the checks that are.
Info = dict[str, Any]

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")

#: URL paths that name a channel or a search rather than a lecture. Separate
#: from the playlist path because the two kinds of collection get different
#: treatment: a playlist holds a bounded, ordered list of videos and can be
#: expanded into them, while a channel or search page has no such list to
#: become and stays refused by name.
_CHANNEL_PATHS = ("/channel/", "/c/", "/user/", "/results", "/@")

#: Everything that names a collection rather than a lecture, expandable or not.
_COLLECTION_PATHS = ("/playlist", *_CHANNEL_PATHS)

#: URL path prefixes that carry the video ID in the path rather than in a
#: ``v=`` parameter. `youtu.be` is the same idea with no prefix at all — there
#: the id is the whole path, which is why it is handled separately below.
_VIDEO_PATHS = ("/shorts/", "/live/", "/embed/", "/v/")

#: YouTube's sentinel for "embed a whole playlist rather than one video", as in
#: ``/embed/videoseries?list=PL...``. It is eleven characters drawn from the
#: legal video-ID alphabet, so no test of *shape* can tell it apart from a real
#: id — it has to be excluded by name.
_PLAYLIST_EMBED = "videoseries"

#: yt-dlp marks the original automatic track with this suffix when the video
#: also carries auto-translations.
_ORIGINAL_SUFFIX = "-orig"

#: Bumped when the stored manifest's shape changes. Part of the cache *key*
#: rather than a field inside the record, so an old entry simply misses and is
#: rewritten. A version stored inside would need a read-back check, an error to
#: report a mismatch, and a reader for a format nothing writes any more — three
#: things to maintain in exchange for the same outcome.
_MANIFEST_VERSION = "1"

#: Same idea, for stored playlist expansions.
_EXPANSION_VERSION = "1"

#: Seconds to wait on a stalled socket. yt-dlp's own default is no bound at
#: all, and an unbounded wait is indistinguishable from a hang — which matters
#: for something someone runs in a terminal and watches. Generous enough for a
#: 700 KB caption payload over a poor connection.
_SOCKET_TIMEOUT = 30.0

#: Retries for a transient failure, applied to both the network and the
#: extractor. Small on purpose: `AcquisitionFailed` already tells the reader to
#: try again, and a CLI that silently retries for minutes looks like a hang.
_RETRIES = 2

#: The two keys caption tracks are read from. Absent means the contract moved;
#: present and empty means the lecture really has no captions. See
#: `_require_shape` for why that distinction is worth this much care.
_CAPTION_KEYS = ("subtitles", "automatic_captions")

#: Phrases that mean *the transport is broken*, not *the lecture is unusable*.
#:
#: The first two are yt-dlp saying so itself: it appends both to errors it
#: considers its own bug rather than a fact about the video. Taking its word
#: beats guessing at what its extraction failures look like, the same reasoning
#: that makes `_ORIGINAL_SUFFIX` worth trusting.
#:
#: The rest are named because a YouTube player change breaks them first and
#: they do not always carry the report-this-issue suffix. They are what a
#: `pip install -U yt-dlp` fixes, so they must not be filed as a deleted video.
_TRANSPORT_BROKEN_PATTERNS: tuple[str, ...] = (
    "please report this issue",
    "confirm you are on the latest version",
    "unable to extract player response",
    "unable to extract yt initial data",
    "unable to extract video data",
    "signature extraction failed",
    "nsig extraction failed",
)

#: Well-known yt-dlp failure phrases. Matching on English error text is
#: unpleasant and deliberately shallow: only clearly-recognisable cases are
#: classified, and anything else becomes `AcquisitionFailed` carrying the
#: original message. A wrong guess would be worse than an honest fallback.
#:
#: Checked *after* `_TRANSPORT_BROKEN_PATTERNS`, because a broken extractor
#: says all sorts of things and some of them look like facts about the video.
_FAILURE_PATTERNS: tuple[tuple[str, type[SourceError]], ...] = (
    ("confirm your age", AgeRestricted),
    ("age-restricted", AgeRestricted),
    ("inappropriate for some users", AgeRestricted),
    # yt-dlp's actual phrasing is "The uploader has not made this video
    # available in your country", so match the tail rather than a guess at
    # the whole sentence.
    ("available in your country", RegionBlocked),
    ("blocked it in your country", RegionBlocked),
    ("video unavailable", LectureUnavailable),
    ("video is unavailable", LectureUnavailable),
    # Qualified with "video". A bare "is not available" also matches "ffmpeg
    # is not available" and every other missing-component complaint, and
    # diagnosing a broken install as a deleted lecture sends the reader to
    # check a video that is fine.
    ("this video is not available", LectureUnavailable),
    ("private video", LectureUnavailable),
    ("has been removed", LectureUnavailable),
)


@dataclass(frozen=True)
class YouTubeTrackRef:
    """What `load` needs to fetch one track, and what the cache keys on.

    The URL is carried but never keyed on: YouTube's caption URLs are signed
    and expire, so caching by URL would miss every time.
    """

    video_id: str
    language: str
    tier: TrustTier
    caption_format: str
    url: str

    offline_reason: TranscriptError | None = None
    """Set when this ref was rebuilt from a cached manifest because the
    transport was unreachable. The URL is empty in that case — a stored signed
    URL would have expired anyway — so only a cached payload can satisfy
    `load`. Carrying the original failure means a track that was never fetched
    reports why it cannot be, instead of a puzzling complaint about an empty
    address."""

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
        """How this track is named in a failure.

        The three things a reader needs to know which request went wrong, and
        none of the things a signed URL would also have carried. `redact`
        keeps a URL safe to print; this avoids printing one at all.
        """
        return f"{self.video_id} ({self.language}, {self.caption_format})"


@providers.register("youtube")
class YouTubeProvider(SourceProvider):
    """Fetches lecture captions and citation metadata from YouTube."""

    name = "youtube"

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        cache: Cache | None = None,
        extractor: Callable[[str], Info] | None = None,
        opener: Callable[..., str] | None = None,
        flat_extractor: Callable[[str], Info] | None = None,
    ) -> None:
        """`extractor`, `opener` and `flat_extractor` exist so the whole
        provider can be driven from captured fixtures — the test suite never
        reaches the network."""
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

        One flat request that names the videos without visiting any of them —
        the same costs-one-request, downloads-nothing discipline `list` keeps.
        Channels and search pages pass through untouched, so `list` can go on
        refusing them by name.
        """
        if not _is_playlist(source):
            return Expansion(sources=(source,))

        playlist_id = _playlist_id(source)
        try:
            ids = _require_playlist_ids(self._extract_flat(source), source)
        except SourceError as error:
            # The same line `list` draws: a source that cannot be reached
            # makes last week's answer the best available.
            remembered = self._recall_expansion(source, playlist_id, error)
            if remembered is None:
                raise
            return remembered

        # Outside the fallback on purpose. Size and emptiness are facts
        # learned *by reaching* the playlist as it stands now, and answering
        # either one from the cache would invent videos it no longer offers.
        if len(ids) > MAX_PLAYLIST_ITEMS:
            raise PlaylistTooLarge(source=source, limit=MAX_PLAYLIST_ITEMS)
        if not ids:
            raise PlaylistEmpty(source=source)

        self._remember_expansion(playlist_id, ids)
        # Canonical watch URLs rather than bare ids: `provider_for` prefers a
        # path that exists over a video ID, so a bare id could be captured by
        # a same-named file in the working directory. An URL cannot.
        return Expansion(
            sources=tuple(_watch_url(video) for video in ids), origin=source
        )

    def list(self, source: str) -> TrackManifest:
        # Every collection is refused here, expandable or not — `list`
        # answers for one lecture, and expansion is `expand`'s job, a step
        # earlier.
        if _is_collection(source):
            raise PlaylistNotSupported(source=source)

        try:
            info = _require_shape(self._extract(_watch_url(source)), source)
            meta = _meta_from(info)
            tracks = tuple(self._tracks_from(info, meta))
            if not tracks:
                _explain_empty_manifest(info, meta.source_id)
        except SourceError as error:
            # `SourceError` and not `TranscriptError`, and the line the taxonomy
            # already draws is exactly the right one. A `SourceError` means the
            # source could not be reached or read, so last week's answer is the
            # best available and very likely still true. A `CaptionError` means
            # it *was* reached and had nothing usable to say — that is a fact
            # about the lecture as it stands now, and overriding it with a
            # cached manifest would be inventing captions that no longer exist.
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
            # Listed from a cached manifest, but this particular track was
            # never fetched, so there is nothing to serve. The transport
            # failure that put us in this mode is the honest explanation.
            raise ref.offline_reason

        try:
            payload = self._open(ref.url)
        except SourceError as error:
            # The classifier only ever saw a URL, so the best it could name the
            # failure after redaction was an endpoint — true, and the same for
            # every track on every lecture. The ref knows which one this is, so
            # it says so. Rewriting the context rather than the error keeps the
            # class, and with it the taxonomy the caller branches on.
            error.context["source"] = ref.label
            raise

        self.cache.write(key, payload)
        return payload

    def _manifest_key(self, video_id: str) -> str:
        return Cache.key("youtube-manifest", _MANIFEST_VERSION, video_id)

    def _remember(self, manifest: TrackManifest) -> None:
        """Store what this lecture offers, so an outage cannot hide it.

        Discovery is the one step that always cost a live request: caption
        payloads were cached but the metadata call reaching them was not, so a
        broken transport took out lectures whose captions were already sitting
        on disk. That made this module quietly untrue about what its cache was
        for — "a transcript you cited last week ought to still say what you
        quoted" only holds if you can still get to it.

        Signed caption URLs are deliberately not stored. They expire in hours,
        so they would be worthless by the time this entry mattered, and they
        are most of the bytes on a lecture offering five hundred tracks.
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
        """The last manifest stored for this lecture, or None if there is none."""
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
            # A cache entry that will not read is not worth an error of its
            # own. The transport failure that sent us here is the real news
            # and is about to be raised by the caller.
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
        """Store which videos this playlist held, so an outage cannot hide it.

        Keyed on the playlist id rather than the URL as typed, for the same
        reason the manifest cache keys on the video id: several spellings
        name one playlist. A URL that carries no id is simply not cached.
        """
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
            # Same reasoning as `_recall`: an unreadable cache entry is not
            # worth an error of its own while the transport failure that sent
            # us here is about to be raised by the caller.
            return None

        if (
            not videos
            or not isinstance(videos, list)
            or not all(isinstance(video, str) for video in videos)
        ):
            # An empty roster is never written (`PlaylistEmpty` fires first),
            # so an empty or misshapen one read back is a corrupt entry — and
            # serving it would make the playlist quietly vanish from the run.
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
            for raw_language, entries in captions.items():
                tier = _tier_for(raw_language, spoken_language)
                for entry in entries or ():
                    # An entry that is not an object cannot be read, and
                    # `_explain_empty_manifest` will say so if skipping them
                    # leaves nothing. Guarded here rather than trusted,
                    # because the alternative is an `AttributeError` from
                    # inside a generator with no mention of yt-dlp in it.
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
    """The installed transport version, named in the error that blames it.

    Read from the distribution metadata rather than by importing ``yt_dlp``:
    this is only ever called while reporting a failure, and pulling in a heavy
    package at that point would be a poor moment to discover it cannot be
    imported. It also reports the version `pip` would upgrade *from*, which is
    the number the remedy is about.
    """
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return "(not installed)"


def _contract_changed(source: str, detail: str) -> TransportContractChanged:
    return TransportContractChanged(
        source=source, detail=detail, version=_yt_dlp_version()
    )


def _require_shape(info: Any, source: str) -> Info:
    """Check the extractor returned something the tool can still read.

    This is the job the ``<2027`` ceiling in ``pyproject.toml`` used to stand
    in for, moved to where it can actually do it. A version bound is a proxy
    for "did the shape change?", and a poor one in both directions: it fires
    on a calendar boundary when nothing has changed, stays silent when the
    shape moves within the range it allows, and only ever fires at *install*
    time — which does nothing for a virtualenv that already exists. Checking
    the shape itself fires exactly when the shape moved, on the machine with
    the problem, and can name what moved.

    The distinction drawn here is absent versus empty, and it is the whole
    point. A lecture with no captions is an ordinary fact about a video and
    belongs to `NoCaptionsAvailable`. Caption keys that are not there *at all*
    cannot be a fact about the video, because yt-dlp emits them for every
    video whether or not they hold anything — so their absence is a fact about
    the tool, and saying otherwise sends the reader to check a video that is
    fine. That is the same mistake `PlaylistNotSupported` exists to prevent.

    So: membership tests, never truthiness. ``info.get(key) or {}`` is the
    idiom that erased the difference in the first place.
    """
    if info is None:
        raise _contract_changed(source, "the extractor returned nothing at all")

    if not isinstance(info, dict):
        raise _contract_changed(
            source, f"the metadata came back as {type(info).__name__}, not an object"
        )

    # The value, not just the key. `{"id": ""}` passed a membership test and
    # then reopened the overwrite finding: `naming` falls back to the bare
    # title when there is no id to distinguish it with, so a lecture with an
    # empty id is named by a title its uploader chose. `None` and a bare number
    # got further still and raised `TypeError` out of `sanitise`.
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

    `_require_shape`'s discipline, for the playlist form: membership tests,
    never truthiness. ``entries`` *absent* is yt-dlp changing shape and must
    not read as an empty playlist; present and empty is a fact about the
    playlist itself — judged by the caller, not here, because emptiness and
    size are not contract questions and their errors must not fall into the
    cache fallback that contract failures share.

    Reads at most one entry past `MAX_PLAYLIST_ITEMS`: the flat client asks
    the far end to stop there too, but ``playlistend`` is a request, and this
    cap is ours. One entry past the ceiling is enough to tell "exactly at the
    limit" from "more coming" — the same trick `_read_capped` uses for bytes.

    A deleted or private video needs no case of its own: the flat extraction
    still names it with a real id, it expands like any other, and the fetch
    that later fails for it is reported per lecture, which is where that
    failure belongs.
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

    `_require_shape` proves the caption keys exist; a manifest can still come
    out empty afterwards, and the two reasons are not the same failure. No
    tracks listed at all is a fact about the lecture. Tracks listed but none
    survived `_tracks_from` is a fact about the tool — every entry was dropped
    for having no recognised ``ext`` or no ``url``, which is what a renamed
    field looks like from here. Reporting the second as `NoCaptionsAvailable`
    would be the original lie in a new place.

    The detail names the formats that were actually offered, so the message
    still explains itself if the cause ever turns out to be an ordinary gap in
    what the tool parses rather than a contract that moved.
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


def _tier_for(raw_language: str, spoken_language: str | None) -> TrustTier:
    """Classify one caption track.

    `spoken_language` is None for human-written tracks, which are always
    `MANUAL`. For automatic tracks it is the video's declared language, and
    anything else is a machine translation of a machine transcription.
    """
    if spoken_language is None:
        return TrustTier.MANUAL

    language = raw_language.lower()
    if language.endswith(_ORIGINAL_SUFFIX):
        # yt-dlp's own marker for the track everything else was translated
        # *from*, so it is the original transcription by definition. Taking
        # the marker at its word rather than re-deriving the same conclusion
        # from the video's declared language matters when there is no declared
        # language: the comparison below then matches nothing, and the one
        # genuine ASR track on the video is filed as a machine translation of
        # itself — the least trustworthy tier there is, for the most
        # trustworthy automatic track on offer.
        return TrustTier.ASR_PLATFORM

    if primary_subtag(language) == primary_subtag(spoken_language):
        return TrustTier.ASR_PLATFORM
    return TrustTier.TRANSLATED


def _meta_from(info: Info) -> LectureMeta:
    """Citation metadata, from a dict nobody here controls.

    Every field is optional and every coercion forgiving, on purpose. These
    are the parts of a lecture that make it citable, not the parts that make
    it readable: a lecture whose chapter list has gone strange is still a
    lecture, and failing the retrieval over one would trade the whole
    transcript for a heading. What must *not* be tolerated quietly is the
    caption tracks going missing, and that is `_require_shape`'s job.
    """
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
    """Published chapters, skipping anything that does not read as one.

    A chapter with no usable start cannot be placed, and `refine.sections`
    would have nothing to do with it. yt-dlp does emit ``start_time: None`` —
    which is why this reads through `_optional_float` rather than calling
    `float` on a `.get` default, the version that raised `TypeError` from
    inside a generator expression several frames from anything explanatory.
    """
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

    The length and type tests are not enough on their own, which is why the
    constructor is inside the `try`: ``"00000000"`` is eight characters and
    every one a digit, and `date` refuses month zero. `str.isdigit` was the
    previous guard and is true for superscripts that `int` then rejects. A
    lecture whose date will not parse is still worth having, so this reports
    no date rather than no lecture.
    """
    stamp = info.get("upload_date") or info.get("release_date")
    if not isinstance(stamp, str) or len(stamp) != 8:
        return None

    try:
        return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
    except ValueError:
        return None


def _optional_float(value: Any) -> float | None:
    """A number, or None if it is missing or does not read as one.

    Finite or nothing: JSON's parser accepts ``Infinity`` and ``NaN``
    literals, and a non-finite duration or chapter start survives every stage
    until it crashes `format_timestamp` inside a renderer — the exact failure
    `parse.base.require_finite` refuses on the caption path. Metadata is the
    forgiving path, so the answer here is None rather than an error.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _split(source: str) -> SplitResult:
    """Parse a URL, tolerating a missing scheme.

    `urlsplit` puts a scheme-less ``youtube.com/watch?v=X`` entirely in `path`
    and leaves `netloc` empty, so a hostname check would never fire — and that
    is a common form to paste.
    """
    return urlsplit(source if "//" in source else f"https://{source}")


def _is_youtube_url(source: str) -> bool:
    """Whether `source` is a URL whose *host* is YouTube's.

    Matching the hostname rather than searching the whole string. A substring
    test says yes to ``https://elsewhere.invalid/youtube.com/x`` and hands it
    to the extractor, which is a strange thing for a lecture tool to do with
    an address nobody at YouTube controls.
    """
    try:
        host = _split(source).hostname or ""
    except ValueError:
        # Not parseable, so not a YouTube URL. Something else may claim it,
        # or the caller is told that nothing recognised it.
        return False

    return any(host == known or host.endswith(f".{known}") for known in _HOSTS)


def _is_video_id(segment: str) -> bool:
    """Whether one path segment is a video ID."""
    return segment != _PLAYLIST_EMBED and _VIDEO_ID.fullmatch(segment) is not None


def _video_id_in_path(parts: SplitResult) -> str | None:
    """The video ID this address carries in its path, if it carries one.

    ``youtu.be/<id>`` does, and so do the ``/shorts/``, ``/live/``, ``/embed/``
    and ``/v/`` forms. Knowing that is what stops `_is_collection` condemning
    them: none of these has a ``v=`` parameter to find, so "carries ``list``
    and no ``v``" — a fair test for a ``/watch`` URL — reads every one of them
    as a playlist. ``youtu.be/<id>?list=PL...`` is precisely what the share
    button produces for a video sitting in a playlist, so that mistake refuses
    one of the most common things anyone pastes.

    Returns the ID rather than a yes-or-no because the manifest cache needs
    the same answer, and two functions walking the same URL forms would be two
    places for the ``videoseries`` exclusion to be forgotten.
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
    """Which lecture this source names, however it was addressed.

    The manifest cache keys on this rather than on the string as typed, so
    that ``youtu.be/X``, ``watch?v=X`` and a bare ``X`` are one cache entry
    for one lecture instead of three for the same one.
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
    """Whether this URL names a playlist or channel rather than one lecture.

    Deliberately narrow. ``watch?v=ID&list=PL...`` is what YouTube's own share
    button produces for a video that happens to sit in a playlist, and it is a
    single lecture — the presence of ``list`` must not condemn it. Only an
    address with no video in it at all is a collection, and a video may be
    named either by a ``v=`` parameter or by the path.
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
    """Whether this collection is a playlist — the kind `expand` can unroll.

    Built on `_is_collection` rather than beside it, so the hard-won part —
    ``watch?v=ID&list=PL...`` and its share-button relatives are single
    lectures, not collections — is decided in exactly one place. What is left
    to tell apart here is only which *kind* of collection: a channel or
    search path has no bounded list of videos to become, and stays refused.
    """
    return _is_collection(source) and not _split(source).path.startswith(_CHANNEL_PATHS)


def _playlist_id(source: str) -> str | None:
    """Which playlist this source names, for the expansion cache.

    None when the URL carries no ``list`` parameter — a bare ``/playlist``
    path, say. Expansion still runs; it simply cannot be cached, the same
    answer `_video_id` gives `_recall` for an unrecognisable address.
    """
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
        # `cast` rather than a typed local, because a local would put the
        # `return` outside the `try` — one statement further than the offline
        # suite can reach, since these two functions are exactly the seam the
        # tests replace and only their failure path is exercised here. A line
        # the coverage gate cannot cover is a worse trade than saying plainly
        # that this value is unchecked; `_require_shape` is what checks it.
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

    Read in bounded chunks rather than in one `.read()`. The old call sized its
    allocation from whatever arrived, which let the far end choose how much
    memory this process used — and a process killed for using too much takes
    every already-fetched lecture in the batch with it, which is the one
    failure `cli.run`'s per-source catch cannot help with.
    """
    ydl = _youtube_dl()
    try:
        response = ydl.urlopen(url)
    except Exception as error:
        raise _classify(url, error) from error

    # Outside the `try`: `PayloadTooLarge` is a fact about the payload, and
    # `_classify` would file it as a transport failure and send the reader off
    # to check their network. Closed either way — a refused payload must not
    # also leak the connection that was carrying it. Guarded, because yt-dlp's
    # response objects are not all the same shape.
    try:
        return _read_capped(response, source)
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()


#: Read size. Small enough that the cap is never overshot by much, large
#: enough that an ordinary caption file takes a handful of reads.
_CHUNK_BYTES = 64 * 1024


def _read_capped(response: Any, source: str) -> str:
    """Read at most `MAX_PAYLOAD_BYTES`, refusing anything past it.

    ``Content-Length`` is used only to refuse *early*, never to decide when to
    stop. It is a claim by the far end, and a broken or hostile one can omit
    it, understate it, or lie outright — so the streaming cap is the real
    defence and the header only saves a pointless download.
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
        # Asking for one byte past the limit is what tells "exactly at the
        # limit" apart from "there is more coming".
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
        # Unlike a cache entry, a wire payload is not disposable — retrying
        # will not read it differently, so it gets a typed refusal rather
        # than escaping as an unclassified "retry" failure.
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
    """A configured `YoutubeDL`, typed as `Any` because yt-dlp ships no types.

    Naming it honestly here keeps the vagueness at the one seam that has it,
    rather than letting an unchecked value spread outwards under a signature
    that looks precise.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:  # pragma: no cover - depends on install extras
        raise AcquisitionFailed(
            source="youtube",
            detail=(
                "yt-dlp is not installed. "
                "Install it with: pip install 'youtube-transcript-notes[youtube]'"
            ),
        ) from error

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noprogress": True,
        # `_is_collection` already ruled that a URL naming a video is one
        # video even when it also carries `list=` — the share-button form.
        # yt-dlp's default reads the same URL as the whole playlist, which is
        # a slow full extraction whose result has no caption keys and lands as
        # a moved contract. Inert on the flat path: `_is_playlist` only ever
        # hands it collection URLs with no video in them.
        "noplaylist": True,
        "socket_timeout": _SOCKET_TIMEOUT,
        "retries": _RETRIES,
        "extractor_retries": _RETRIES,
    }
    if flat:
        # `in_playlist` names every video in one request without visiting any
        # of them; without it, enumerating a playlist costs one full
        # extraction per video. `playlistend` asks the far end to stop one
        # entry past the ceiling — a request rather than a guarantee, which
        # is why `_require_playlist_ids` caps its own read as well. The dict
        # is built per call, so the single-video options cannot pick these up.
        options["extract_flat"] = "in_playlist"
        options["playlistend"] = MAX_PLAYLIST_ITEMS + 1

    return YoutubeDL(options)


def _classify(source: str, error: Exception) -> SourceError:
    """Map a transport failure onto the taxonomy, or admit we do not know.

    Transport-broken patterns are tested first. A yt-dlp that cannot read
    YouTube's player says a great many things, and some of them read exactly
    like facts about the video — which is how a compatibility problem ends up
    reported as a deleted lecture, sending the reader to check a video that
    was never the problem.
    """
    # Redacted here rather than at each call site, because this is the one
    # place every transport failure passes through — and it has to cover both
    # halves. The tool's own `source` is one; the third-party message is the
    # other, since yt-dlp quotes the URL it failed on ("HTTP Error 403:
    # Forbidden for url: ...&sig=..."). Matching on the message happens before
    # redaction, on the original, so nothing about the classification changes.
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

    return AcquisitionFailed(source=source, detail=message)
