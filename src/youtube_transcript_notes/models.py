"""The core data model.

Every type is frozen and every container a tuple, so "renderers are pure
functions of a Lecture" is enforced by the language. `Lecture.to_dict()` and
`Lecture.from_dict()` are exact inverses, written out by hand so the
round-trip test means something.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from .errors import MalformedLecture

__all__ = [
    "MONTH_NAMES",
    "SCHEMA_VERSION",
    "Chapter",
    "Correction",
    "Cue",
    "Lecture",
    "LectureMeta",
    "Locator",
    "Passage",
    "Provenance",
    "Section",
    "TrustTier",
    "Word",
    "content_hash",
    "format_date",
    "format_timestamp",
    "sort_by_tier",
]

SCHEMA_VERSION = 1


def content_hash(payload: str | bytes) -> str:
    """Stable SHA-256 of a caption payload, recorded in `Provenance`."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def format_date(value: date | None) -> str:
    """Locale-independent long date: ``12 September 2011``, or ``n.d.``.

    Not ``strftime("%B")``, whose output depends on the machine's locale.
    """
    if value is None:
        return "n.d."
    return f"{value.day} {MONTH_NAMES[value.month - 1]} {value.year}"


def format_timestamp(seconds: float) -> str:
    """Seconds to a human timestamp: ``5:07`` under an hour, ``1:05:07`` over."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class TrustTier(StrEnum):
    """How far the text can be trusted.

    Drives track priority (`rank`), the prose shown in bylines (`prose`), and
    whether rolling-window deduplication applies (see `refine.policy_for`).
    """

    MANUAL = "manual"
    """Human-written captions. Usually accurate on technical vocabulary, which
    is exactly where speech recognition fails hardest."""

    UNMARKED = "unmarked"
    """A local caption file whose name carries no tier marker. Its origin is
    unstated — yt-dlp writes automatic captions under exactly such a name — so
    it is neither deduplicated nor described as human-written."""

    ASR_PLATFORM = "asr_platform"
    """The platform's automatic captions. Recent ones are punctuated and cased;
    older tracks are not, and vtt/srt forms repeat text in a rolling window."""

    ASR_LOCAL = "asr_local"
    """Transcribed outside the tool — a caption file marked ``.whisper.`` or
    ``.transcribed.``. Quality depends on that tool; timings are clean."""

    TRANSLATED = "translated"
    """Machine-translated from another track. A derived artefact: use it for
    gist, and prefer it last."""

    @property
    def rank(self) -> int:
        """Default resolution priority; lower wins. Callers may reorder."""
        return _TIER_RANK[self]

    @property
    def prose(self) -> str:
        """How this tier reads in a rendered document, without jargon."""
        return _TIER_PROSE[self]

    @property
    def assume_punctuated(self) -> bool:
        """The fallback guess at sentence punctuation, for a track too short to
        measure. `refine.policy_for` counts sentence endings whenever it can."""
        return self is not TrustTier.ASR_PLATFORM


_TIER_RANK = {
    TrustTier.MANUAL: 0,
    TrustTier.UNMARKED: 1,
    TrustTier.ASR_PLATFORM: 2,
    TrustTier.ASR_LOCAL: 3,
    TrustTier.TRANSLATED: 4,
}

_TIER_PROSE = {
    TrustTier.MANUAL: "human-written captions",
    TrustTier.UNMARKED: "captions of unstated origin",
    TrustTier.ASR_PLATFORM: "platform auto-generated captions",
    TrustTier.ASR_LOCAL: "locally transcribed audio",
    TrustTier.TRANSLATED: "machine-translated captions",
}


@dataclass(frozen=True)
class Word:
    """A single word with its own start time, when the format carries one."""

    text: str
    start: float

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start": self.start}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Word:
        return cls(text=data["text"], start=data["start"])


@dataclass(frozen=True)
class Cue:
    """One caption cue, exactly as the source published it.

    Transient: parsers emit cues and reflow consumes them. A finished `Lecture`
    holds `Passage` objects.
    """

    text: str
    start: float
    duration: float
    words: tuple[Word, ...] = ()
    speaker: str | None = None
    turn: bool = False
    """Whether a new speaker starts here.

    Separate from `speaker` because a ``NAME:`` label says who is talking,
    while a bare ``>>`` says only that someone else is."""

    @property
    def end(self) -> float:
        return self.start + self.duration

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "start": self.start,
            "duration": self.duration,
            "words": [word.to_dict() for word in self.words],
            "speaker": self.speaker,
            "turn": self.turn,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Cue:
        return cls(
            text=data["text"],
            start=data["start"],
            duration=data["duration"],
            words=tuple(Word.from_dict(word) for word in data.get("words", ())),
            speaker=data.get("speaker"),
            turn=data.get("turn", False),
        )


@dataclass(frozen=True)
class Passage:
    """A readable paragraph of text.

    `start` is the start of the first cue that fed it, so a quote from the
    middle of a document still points at the moment it was said.
    """

    text: str
    start: float
    end: float
    speaker: str | None = None
    """Who is speaking, when the captions said so. Never guessed."""

    turn: bool = False
    """Whether this passage begins a new speaker's turn — see `Cue.turn`."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "speaker": self.speaker,
            "turn": self.turn,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Passage:
        return cls(
            text=data["text"],
            start=data["start"],
            end=data["end"],
            speaker=data.get("speaker"),
            turn=data.get("turn", False),
        )


