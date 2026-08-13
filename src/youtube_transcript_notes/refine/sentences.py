"""Where sentences end, and how to cut a cue on one.

Caption cues break where a display line filled up, which has nothing to do with
where a sentence ended. A cue routinely reads ``"...bring it to an end. Here
are those ideas"`` — one sentence finishing and the next beginning, inside a
single timed unit. Grouping such cues into paragraphs can therefore only ever
start a paragraph mid-sentence, and the timestamp on that paragraph points at
the tail of a thought rather than the head of one.

Cutting the cue first removes the problem at its source: after this stage every
sentence boundary *is* a cue boundary, so the existing grouping rules can align
paragraphs to sentences without knowing anything about sentences.

The cut is only made where the source supplied word timings, because the second
half needs a start and the only honest one is the timing of the word that opens
it. Dividing a cue's duration by its word count would manufacture a number that
later code could not tell from a measured one — the same reason `parse.json3`
refuses to invent word timings for manual tracks.

This module also owns `ends_sentence`, which `refine.reflow` uses as its
paragraph gate. One definition on purpose: a splitter and a gate that disagreed
about what a sentence ending looks like would cut in places the gate then
refused to break on.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..models import Cue

__all__ = ["cut_at", "ends_sentence", "looks_punctuated", "split_at_sentences"]

#: Characters that can end a sentence, ignoring any closing quote or bracket.
_ENDINGS = (".", "?", "!")

#: Closing punctuation that can sit after a full stop. The curly quotes are
#: deliberate — transcripts of published captions use typographic quotes, and
#: missing them would hide the sentence end underneath.
_TRAILING = "\"')]}»”’"  # noqa: RUF001

#: Opening punctuation that can sit before the first letter of a sentence.
_OPENING = "\"'([{«“‘"  # noqa: RUF001

#: Words whose full stop ends the word rather than the sentence. Deliberately
#: short: a missed abbreviation costs one paragraph break in the wrong place,
#: while a missed *sentence* costs nothing at all, because the text simply
#: stays as it is today.
_ABBREVIATIONS = frozenset(
    {
        "e.g.",
        "i.e.",
        "etc.",
        "vs.",
        "cf.",
        "al.",
        "no.",
        "fig.",
        "dr.",
        "mr.",
        "mrs.",
        "ms.",
        "prof.",
        "st.",
        "jr.",
        "sr.",
    }
)

#: Sentence endings per word, below which a track is treated as unpunctuated.
#: The margin either side is enormous and the threshold is not delicate: the
#: measured lecture's human captions run 0.069 and its automatic ones 0.0003,
#: two orders of magnitude clear in both directions.
_PUNCTUATION_DENSITY = 0.01

#: Words needed before the measurement above is trusted at all. A handful of
#: cues can be punctuated or not by accident.
_ENOUGH_TO_JUDGE = 200


def ends_sentence(text: str) -> bool:
    """Whether this text finishes a sentence."""
    return text.rstrip().rstrip(_TRAILING).endswith(_ENDINGS)


def looks_punctuated(cues: Sequence[Cue]) -> bool | None:
    """Whether this track's text carries sentence punctuation.

    `None` means there is too little text to say, and the caller should fall
    back to what the track's tier claims.

    Measured rather than assumed because the assumption expired. Platform
    automatic captions were unpunctuated and uncased for years, and are not any
    more; a tier flag records what the track *is*, which is the right basis for
    deciding deduplication and the wrong one for deciding whether there are
    sentences to align to. The two differ in what being wrong costs: a bad
    deduplication guess eats words unrecoverably, while a bad punctuation guess
    only means a paragraph breaks on length instead of on a full stop, with
    `max_words` still bounding it.
    """
    words = 0
    endings = 0
    for cue in cues:
        for token in cue.text.split():
            words += 1
            if ends_sentence(token):
                endings += 1

    if words < _ENOUGH_TO_JUDGE:
        return None
    return endings >= words * _PUNCTUATION_DENSITY


def split_at_sentences(cues: Sequence[Cue]) -> list[Cue]:
    """Cut every cue that finishes one sentence and starts another."""
    split: list[Cue] = []
    for cue in cues:
        tokens = cue.text.split()
        split.extend(
            cut_at(
                cue,
                (i + 1 for i in range(len(tokens) - 1) if _breaks_after(tokens, i)),
            )
        )
    return split


def cut_at(cue: Cue, before: Iterable[int]) -> list[Cue]:
    """Cut one cue into pieces, each starting at the word index given.

    Every piece is dated by the timing of the word that opens it, which is the
    only honest answer and the reason a cue with no word timings is returned
    whole. `refine.dedupe` draws that line in the same place and for the same
    reason: a cue whose words and text have drifted apart gives no way to say
    which timing belongs to which word, and a cut placed on a guess would be a
    fabricated timestamp wearing a measured one's clothes.
    """
    tokens = cue.text.split()
    if len(cue.words) != len(tokens):
        return [cue]

    cuts = sorted({index for index in before if 0 < index < len(tokens)})
    if not cuts:
        return [cue]

    pieces = []
    start = cue.start
    for first, last in zip([0, *cuts], [*cuts, len(tokens)], strict=True):
        following = (
            _within(cue.words[last].start, start, cue.end)
            if last < len(tokens)
            else cue.end
        )
        pieces.append(
            Cue(
                text=" ".join(tokens[first:last]),
                start=start,
                duration=following - start,
                words=cue.words[first:last],
                speaker=cue.speaker,
                turn=cue.turn and first == 0,
            )
        )
        start = following
    return pieces


def _within(value: float, low: float, high: float) -> float:
    """`value`, kept inside the cue it came from.

    Word offsets are clamped at zero by the parsers, so one can in principle
    land before the cue that carries it. Letting that through would put a
    piece's start before its predecessor's and break the ordering every
    consumer downstream relies on.
    """
    return min(max(value, low), high)


def _breaks_after(tokens: list[str], index: int) -> bool:
    word = tokens[index]
    if not ends_sentence(word):
        return False
    if _is_abbreviation(word):
        return False
    return _opens_a_sentence(tokens[index + 1])


def _is_abbreviation(word: str) -> bool:
    lowered = word.lower().lstrip(_OPENING).rstrip(_TRAILING)
    if lowered in _ABBREVIATIONS:
        return True

    stem = lowered[:-1]
    parts = stem.split(".")
    if len(parts) == 1:
        return len(stem) == 1 and stem.isalpha()  # an initial: "J."
    # A dotted acronym — "U.S.", "Ph.D." Every part is a letter or two, which
    # is what separates it from a genuine sentence end followed by nothing.
    return all(part.isalpha() and len(part) <= 2 for part in parts)


def _opens_a_sentence(word: str) -> bool:
    """Whether this word could begin a sentence.

    Requiring a capital or a digit is the conservative half of the test, and
    does the work of a much longer abbreviation list: "3.5 billion" and "e.g.
    the second one" both fail it. A sentence wrongly left uncut is invisible —
    it is exactly what happens today.
    """
    opener = word.lstrip(_OPENING)
    return bool(opener) and (opener[0].isupper() or opener[0].isdigit())
