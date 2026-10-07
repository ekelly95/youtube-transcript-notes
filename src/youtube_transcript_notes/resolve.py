"""Discovery: what tracks exist, and choosing between them.

`list()` on a provider returns a `TrackManifest` — everything needed to cite
the source, without downloading a caption. Only `TrackHandle.fetch()` does
work.

Choosing is a priority resolution over language, then trust tier, then
caption format: a transcript in the wrong language is useless however good,
tier decides how far the words can be trusted, and format only affects timing
detail.
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

#: Preferred caption formats, best first. json3 carries word timings and marks
#: scrolling structurally, so it needs no deduplication.
FORMAT_PREFERENCE = ("json3", "vtt", "webvtt", "srt", "subrip")

#: A BCP-47-ish tag, capturing the primary subtag for `LANGUAGE_CODES`.
_LANGUAGE_TAG = re.compile(r"([A-Za-z]{2,3})(?:-[A-Za-z0-9]+)*")

#: Language subtags the tool will read out of a filename.
#:
#: ISO 639-1 entire, plus `iw` (YouTube's legacy Hebrew). The three-letter half
#: is a short list of what caption tooling writes, not ISO 639-2: the full
#: standard would admit `bak`, `new`, `raw`, `sub`, `mix`, `cut` and `tmp`, all
#: common filename words. A code left out costs a track its label (it becomes
#: `und`, which matches everything); a code wrongly let in costs a
#: `TrackNotFound`. So when in doubt, leave it out.
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

    MIT OpenCourseWare labels human-written tracks like ``en-j3PyPqV-e1s``, so
    exact-tag matching would miss plainly English tracks.
    """
    return code.split("-")[0].lower()


def looks_like_language(text: str) -> bool:
    """Whether a filename part names a language.

    Shape and table: shape alone matches `raw`, `tmp`, `bak` and friends. The
    table applies only to the primary subtag, so `en-j3PyPqV-e1s` still matches.
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
    """Exactly how the source labelled it — see `primary_subtag`."""

    label: str | None = None
    """The source's human-readable name, e.g. ``English``."""

    def __post_init__(self) -> None:
        if not self.raw_language:
            object.__setattr__(self, "raw_language", self.language)

    def matches(self, requested: str) -> bool:
        """Whether this track satisfies a requested language.

        Accepts an exact tag, a bare language, or a prefix, so ``en`` finds
        ``en-GB``. A track of undeclared language matches anything; provenance
        still records that the language is unknown.
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
        """One line for an availability listing."""
        name = f' "{self.label}"' if self.label else ""
        return f"{self.raw_language}{name} — {self.tier.value}, {self.caption_format}"

    def to_dict(self) -> dict[str, Any]:
        """Exact inverse of `from_dict`; used by the manifest cache."""
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
    """A track, plus the means to fetch it. Holding one costs nothing."""

    track: Track
    meta: LectureMeta
    provider: SourceProvider
    ref: Any
    """Whatever the provider needs to retrieve this track. Opaque elsewhere."""

    def fetch(
        self, policy: ReflowPolicy | None = None, glossary: Glossary | None = None
    ) -> Lecture:
        """Download, parse, reassemble and stamp with provenance.

        The same pipeline for every provider; only `load` differs.
        """
        payload = self.provider.load(self.ref)
        cues = parse_captions(
            payload, self.track.caption_format, source=self.meta.source_id
        )
        passages = reflow(
            cues,
            policy or policy_for(self.track.tier, self.track.caption_format, cues),
        )
        # Measured after reflow, so an all-`[MUSIC]` track is refused too rather
        # than written out as a note with a title and nothing else.
        if not passages:
            raise EmptyTranscript(
                source=self.meta.source_id, fmt=self.track.caption_format
            )

        # The title and chapter headings spell exactly the words a recogniser
        # gets wrong. A caller's glossary wins where they disagree.
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
    """Why this manifest came from the cache rather than the source.

    Set when discovery could not reach the source and a stored manifest was
    used, so the reader can be told the transport needs fixing.
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

        `languages` is a preference list: the first with any usable track wins
        outright, so ``["de", "en"]`` never returns English when German exists
        at any tier. `tiers` restricts and reorders trust tiers; the default is
        every tier, most trustworthy first.
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
        """Track descriptions, truncated but never silently.

        A YouTube video can offer hundreds of auto-translated tracks. Providers
        list human-written tracks first, so truncation keeps the likeliest ones.
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
