"""Names the recogniser mangled, and the spellings the source already knew.

The title and chapter headings are typed by a person, and they name exactly
what a recogniser gets wrong: people, products, terms of art. This stage
compares them with the transcript and *proposes* corrections, shown beside the
original words — never edits, so a repair can always be told from a
hallucination.

The automatic half is deliberately narrow. Fuzzy ratios (`difflib`) were
measured first and proposed 140 corrections on a two-hour interview, about
eight of them right. Edit distance counts what differs, so it catches
near-misses within a character or two (`Enthropic`, `Cowerk`) and little else.
Plurals and possessives of a harvested term are refused, because headings are
singular and speakers are not.

Acoustic errors ("quad code" for "Claude Code") are out of reach of any
spelling measure; they are named once in a glossary or `--corrections` file
and caught on every later run.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from ..errors import MalformedCorrections, PayloadTooLarge
from ..limits import MAX_GLOSSARY_TERMS
from ..models import Correction, LectureMeta, Passage

__all__ = [
    "Glossary",
    "propose_corrections",
    "read_corrections",
    "read_glossary",
    "terms_from",
]

#: Shortest single-word term worth watching. Shorter words sit one edit from
#: too much ordinary English ("Meta": meat, beta, mega, met).
_SHORTEST_TERM = 6

#: Shortest single word taken out of a longer name. Higher than
#: `_SHORTEST_TERM` because the evidence is weaker — see `_worth_watching`.
_SHORTEST_INHERITED = 8

#: Terms at least this long tolerate two edits; shorter ones tolerate one.
#: Admits "Cloud Code" (2) for "Claude Code" and refuses "Claude to" (3).
_LONG_ENOUGH_FOR_TWO = 10

#: Characters stripped from the edge of a phrase before comparing, so that
#: "code." and "code" are the same word.
_EDGES = ".,;:!?\"'()[]{}—–-"  # noqa: RUF001

_WORD = re.compile(r"\S+")


class Glossary(NamedTuple):
    """Canonical spellings, and the wrong forms already known for them."""

    terms: dict[str, str]
    """Canonical spelling to where it came from."""

    variants: dict[str, tuple[str, str]]
    """Known-wrong spelling, folded, to what it should be and who says so."""

    def merged_with(self, other: Glossary) -> Glossary:
        """This glossary over `other` where the two name the same thing."""
        return Glossary(
            terms={**other.terms, **self.terms},
            variants={**other.variants, **self.variants},
        )


def terms_from(meta: LectureMeta) -> Glossary:
    """Canonical spellings the source supplied, mapped to where they came from.

    Proper-noun phrases only. A heading capitalises its first word regardless,
    so `## Lessons from Meta` yields "Meta" and not "Lessons".
    """
    found: dict[str, str] = {}
    sources = [(meta.title, "title"), (meta.channel or "", "channel")]
    sources += [(chapter.title, "chapter") for chapter in meta.chapters]

    for text, origin in sources:
        for phrase in _proper_nouns(text):
            found.setdefault(phrase, origin)
    return Glossary(terms=found, variants={})


def read_glossary(text: str, source: str = "glossary") -> Glossary:
    """Read a glossary file.

    One term per line. `Anthropic` alone watches for near-misses of it;
    `Claude Code: quad code, Squad code` also names exact wrong forms. `#`
    starts a comment.
    """
    terms: dict[str, str] = {}
    variants: dict[str, tuple[str, str]] = {}

    for line in text.splitlines():
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue

        term, _, wrong = entry.partition(":")
        term = term.strip()
        if not term:
            continue

        terms.setdefault(term, "glossary")
        for form in wrong.split(","):
            folded = _fold(form)
            if folded and folded != _fold(term):
                # First entry wins, so a form listed under two terms resolves
                # the same way every run rather than by dictionary order.
                variants.setdefault(folded, (term, "glossary"))

    # Counted after parsing: the ceiling protects the scan, not the parse.
    entries = len(terms) + len(variants)
    if entries > MAX_GLOSSARY_TERMS:
        raise PayloadTooLarge(
            source=source,
            measured=f"{entries:,} terms",
            limit=f"{MAX_GLOSSARY_TERMS:,} terms",
        )

    return Glossary(terms=terms, variants=variants)


def read_corrections(records: Sequence[object], source: str) -> Glossary:
    """A model's corrections table, as exact forms to watch for.

    Read as a glossary, so a correction reported once is marked everywhere the
    phrase occurs.
    """
    variants: dict[str, tuple[str, str]] = {}

    for record in records:
        if not isinstance(record, dict):
            raise MalformedCorrections(
                source=source, detail=f"not an object: {record!r:.60}"
            )
        wrong, right = record.get("wrong"), record.get("right")
        if not isinstance(wrong, str) or not isinstance(right, str):
            raise MalformedCorrections(
                source=source, detail=f"entry has no wrong/right pair: {record!r:.60}"
            )

        folded = _fold(wrong)
        if folded and folded != _fold(right):
            evidence = record.get("evidence")
            variants.setdefault(
                folded,
                (
                    right,
                    evidence if isinstance(evidence, str) and evidence else "given",
                ),
            )

    return Glossary(terms={}, variants=variants)


def propose_corrections(
    passages: Sequence[Passage], glossary: Glossary
) -> tuple[Correction, ...]:
    """Find phrases that are a known or likely misspelling of a term."""
    watched = {
        term: origin for term, origin in glossary.terms.items() if _worth_watching(term)
    }
    known = {_fold(term) for term in watched} | {_fold(t) for t in glossary.terms}

    by_length: dict[int, list[tuple[str, str]]] = {}
    for term, origin in watched.items():
        by_length.setdefault(len(term.split()), []).append((term, origin))
    # A known-wrong form needs its own window length scanned even when no term
    # happens to be that many words long.
    for wrong in glossary.variants:
        by_length.setdefault(len(wrong.split()), [])

    if not by_length:
        return ()

    found: dict[tuple[str, str], Correction] = {}
    for passage in passages:
        for phrase, term, origin, distance in _matches(
            passage.text, by_length, known, glossary.variants
        ):
            key = (_fold(phrase), term)
            seen = found.get(key)
            found[key] = (
                seen.again()
                if seen
                else Correction(
                    wrong=phrase,
                    right=term,
                    at=passage.start,
                    confidence=1.0 if distance == 0 else round(1.0 - distance / 10, 2),
                    evidence=origin,
                )
            )

    return tuple(sorted(found.values(), key=lambda c: (-c.occurrences, c.at or 0.0)))


def _matches(
    text: str,
    by_length: dict[int, list[tuple[str, str]]],
    known: set[str],
    variants: Mapping[str, tuple[str, str]],
) -> Iterable[tuple[str, str, str, int]]:
    spans = [match.span() for match in _WORD.finditer(text)]
    hits = []

    for length, terms in by_length.items():
        for start in range(len(spans) - length + 1):
            phrase = text[spans[start][0] : spans[start + length - 1][1]]
            found = _hit(phrase, terms, known, variants)
            if found is not None:
                hits.append((start, length, found))

    # Longest first, so "Erik Domane" and "Domane" count as one mistake.
    taken: set[int] = set()
    for start, length, found in sorted(hits, key=lambda hit: (-hit[1], hit[0])):
        window = range(start, start + length)
        if taken.isdisjoint(window):
            taken.update(window)
            yield found


def _hit(
    phrase: str,
    terms: list[tuple[str, str]],
    known: set[str],
    variants: Mapping[str, tuple[str, str]],
) -> tuple[str, str, str, int] | None:
    folded = _fold(phrase)
    if not folded:
        return None

    named = variants.get(folded)
    if named is not None:
        return phrase.strip(_EDGES), named[0], named[1], 0

    # Already one of the spellings being checked against — either right, or
    # right about something else. Not an error either way.
    if folded in known:
        return None

    for term, origin in terms:
        distance = _distance(folded, _fold(term))
        if distance is not None and not _merely_inflected(folded, term, origin):
            return phrase.strip(_EDGES), term, origin, distance
    return None


#: Plural and possessive endings: one edit each, but the same word.
_INFLECTIONS = ("s", "es", "'s", "’s")  # noqa: RUF001


def _merely_inflected(phrase: str, term: str, origin: str) -> bool:
    """Whether these differ only by a plural or possessive ending.

    Harvested headings are singular and speakers use plurals: MIT 6.006's
    chapter `Simple Algorithm` once marked every "algorithms". Terms from a
    glossary file are exempt — naming `Devadas` still catches "Devada".
    """
    if origin == "glossary":
        return False

    short, long = sorted((phrase, _fold(term)), key=len)
    return any(long == short + ending for ending in _INFLECTIONS)


def _distance(phrase: str, term: str) -> int | None:
    """How many edits apart, or None if further than this term tolerates."""
    # A version is not a spelling: "Sonnet 4.5" is one edit from "Sonnet 3.5".
    # Terms with digits match exactly or not at all.
    if _digits(term) or _digits(phrase):
        return None

    allowed = 2 if len(term) >= _LONG_ENOUGH_FOR_TWO else 1
    if abs(len(phrase) - len(term)) > allowed:
        return None

    distance = _levenshtein(phrase, term, allowed)
    return distance if distance is not None and distance > 0 else None


def _levenshtein(left: str, right: str, ceiling: int) -> int | None:
    """Edit distance, abandoned as soon as it is certainly over `ceiling`.

    Written out to keep the pipeline free of third-party dependencies.
    """
    if len(left) < len(right):
        left, right = right, left

    previous = list(range(len(right) + 1))
    for index, one in enumerate(left, start=1):
        current = [index]
        for position, other in enumerate(right, start=1):
            current.append(
                min(
                    previous[position] + 1,
                    current[position - 1] + 1,
                    previous[position - 1] + (one != other),
                )
            )
        if min(current) > ceiling:
            return None
        previous = current

    return previous[-1] if previous[-1] <= ceiling else None


#: Separators that restart title case part way through a title. The word after
#: one is capitalised by position, so in `CS230 | Lecture 8: Agents` "Lecture"
#: is not evidence of a name.
_SEGMENTS = re.compile(r"[|:;.–—]|\s-\s")  # noqa: RUF001


def _proper_nouns(text: str) -> list[str]:
    phrases: list[str] = []
    for segment in _SEGMENTS.split(text):
        phrases.extend(_proper_nouns_in(segment))
    return phrases


def _proper_nouns_in(text: str) -> list[str]:
    words = text.split()
    phrases: list[str] = []

    run: list[str] = []
    for index, word in enumerate([*words, ""]):
        if word[:1].isupper():
            run.append(word)
            continue
        if run:
            opened_the_text = index - len(run) == 0
            # A run that opened the text was capitalised by position, so it
            # contributes only forms of two or more words.
            if len(run) > 1 or not opened_the_text:
                _collect(phrases, " ".join(run))
            if opened_the_text and len(run) > 1:
                _collect(phrases, " ".join(run[1:]), inherited=True)
            run = []

    return phrases


def _collect(phrases: list[str], phrase: str, inherited: bool = False) -> None:
    trimmed = phrase.strip(_EDGES)
    if _worth_watching(trimmed, inherited):
        phrases.append(trimmed)


def _worth_watching(term: str, inherited: bool = False) -> bool:
    if len(term.split()) > 1:
        return len(term) >= _SHORTEST_TERM
    if not any(char.isalpha() for char in term):
        return False
    # A word taken out of a longer name is weaker evidence, so it must be
    # longer: "Anthropic" from "Joining Anthropic", but not "Code's".
    return len(term) >= (_SHORTEST_INHERITED if inherited else _SHORTEST_TERM)


def _digits(text: str) -> str:
    return "".join(char for char in text if char.isdigit())


def _fold(text: str) -> str:
    return " ".join(word.strip(_EDGES) for word in text.split()).casefold().strip()
