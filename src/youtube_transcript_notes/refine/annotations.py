"""Caption markup, read as structure rather than carried as noise.

`[MUSIC]` says nobody is speaking, `[INAUDIBLE]` that the captioner could not
hear, `[? maybe ?]` that they guessed, `>>` that the speaker changed, and
`PROFESSOR:` who it is. This stage turns those into fields and marks.

Consuming markup here keeps `render.escape` unconditional: by the time a
passage reaches a renderer, recognised markup is gone and anything left is
text to neutralise. Non-speech cues are dropped, not noted. An anonymous `>>`
turn is never given a name or number — the captions only say the speaker
changed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from ..models import Cue, Word
from .sentences import cut_at

__all__ = ["consume_markup"]

#: Bracket bodies that mean "not speech". Matched against the whole body, so a
#: word must be alone in its brackets to be dropped.
_NON_SPEECH = frozenset(
    {
        "applause",
        "blank_audio",
        "cheering",
        "cough",
        "coughing",
        "crosstalk",
        "laugh",
        "laughing",
        "laughter",
        "music",
        "noise",
        "silence",
        "sound",
    }
)

#: Bracket bodies meaning the captioner could not make the words out.
_UNHEARD = frozenset({"inaudible", "unintelligible", "indistinct"})

#: How an unheard stretch is written. Round brackets, because `render.escape`
#: backslashes square ones and typographic marks break a cp1252 console.
_UNHEARD_MARK = "(inaudible)"

#: How an uncertain transcription is written: `[? a cure. ?]` becomes
#: `a cure.(?)`, the guess kept with the doubt attached.
_DOUBT_MARK = "(?)"

_BRACKETED = re.compile(r"\[([^\[\]]*)\]")

#: A speaker label opening a cue: `PROFESSOR:`, `GRAHAM NEUBIG:`. Upper case and
#: at least three characters, so an ordinary sentence with a colon survives.
_LABEL = re.compile(r"^\s*([A-Z][A-Z0-9 .'\-]{2,38}):\s*")

#: The speaker-change marker, as WebVTT and most captioners write it.
_TURN = ">>"


def consume_markup(cues: Sequence[Cue]) -> list[Cue]:
    """Turn recognised caption markup into fields, and drop what is not speech."""
    consumed: list[Cue] = []
    speaker: str | None = None

    for cue in cues:
        for piece in _turns(cue):
            text, turn, named = _read(piece.text)
            if named is not None:
                speaker = named
            elif turn:
                # Someone else is talking and the captions did not say who;
                # keeping the previous name would misattribute their words.
                speaker = None

            text = _tidy(text, previous=consumed)
            if not text:
                continue

            words = _realign(piece, text)
            start = _retime(piece, words)
            consumed.append(
                replace(
                    piece,
                    text=text,
                    start=start,
                    duration=max(piece.end - start, 0.0),
                    words=words,
                    speaker=speaker,
                    turn=turn or piece.turn,
                )
            )

    return consumed


def _retime(cue: Cue, words: tuple[Word, ...]) -> float:
    """When this cue starts once a leading marker is gone: at its first word."""
    if not words or words is cue.words:
        return cue.start
    return min(max(words[0].start, cue.start), cue.end)


def _realign(cue: Cue, text: str) -> tuple[Word, ...]:
    """The word timings that still belong to this cue, once markup has gone.

    A removed prefix leaves the remaining words as an exact suffix of the
    originals. Anything else drops the timings rather than misattributing them.
    """
    tokens = text.split()
    if len(cue.words) != len(cue.text.split()):
        return ()
    if len(tokens) == len(cue.words):
        return cue.words

    tail = cue.words[len(cue.words) - len(tokens) :]
    return tail if [word.text for word in tail] == tokens else ()


def _turns(cue: Cue) -> list[Cue]:
    """Split a cue at a speaker change part way through it.

    Cut by word timings where they exist; without them the cue stays whole and
    the turn moves to its start, at most one cue early.
    """
    tokens = cue.text.split()
    inner = [index for index, token in enumerate(tokens) if token == _TURN and index]
    if not inner:
        return [cue]

    pieces = cut_at(cue, inner)
    if len(pieces) == 1:
        return pieces
    return [pieces[0], *(replace(piece, turn=True) for piece in pieces[1:])]


def _read(text: str) -> tuple[str, bool, str | None]:
    """Strip the markers off one cue, saying what they meant."""
    turn = False
    stripped = text.lstrip()
    while stripped.startswith(_TURN):
        turn = True
        stripped = stripped[len(_TURN) :].lstrip()

    # A `>>` that could not be cut on is still markup, not prose.
    stripped = stripped.replace(_TURN, " ")

    named = None
    label = _LABEL.match(stripped)
    if label:
        named = label.group(1).strip()
        turn = True
        stripped = stripped[label.end() :]

    return _BRACKETED.sub(_bracket, stripped), turn, named


def _bracket(match: re.Match[str]) -> str:
    body = match.group(1).strip()
    folded = body.casefold()

    if folded.startswith("?") and folded.endswith("?"):
        # `[? a cure. ?]` — a guess the captioner flagged. Keep the guess.
        guess = body[1:-1].strip()
        return f"{guess}{_DOUBT_MARK}" if guess else _DOUBT_MARK
    if folded in _NON_SPEECH:
        return ""
    if folded in _UNHEARD:
        return _UNHEARD_MARK
    # Unrecognised: left as published, and neutralised downstream.
    return match.group(0)


def _tidy(text: str, previous: list[Cue]) -> str:
    """Collapse the gaps and the repetition that removing markup leaves behind."""
    words = text.split()

    merged: list[str] = []
    for word in words:
        # A run of unheard speech is one fact, not several.
        if word == _UNHEARD_MARK and merged and merged[-1] == _UNHEARD_MARK:
            continue
        merged.append(word)

    # The same run, arriving one marker per cue.
    if merged == [_UNHEARD_MARK] and previous:
        if previous[-1].text.endswith(_UNHEARD_MARK):
            return ""

    return " ".join(merged)