@dataclass(frozen=True)
class Section:
    """A run of passages under one heading; `title` is None without chapters."""

    title: str | None
    start: float
    passages: tuple[Passage, ...]

    @property
    def end(self) -> float:
        return self.passages[-1].end if self.passages else self.start

    @property
    def text(self) -> str:
        return "\n\n".join(passage.text for passage in self.passages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start": self.start,
            "passages": [passage.to_dict() for passage in self.passages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Section:
        return cls(
            title=data["title"],
            start=data["start"],
            passages=tuple(Passage.from_dict(p) for p in data["passages"]),
        )


@dataclass(frozen=True)
class Correction:
    """A phrase the transcript probably got wrong, and what it should say.

    Never applied to `Passage.text`. A corrected transcript and a hallucinated
    one look identical; one that carries its corrections beside the original
    words can be checked. `evidence` says where the right spelling came from.
    """

    wrong: str
    right: str
    at: float | None = None
    distance: int | None = None
    """Edits between `wrong` and `right` for a near-miss the tool spotted;
    None when the wrong form was named outright, by a glossary or a
    corrections file. A measurement, not a probability: nothing here knows how
    likely a correction is to be right."""
    evidence: str = ""
    occurrences: int = 1

    def again(self) -> Correction:
        """The same correction, having now been seen once more."""
        return replace(self, occurrences=self.occurrences + 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wrong": self.wrong,
            "right": self.right,
            "at": self.at,
            "distance": self.distance,
            "evidence": self.evidence,
            "occurrences": self.occurrences,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Correction:
        return cls(
            wrong=data["wrong"],
            right=data["right"],
            at=data.get("at"),
            # A pre-0.5 line carries an invented "confidence"; it is ignored.
            distance=data.get("distance"),
            evidence=data.get("evidence", ""),
            occurrences=data.get("occurrences", 1),
        )


@dataclass(frozen=True)
class Chapter:
    """A chapter marker as published, before passages are fitted to it.

    Distinct from `Section` so a bad chapter list cannot destroy text.
    """

    title: str
    start: float
    end: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chapter:
        return cls(title=data["title"], start=data["start"], end=data.get("end"))


@dataclass(frozen=True)
class LectureMeta:
    """Everything needed to cite the source."""

    source_id: str
    title: str
    url: str | None = None
    channel: str | None = None
    published: date | None = None
    duration: float | None = None
    chapters: tuple[Chapter, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "channel": self.channel,
            "published": self.published.isoformat() if self.published else None,
            "duration": self.duration,
            "chapters": [chapter.to_dict() for chapter in self.chapters],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LectureMeta:
        published = data.get("published")
        return cls(
            source_id=data["source_id"],
            title=data["title"],
            url=data.get("url"),
            channel=data.get("channel"),
            published=date.fromisoformat(published) if published else None,
            duration=data.get("duration"),
            chapters=tuple(Chapter.from_dict(c) for c in data.get("chapters", ())),
        )


@dataclass(frozen=True)
class Provenance:
    """Where the text came from: which track, when, and a hash of what was parsed."""

    provider: str
    tier: TrustTier
    language: str
    caption_format: str
    retrieved_at: datetime
    content_hash: str
    source_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "tier": self.tier.value,
            "language": self.language,
            "caption_format": self.caption_format,
            "retrieved_at": self.retrieved_at.isoformat(),
            "content_hash": self.content_hash,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        return cls(
            provider=data["provider"],
            tier=TrustTier(data["tier"]),
            language=data["language"],
            caption_format=data["caption_format"],
            retrieved_at=datetime.fromisoformat(data["retrieved_at"]),
            content_hash=data["content_hash"],
            source_url=data.get("source_url"),
        )


@dataclass(frozen=True)
class Locator:
    """A citable position: the one place a time becomes a timestamp or deep link."""

    source_id: str
    start: float
    end: float
    section: str | None = None
    base_url: str | None = None

    @property
    def timestamp(self) -> str:
        return format_timestamp(self.start)

    @property
    def url(self) -> str | None:
        """Deep link into the source at this position, if there is one."""
        if self.base_url is None:
            return None
        separator = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{separator}t={int(self.start)}"

    def reference(self) -> str:
        """Short inline citation: ``[12:04]``, or the section too if known."""
        if self.section:
            return f"[{self.section}, {self.timestamp}]"
        return f"[{self.timestamp}]"


@dataclass(frozen=True)
class Lecture:
    """A transcript, reassembled and ready to read, render, or cite."""

    meta: LectureMeta
    sections: tuple[Section, ...]
    provenance: Provenance
    corrections: tuple[Correction, ...] = ()
    """Spellings the transcript probably got wrong, held once per document."""

    @property
    def passages(self) -> tuple[Passage, ...]:
        return tuple(passage for _, passage in self.walk())

    @property
    def text(self) -> str:
        return "\n\n".join(passage.text for passage in self.passages)

    def walk(self) -> Iterator[tuple[Section, Passage]]:
        """Every passage with the section it belongs to."""
        for section in self.sections:
            for passage in section.passages:
                yield section, passage

    def between(self, start: float, end: float) -> Lecture:
        """The part overlapping ``[start, end]`` seconds, still citable.

        Metadata and provenance come along; sections left empty are dropped.
        """
        sections = []
        for section in self.sections:
            kept = tuple(
                passage
                for passage in section.passages
                if passage.start < end and passage.end > start
            )
            if kept:
                sections.append(replace(section, passages=kept))

        return replace(self, sections=tuple(sections))

    def locator_for(self, passage: Passage, section: Section | None = None) -> Locator:
        return Locator(
            source_id=self.meta.source_id,
            start=passage.start,
            end=passage.end,
            section=section.title if section else None,
            base_url=self.meta.url,
        )

    def locators(self) -> Iterator[Locator]:
        for section, passage in self.walk():
            yield self.locator_for(passage, section)

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": SCHEMA_VERSION,
            "meta": self.meta.to_dict(),
            "sections": [section.to_dict() for section in self.sections],
            "provenance": self.provenance.to_dict(),
            "corrections": [c.to_dict() for c in self.corrections],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lecture:
        version = data.get("v")
        if version != SCHEMA_VERSION:
            raise MalformedLecture(
                detail=(
                    f"schema version {version!r} is not supported "
                    f"(this build reads version {SCHEMA_VERSION})"
                )
            )
        return cls(
            meta=LectureMeta.from_dict(data["meta"]),
            sections=tuple(Section.from_dict(s) for s in data["sections"]),
            provenance=Provenance.from_dict(data["provenance"]),
            corrections=tuple(
                Correction.from_dict(c) for c in data.get("corrections", ())
            ),
        )


def sort_by_tier(tiers: Sequence[TrustTier]) -> tuple[TrustTier, ...]:
    """Order trust tiers by the default policy, most trusted first."""
    return tuple(sorted(tiers, key=lambda tier: tier.rank))
