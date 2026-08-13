"""The core data model.

Every type here is frozen, and every container is a tuple rather than a list.
That is not fussiness: it makes the "renderers are pure functions of a Lecture"
invariant something the language enforces rather than something a code review
has to catch.

`Lecture.to_dict()` and `Lecture.from_dict()` are exact inverses. That round
trip is the whole persistence story — a lecture written to disk and read back
compares equal to the original, which is what lets the cache be trusted and
makes re-rendered notes reproducible.

Serialisation is written out by hand rather than derived by introspection. It
is more lines, but the round-trip test then means something, and there is no
metaprogramming to debug when a field's type changes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
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
    """Stable content hash for a caption payload.

    Used by `Provenance` so a stored lecture can be checked against the source
    it came from, and so the cache can tell "same video, re-uploaded captions"
    from "same captions, fetched again".
    """
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

    Deliberately not ``strftime("%B")``, which is locale-dependent — rendered
    output would then differ between machines and golden-file tests would fail
    on someone else's laptop for no real reason.
    """
    if value is None:
        return "n.d."
    return f"{value.day} {MONTH_NAMES[value.month - 1]} {value.year}"


def format_timestamp(seconds: float) -> str:
    """Seconds to a human timestamp: ``5:07`` under an hour, ``1:05:07`` over.

    Lecture-shaped rather than caption-shaped — no leading zero hour and no
    milliseconds, because these end up in prose and citations, not in a
    subtitle file.
    """
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class TrustTier(str, Enum):
    """How much the text can be trusted, and what that implies downstream.

    A plain manual-versus-generated flag does not survive having more than two
    sources, so this is a tier rather than a boolean. It drives two different
    decisions, which is what stops it being a decorative label: `rank` orders
    the priority resolver, and `assume_punctuated` selects the reflow strategy.
    """

    MANUAL = "manual"
    """Human-written captions. Punctuated, cased, and usually accurate on
    technical vocabulary — which is exactly where ASR fails hardest on
    lecture material."""

    ASR_PLATFORM = "asr_platform"
    """Platform auto-captions. Typically unpunctuated and uncased, and they
    overlap heavily because they are drawn as a rolling window."""

    ASR_LOCAL = "asr_local"
    """Transcribed outside the tool — a caption file marked ``.whisper.`` or
    ``.transcribed.``. Punctuated and cased, quality varies with whatever
    made it, but timings are clean and there is no rolling overlap."""

    TRANSLATED = "translated"
    """Machine-translated from another track. Punctuated, but a derived
    artefact — quote it with care and prefer it last."""

    @property
    def rank(self) -> int:
        """Resolution priority; lower wins. A default policy, not a law —
        the resolver lets a caller supply their own tier ordering."""
        return _TIER_RANK[self]

    @property
    def prose(self) -> str:
        """How this tier reads in a rendered document.

        Written for someone who has never heard of a "trust tier": it appears
        in bylines and citations, where ``asr_platform`` would be jargon.
        """
        return _TIER_PROSE[self]

    @property
    def assume_punctuated(self) -> bool:
        """Whether text from this tier arrives with sentence punctuation.

        False means the reflow engine cannot split on sentence boundaries and
        must fall back to timing gaps.
        """
        return self is not TrustTier.ASR_PLATFORM


_TIER_RANK = {
    TrustTier.MANUAL: 0,
    TrustTier.ASR_PLATFORM: 1,
    TrustTier.ASR_LOCAL: 2,
    TrustTier.TRANSLATED: 3,
}

_TIER_PROSE = {
    TrustTier.MANUAL: "human-written captions",
    TrustTier.ASR_PLATFORM: "platform auto-generated captions",
    TrustTier.ASR_LOCAL: "locally transcribed audio",
    TrustTier.TRANSLATED: "machine-translated captions",
}


