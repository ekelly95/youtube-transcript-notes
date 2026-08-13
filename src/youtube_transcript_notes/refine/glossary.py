"""Names the recogniser mangled, and the spellings the source already knew.

A lecture arrives carrying part of its own answer key. The title and the
uploader's chapter headings are typed by a person, so they spell the subject
correctly, and they name exactly the things a speech recogniser gets wrong:
products, people, the terms of art the talk is about. In one measured interview
the heading read `## Claude Cowork` while the transcript underneath it said
"Claude Colab", and the pipeline held both strings at once and never compared
them.

This stage compares them. It proposes; it does not edit. A `Correction` says
what was found, what it should be, how sure that is and where the right
spelling came from, and the renderers show the original with the correction
beside it. Rewriting the transcript in place would be the one change that
cannot be undone, because the reader would lose the ability to tell a repair
from a hallucination, and a transcript nobody can check is worth less than a
wrong one everybody can.

## Why the matching is as narrow as it is

The obvious design — fuzzy-match every phrase against every known term — was
built first and measured on a two-hour interview. It proposed 140 corrections,
of which about eight were right. `difflib`'s ratio is dominated by the shared
part of a string, so "Claude to" scores 0.80 against "Claude Code" and beats
the genuine "quad code" at 0.70: the noise outranked the signal, and no
threshold existed that admitted one without the other.

Edit distance separates them, because it counts what differs instead of
rewarding what matches. On the same pairs: "Claude core" 1, "Cloud Code" 2,
"Claude to" 3, "quad code" 4. So the automatic half of this stage catches
near-misses within a character or two and nothing else, which is a small,
reliable win — `Enthropic`, `Cherney`, `Mahes`, `Cowerk` — rather than a large
unreliable one.

Narrow is still not narrow enough on its own, because **a plural is one edit
away from its own singular**. Headings are written singular and lecturers speak
in the plural, so the chapter `Simple Algorithm` on MIT 6.006 contributed the
word "Algorithm" and the note came back reading `algorithms [Algorithm]`
thirty-six times — every correction that lecture produced, all of them wrong,
printed in the middle of the sentences the note exists to make readable. A
proposal that is only an inflection of the word already there is refused; see
`_merely_inflected`, including why it does not apply to a term somebody wrote
down on purpose.

The rest is a list, and the list is the point. Speech recognition errors are
*acoustic*: "Colab" for "Cowork", "quad code" for "Claude Code", "sauna 3.5"
for "Sonnet 3.5". Nothing measuring spelling will ever reach those. They have
to be named once, by someone who knows the domain — a reader or a model
reading with `--corrections` — and after that they are caught for nothing on
every lecture that follows. A glossary is worth more in its second year than
its first.
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

#: Shortest single-word term worth watching. Below this, ordinary English sits
#: one character away from half the vocabulary — "Meta" is one edit from meat,
#: beta, mega and met — and a short chapter title would annotate the document
#: into uselessness.
_SHORTEST_TERM = 6

#: Shortest single word taken out of a longer name. Higher than
#: `_SHORTEST_TERM` because the evidence is weaker — see `_worth_watching`.
_SHORTEST_INHERITED = 8

#: How many characters may differ before a phrase stops being a misspelling of
#: a term and starts being a different phrase. Two for something long enough to
#: absorb it, one otherwise. Measured: this admits "Cloud Code" (2) and refuses
#: "Claude to" (3).
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

    Proper-noun phrases only — maximal runs of capitalised words. The subtlety
    is that a heading capitalises its first word whether or not it is a name,
    so `## Lessons from Meta` must yield "Meta" and not "Lessons". A run that
    opens its heading therefore contributes only its multi-word forms, and a
    lone capitalised word counts only where nothing forced it to be capital.
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

    One term per line. `Anthropic` on its own says to watch for near-misses of
    it. `Claude Code: quad code, Squad code` also says that those two exact
    forms are it, which is how the errors no spelling measure can reach get
    caught. `#` starts a comment.
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

    # Counted after parsing, because parsing a megabyte is milliseconds — the
    # scan is what the ceiling protects: every term here runs an edit distance
    # against every word window of every passage, so a list the byte cap
    # happily admits can cost minutes per lecture. Refused whole, like every
    # other ceiling in `limits`.
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

    Read as a glossary rather than kept as findings, so that a correction found
    once is applied everywhere the phrase occurs and counted — a model reading
    a two-hour transcript reports "quad code" once, and the note should mark
    all twenty-three of them.
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

    # Longest first. "Erik Domane" and "Domane" match the same two words, and
    # reporting both would count one mistake twice and put two rows in the
    # appendix where the speaker's name was got wrong once.
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


#: Plural and possessive endings. A word carrying one of these is the same word,
#: and every one of them is a single edit — which is inside what `_distance`
#: allows, so without this check they all read as misspellings.
_INFLECTIONS = ("s", "es", "'s", "’s")  # noqa: RUF001


def _merely_inflected(phrase: str, term: str, origin: str) -> bool:
    """Whether these differ only by a plural or possessive ending.

    A term harvested from the lecture's own headings is usually singular, and
    the lecturer then says it in the plural all afternoon. On MIT 6.006 the
    chapter `Simple Algorithm` contributed the word "Algorithm", which is one
    edit from "algorithms" — so a correct transcript came back annotated
    `algorithms [Algorithm]` twenty times in one lecture. The proposals were
    not merely useless: every one is rendered beside the words, so the noise
    landed in the middle of the sentences the note exists to make readable.

    Only for the automatic half. A term somebody wrote in a glossary file is a
    decision they made on purpose, and honouring it is what makes the list
    worth keeping — if they name `Devadas`, "Devada" is still proposed, even
    though nothing about its shape distinguishes it from a plural.
    """
    if origin == "glossary":
        return False

    short, long = sorted((phrase, _fold(term)), key=len)
    return any(long == short + ending for ending in _INFLECTIONS)


def _distance(phrase: str, term: str) -> int | None:
    """How many edits apart, or None if further than this term tolerates."""
    # A version is not a spelling. "Sonnet 4.5" is one edit from "Sonnet 3.5",
    # "GPT-4" one from "GPT-4o", and neither is a misspelling of the other —
    # they are different things, and proposing the correction would quietly
    # rewrite which model somebody was talking about. So a term carrying a
    # digit is matched exactly or not at all: name the wrong forms in the
    # glossary, where saying so is a decision somebody made on purpose.
    if _digits(term) or _digits(phrase):
        return None

    allowed = 2 if len(term) >= _LONG_ENOUGH_FOR_TWO else 1
    if abs(len(phrase) - len(term)) > allowed:
        return None

    distance = _levenshtein(phrase, term, allowed)
    return distance if distance is not None and distance > 0 else None


def _levenshtein(left: str, right: str, ceiling: int) -> int | None:
    """Edit distance, abandoned as soon as it is certainly over `ceiling`.

    Written out rather than pulled in: the pure pipeline has no third-party
    code, and this is a dozen lines against a dependency that would have to be
    installed to render a note.
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


#: What starts a fresh run of title case part way through a title. A word
#: after one of these is capitalised because of where it sits, exactly as the
#: first word of the title is, and proves no more than that one does.
#: `Stanford CS230 | Autumn 2025 | Lecture 8: Agents` offers no evidence that
#: "Lecture" is a name — and watching it annotates every "lectures" in a
#: lecture.
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
            # A run that opened the text opened it capitalised whether or not
            # it is a name, so its first word proves nothing on its own. The
            # phrases it contributes all keep at least two words, where the
            # capitalisation of the second is evidence the first cannot fake.
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
    # A word taken out of a longer name is weaker evidence than one that stood
    # alone, so it has to be more distinctive to earn the same treatment.
    # "Joining Anthropic" is worth watching "Anthropic" for; "Claude Cowork"
    # is not worth watching "Cowork" for, and "Claude Code's" is certainly not
    # worth watching "Code's" for — one edit from "codes".
    return len(term) >= (_SHORTEST_INHERITED if inherited else _SHORTEST_TERM)


def _digits(text: str) -> str:
    return "".join(char for char in text if char.isdigit())


def _fold(text: str) -> str:
    return " ".join(word.strip(_EDGES) for word in text.split()).casefold().strip()
