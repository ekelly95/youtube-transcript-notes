"""Discovery: what tracks exist, and choosing between them.

This is the first half of the two-stage design. `list()` on a provider returns
a `TrackManifest` describing what could be fetched — including everything
needed to cite the lecture — without downloading a single caption. Only
`TrackHandle.fetch()` does work.

Choosing between tracks is a priority resolution over three axes, in this
order: the caller's language preference, then trust tier, then caption format.
Language leads because a transcript in the wrong language is useless however
well produced; tier comes next because it decides how much the words can be
trusted; format last because it only affects how much timing detail survives.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .errors import EmptyTranscript, TrackNotFound, TranscriptError
from .models import (
    Lecture,
    LectureMeta,
    Provenance,
    TrustTier,
    content_hash,
    sort_by_tier,
)
from .parse import parse_captions
from .refine import (
    Glossary,
    ReflowPolicy,
    build_sections,
    policy_for,
    propose_corrections,
    reflow,
    terms_from,
)

if TYPE_CHECKING:  # pragma: no cover
    from .sources.base import SourceProvider

__all__ = [
    "UNKNOWN_LANGUAGE",
    "Track",
    "TrackHandle",
    "TrackManifest",
    "primary_subtag",
]

#: ISO 639-2 for "undetermined" — a track whose language the source never said.
UNKNOWN_LANGUAGE = "und"

#: How many tracks an availability listing shows before summarising the rest.
MAX_LISTED_TRACKS = 12

#: Preferred caption formats, best first. json3 wins because it carries
#: word-level timings and marks caption scrolling structurally, which means it
#: needs no deduplication — see `refine.dedupe`.
FORMAT_PREFERENCE = ("json3", "vtt", "webvtt", "srt", "subrip")

#: The shape of a BCP-47-ish tag, capturing the primary subtag so it can be
#: checked against `LANGUAGE_CODES`. Shape alone is not enough — see below.
_LANGUAGE_TAG = re.compile(r"([A-Za-z]{2,3})(?:-[A-Za-z0-9]+)*")

#: Language subtags the tool will read out of a filename.
#:
#: The two-letter half is ISO 639-1 entire, plus `iw` — YouTube's legacy code
#: for Hebrew, which arrives in filenames straight from yt-dlp. The
#: three-letter half is deliberately *not* ISO 639-2 or 639-3: it is a short
#: list of what caption tooling actually writes. Copying a standard here would
#: re-admit `bak` (Bashkir), `new` (Newari), `raw` (Rawang), `sub` (Suku),
#: `mix` (Mixtec), `cut` (Cuicatec) and `tmp` (Tai Mène) — which is the exact
#: failure this table exists to stop, since every one of them is something
#: people put in a filename. `fin`, `dan`, `nor` and `ind` are left out for the
#: same reason: "final" is far likelier than Finnish, and `fi` still works.
#:
#: The asymmetry that decides the whole design: **a code left out costs a track
#: its label, not its usability** — it becomes `und`, and `und` matches every
#: request. A code wrongly let in costs a `TrackNotFound` on a file that was
#: labelled correctly. So when in doubt, leave it out.
LANGUAGE_CODES = frozenset(
    """
    aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch
    co cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga
    gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu iw
    ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu
    lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om
    or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq
    sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve
    vi vo wa wo xh yi yo za zh zu
    ara ceb ces deu ell eng fil fra haw heb hin hun ita jpn kor nld pol por ron
    rus spa swe tha tur ukr vie yue zho und
    """.split()
)


def primary_subtag(code: str) -> str:
    """The bare language part of a tag: ``en-GB`` and ``en-j3PyPqV-e1s`` to ``en``.

    That second example is not hypothetical. MIT OpenCourseWare's human-written
    tracks are labelled with codes like ``en-j3PyPqV-e1s``, where everything
    after the language is an internal track identifier. Matching only on exact
    tags would fail to find an English transcript on a lecture that plainly has
    one.
    """
    return code.split("-")[0].lower()


def looks_like_language(text: str) -> bool:
    """Whether a filename part names a language.

    Shape *and* table. Shape alone is not distinctive enough to be useful:
    ``[A-Za-z]{2,3}`` matches `raw`, `hd`, `new`, `old`, `tmp`, `bak`, `cut`,
    `fix`, `mix`, `ocr`, `sub` and `cc`, so `lecture.raw.vtt` was read as a
    lecture in the `raw` language and then found to have no English track.

    The table applies only to the *primary* subtag, so `en-j3PyPqV-e1s` still
    matches — MIT OpenCourseWare labels real tracks that way, and everything
    after the language is somebody else's identifier.
    """
    match = _LANGUAGE_TAG.fullmatch(text)
    return match is not None and match.group(1).lower() in LANGUAGE_CODES


@dataclass(frozen=True)
class Track:
    """A caption track that exists, described without being downloaded."""

    language: str
    """The bare language subtag, for matching."""

    tier: TrustTier
    caption_format: str

    raw_language: str = ""
    """Exactly how the source labelled it, which may carry more than a
    language — see `primary_subtag`."""

    label: str | None = None
    """The source's human-readable name, e.g. ``English``."""

    def __post_init__(self) -> None:
        if not self.raw_language:
            object.__setattr__(self, "raw_language", self.language)

    def matches(self, requested: str) -> bool:
        """Whether this track satisfies a requested language.

        Accepts an exact tag, a bare language, or a prefix — so ``en`` finds
        ``en-GB``, and ``zh-Hant`` can still be asked for precisely.

        A track of undeclared language matches anything. Refusing to return
        the only transcript available because nobody labelled it would be
        pedantry; the provenance still records that the language is unknown.
        """
        if self.language == UNKNOWN_LANGUAGE:
            return True

        wanted = requested.lower()
        raw = self.raw_language.lower()
        return (
            wanted == raw
            or wanted == self.language.lower()
            or raw.startswith(f"{wanted}-")
        )

    def describe(self) -> str:
        """One line for the availability listing shown when nothing matches."""
        name = f' "{self.label}"' if self.label else ""
        return f"{self.raw_language}{name} — {self.tier.value}, {self.caption_format}"

    def to_dict(self) -> dict[str, Any]:
        """Exact inverse of `from_dict`, like everything in `models`.

        Tracks are stored so that a manifest survives the transport being
        unreachable — see `sources.youtube`. Nothing here is derived, so a
        round trip cannot quietly change which track the resolver picks.
        """
        return {
            "language": self.language,
            "raw_language": self.raw_language,
            "tier": self.tier.value,
            "caption_format": self.caption_format,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Track:
        return cls(
            language=data["language"],
            raw_language=data["raw_language"],
            tier=TrustTier(data["tier"]),
            caption_format=data["caption_format"],
            label=data["label"],
        )


@dataclass(frozen=True)
class TrackHandle:
    """A track, plus the means to fetch it.

    Holding a handle costs nothing; `fetch` is where the work happens.
    """

    track: Track
    meta: LectureMeta
    provider: SourceProvider
    ref: Any
    """Whatever the provider needs to retrieve this track — a path, a URL.
    Opaque to everything else."""

    def fetch(
        self, policy: ReflowPolicy | None = None, glossary: Glossary | None = None
    ) -> Lecture:
        """Download, parse, reassemble and stamp with provenance.

        The pipeline is identical for every provider; only `load` differs. A
        provider that fetched *and* parsed would be two responsibilities and
        two places for the format handling to drift.
        """
        payload = self.provider.load(self.ref)
        cues = parse_captions(
            payload, self.track.caption_format, source=self.meta.source_id
        )
        passages = reflow(
            cues,
            policy or policy_for(self.track.tier, self.track.caption_format, cues),
        )
        # Measured after reflow rather than on the cues, so a track that is all
        # `[MUSIC]` is caught alongside one that parsed to nothing — both end
        # as a note holding a title and no lecture. Refusing costs this one
        # track and leaves the rest of a batch alone; returning it wrote the
        # empty note to disk and called the run a success.
        if not passages:
            raise EmptyTranscript(
                source=self.meta.source_id, fmt=self.track.caption_format
            )

        # The lecture's own title and chapter headings are a spelling authority
        # for exactly the words a recogniser gets wrong, and they cost nothing
        # — they are already here. A caller's glossary wins where the two
        # disagree, because it was written on purpose.
        known = terms_from(self.meta)
        if glossary is not None:
            known = glossary.merged_with(known)

        return Lecture(
            meta=self.meta,
            sections=build_sections(passages, self.meta.chapters),
            corrections=propose_corrections(passages, known),
            provenance=Provenance(
                provider=self.provider.name,
                tier=self.track.tier,
                language=self.track.raw_language,
                caption_format=self.track.caption_format,
                retrieved_at=self.provider.now(),
                content_hash=content_hash(payload),
                source_url=self.meta.url,
            ),
        )


@dataclass(frozen=True)
class TrackManifest:
    """What a source has to offer, discovered without downloading captions."""

    meta: LectureMeta
    tracks: tuple[TrackHandle, ...]

    stale_reason: TranscriptError | None = None
    """Why this manifest came from a cache rather than from the source.

    None on every ordinary run. Set when discovery could not reach the source
    and a previously stored manifest was used instead, so that already-fetched
    lectures keep working through an outage. Carried rather than swallowed
    because a run that quietly served last week's answer looks exactly like a
    run that succeeded, and the reader would have no way to tell that the
    transport needs fixing.
    """

    def __iter__(self) -> Iterator[TrackHandle]:
        return iter(self.tracks)

    def __len__(self) -> int:
        return len(self.tracks)

    def languages(self) -> tuple[str, ...]:
        """Every distinct language on offer, in the order first seen."""
        return tuple(dict.fromkeys(handle.track.language for handle in self.tracks))

    def find(
        self,
        languages: Sequence[str] = ("en",),
        tiers: Sequence[TrustTier] | None = None,
    ) -> TrackHandle:
        """Pick the best available track.

        `languages` is a preference list: the first one with any usable track
        wins outright, so asking for ``["de", "en"]`` never returns English
        when a German track exists at any tier.

        `tiers` restricts and reorders trust tiers; the default is every tier,
        most trustworthy first.
        """
        allowed = tuple(tiers) if tiers is not None else _DEFAULT_TIERS

        for language in languages:
            candidates = [h for h in self.tracks if h.track.matches(language)]
            for tier in allowed:
                matching = [h for h in candidates if h.track.tier is tier]
                if matching:
                    return min(matching, key=_format_rank)

        raise TrackNotFound(
            source=self.meta.source_id,
            languages=list(languages),
            tiers=[tier.value for tier in allowed],
            available=self.describe_tracks(),
        )

    def describe_tracks(self, limit: int = MAX_LISTED_TRACKS) -> list[str]:
        """Track descriptions for an error message, truncated but never silently.

        A YouTube lecture typically offers around five hundred tracks once
        auto-translations are counted, and printing all of them helps nobody.
        Providers list human-written tracks first, so the truncation keeps the
        ones a reader is most likely to want, and says how many it dropped.
        """
        lines = [handle.track.describe() for handle in self.tracks]
        if len(lines) <= limit:
            return lines
        return [*lines[:limit], f"... and {len(lines) - limit} more"]


_DEFAULT_TIERS = sort_by_tier(tuple(TrustTier))


def _format_rank(handle: TrackHandle) -> int:
    fmt = handle.track.caption_format
    return (
        FORMAT_PREFERENCE.index(fmt)
        if fmt in FORMAT_PREFERENCE
        else len(FORMAT_PREFERENCE)
    )