@dataclass(frozen=True)
class Word:
    """A single word with its own start time.

    Only some caption formats carry this (YouTube's ``json3`` does). When
    present it makes reflow timestamps accurate to the word rather than to the
    cue, which matters when a cue spans a sentence boundary.
    """

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

    Cues are transient: parsers emit them, the reflow engine consumes them, and
    a finished `Lecture` holds `Passage` objects instead. Nothing downstream of
    reflow should be reasoning about cues.
    """

    text: str
    start: float
    duration: float
    words: tuple[Word, ...] = ()
    speaker: str | None = None
    turn: bool = False
    """Whether a new speaker starts here.

    Separate from `speaker` because captions distinguish the two: a `NAME:`
    label says who is talking now, while a bare `>>` says only that it is
    somebody else. Collapsing them would mean either inventing identities for
    anonymous turns or losing the turn, and both are worse than carrying one
    extra flag."""

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
    """A readable unit of lecture text — a paragraph, in practice.

    `start` is load-bearing: it must equal the start of the first cue that fed
    this passage, so that a quote pulled from the middle of a rendered document
    still points at the moment it was said. Reflow may merge, dedupe and
    re-punctuate freely, but it may never lose that anchor.
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
    """A run of passages under one heading.

    `title` is None when the source published no chapters, in which case the
    whole lecture is a single untitled section.
    """

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

    Never applied to `Passage.text`, and that is the whole design. A corrected
    transcript and a hallucinated one look identical; a transcript that carries
    its corrections alongside the words it actually contains can be checked,
    searched for what was really said, and disbelieved in the one place it
    deserves to be. `evidence` says where the right spelling came from, so a
    reader can weigh it without rerunning anything.
    """

    wrong: str
    right: str
    at: float | None = None
    confidence: float = 1.0
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
            "confidence": self.confidence,
            "evidence": self.evidence,
            "occurrences": self.occurrences,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Correction:
        return cls(
            wrong=data["wrong"],
            right=data["right"],
            at=data.get("at"),
            confidence=data.get("confidence", 1.0),
            evidence=data.get("evidence", ""),
            occurrences=data.get("occurrences", 1),
        )


@dataclass(frozen=True)
class Chapter:
    """A chapter marker as published by the source, before passages exist.

    Distinct from `Section` on purpose: a chapter is an assertion by the
    lecturer about structure, a section is the result of fitting passages to
    it. Keeping them separate means a bad chapter list cannot silently destroy
    text.
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
    """Everything needed to cite the lecture as a source.

    A tool that returns only transcript text solves half the problem: without
    this, the text cannot be referenced in written work.
    """

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
    """Where this text came from and how it was obtained.

    Attached to every `Lecture`. This is the line between a scraper and
    something whose output can be defended: it records not just the source but
    which track was chosen, when, and a hash of exactly what was parsed.
    """

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
    """A citable position inside a lecture.

    The single place that knows how a position becomes something a reader can
    act on — a timestamp to write down, or a link that opens at the right
    moment. Renderers ask for one rather than formatting times themselves.
    """

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
    """A lecture, reassembled and ready to read, render, or cite."""

    meta: LectureMeta
    sections: tuple[Section, ...]
    provenance: Provenance
    corrections: tuple[Correction, ...] = ()
    """Spellings the transcript probably got wrong. A document-level fact, not
    a passage-level one: "quad code" means Claude Code wherever it appears, and
    holding one list beats repeating the same finding on forty paragraphs."""

    @property
    def passages(self) -> tuple[Passage, ...]:
        return tuple(passage for _, passage in self.walk())

    @property
    def text(self) -> str:
        return "\n\n".join(passage.text for passage in self.passages)

    def walk(self) -> Iterator[tuple[Section, Passage]]:
        """Every passage with the section it belongs to.

        Renderers iterate this rather than nesting two loops, so section
        context is always available where a passage is emitted.
        """
        for section in self.sections:
            for passage in section.passages:
                yield section, passage

    def between(self, start: float, end: float) -> Lecture:
        """The part of this lecture overlapping ``[start, end]``, in seconds.

        Metadata and provenance come along unchanged, so an excerpt is still a
        citable lecture rather than a loose bag of text. Sections left with no
        passages are dropped instead of appearing as empty headings.
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
