"""Where sentences end, and how to cut a cue on one.

A cue routinely holds the end of one sentence and the start of the next
(``"...to an end. Here are those ideas"``). Cutting it there first makes every
sentence boundary a cue boundary, so paragraphs — and their timestamps — can
start where a thought starts.

Cuts are made only where the source supplied word timings: the second half's
start must be a measured word time, never a share of the cue's duration.
`ends_sentence` lives here too, so the splitter and reflow's paragraph gate
share one definition.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..models import Cue

__all__ = ["cut_at", "ends_sentence", "looks_punctuated", "split_at_sentences"]

#: Characters that can end a sentence, ignoring any closing quote or bracket.
_ENDINGS = (".", "?", "!")

#: Closing punctuation that can sit after a full stop, typographic quotes included.
_TRAILING = "\"')]}»”’"  # noqa: RUF001

#: Opening punctuation that can sit before the first letter of a sentence.
_OPENING = "\"'([{«“‘"  # noqa: RUF001

#: Words whose full stop ends the word rather than the sentence. Short on
#: purpose: a missed abbreviation misplaces one paragraph break at worst.
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
#: Measured: punctuated captions run about 0.069, unpunctuated about 0.0003.
_PUNCTUATION_DENSITY = 0.01

#: Words needed before the measurement above is trusted at all.
_ENOUGH_TO_JUDGE = 200


def ends_sentence(text: str) -> bool:
    """Whether this text finishes a sentence."""
    return text.rstrip().rstrip(_TRAILING).endswith(_ENDINGS)


def looks_punctuated(cues: Sequence[Cue]) -> bool | None:
    """Whether this track's text carries sentence punctuation.

    `None` means too little text to say; the caller falls back to the tier.
    Measured rather than assumed, because platform captions used to be
    unpunctuated and now are not.
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

    Each piece is dated by the word that opens it, so a cue whose word timings
    do not line up with its text is returned whole.
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
    """`value`, kept inside the cue it came from, so pieces stay in order."""
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
    # A dotted acronym — "U.S.", "Ph.D." — every part a letter or two.
    return all(part.isalpha() and len(part) <= 2 for part in parts)


def _opens_a_sentence(word: str) -> bool:
    """Whether this word could begin a sentence.

    A capital or a digit is required, which does the work of a long
    abbreviation list: "e.g. the second one" fails it.
    """
    opener = word.lstrip(_OPENING)
    return bool(opener) and (opener[0].isupper() or opener[0].isdigit())
