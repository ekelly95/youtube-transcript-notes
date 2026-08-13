"""Caption markup, read as structure rather than carried as noise.

A caption track says more than the words. `[MUSIC]` says nobody is speaking,
`[INAUDIBLE]` says the captioner could not hear, `[? maybe ?]` says they heard
something and are not sure, `>>` says the speaker changed, and `PROFESSOR:`
says who it is. All five are signals, and until this stage existed all five
arrived in the finished document as literal text — and then, because a
transcript is a stranger's text and `render.escape` neutralises brackets
without inspecting them, as *backslashed* literal text: `\\[INAUDIBLE\\]`. The
captioner did the work and the pipeline threw it away.

Consuming the markup here rather than in the renderers is what keeps that
escaping safe to leave alone. `render.escape` is deliberately unconditional —
it has no notion of which brackets are trustworthy, and giving it one would be
the beginning of an exception list that an uploader eventually writes into. By
the time a passage reaches a renderer the recognised markup is already gone,
turned into fields, and anything left over is unrecognised text that *should*
be neutralised. Parsers stay faithful for the same reason in reverse: they
represent what was published, and this is the stage where published becomes
readable.

Two things are deliberately not done. Non-speech cues are dropped rather than
noted, because "[MUSIC]" is not something anybody said and a note saying so is
worth less than the clutter it costs. And an anonymous `>>` turn is never given
a name or a number: the marker asserts that the speaker changed, not that this
is the second speaker, and alternating A/B labels across an interview would
state something the captions never said.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from ..models import Cue, Word
from .sentences import cut_at

__all__ = ["consume_markup"]

#: What a bracketed cue means when it is not speech. Matched case-insensitively
#: against the whole bracket body, so a stray "[music]" in quoted prose has to
#: be alone in its brackets to be dropped.
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

#: How an unheard stretch is written once consumed. Round brackets rather than
#: square on purpose: square brackets are what `render.escape` backslashes, so
#: a marker built from them would arrive in the document as the same
#: `\\[INAUDIBLE\\]` residue this stage exists to remove. Round brackets are
#: left alone by the escaper — with `[` and `]` neutralised, a bare `(…)`
#: cannot become a link — and, unlike the typographic angle marks tried first,
#: they survive a Windows console, which encodes stdout as cp1252 and refuses
#: anything outside it.
_UNHEARD_MARK = "(inaudible)"

#: How an uncertain transcription is written: the captioner's guess, kept, with
#: their doubt attached. `[? a cure. ?]` becomes `a cure.(?)`.
_DOUBT_MARK = "(?)"

_BRACKETED = re.compile(r"\[([^\[\]]*)\]")

#: A speaker label at the start of a cue: `PROFESSOR:`, `GRAHAM NEUBIG:`,
#: `AUDIENCE:`. Upper case throughout and at least three characters, which is
#: what keeps it from eating an ordinary sentence that happens to contain a
#: colon. A lecturer who shouts a single word before a colon will lose it; that
#: is the trade, and it is the right way round.
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
                # Somebody else is talking and the captions did not say who.
                # Keeping the previous name would attribute their words to the
                # wrong person, which is worse than not knowing.
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
    """When this cue starts, now that a marker has been taken off the front.

    `>> That matches what we saw` starts, as published, at the `>>`. Once the
    glyph is gone the cue starts when somebody said "That", and using the
    older moment would put the paragraph's timestamp on markup — half a second
    of nothing, at the exact boundary where a listener is checking a quote.
    """
    if not words or words is cue.words:
        return cue.start
    return min(max(words[0].start, cue.start), cue.end)


def _realign(cue: Cue, text: str) -> tuple[Word, ...]:
    """The word timings that still belong to this cue, once markup has gone.

    Removing a leading `>>` or a `PROFESSOR:` leaves the words that remain as a
    suffix of the originals, which can be matched exactly rather than guessed
    at. Markup removed from the middle cannot be matched that way, and the
    timings are dropped rather than misattributed — one word's start standing
    in for another's is precisely the fabrication the rest of this package
    refuses to make.
    """
    tokens = text.split()
    if len(cue.words) != len(cue.text.split()):
        return ()
    if len(tokens) == len(cue.words):
        return cue.words

    tail = cue.words[len(cue.words) - len(tokens) :]
    return tail if [word.text for word in tail] == tokens else ()


def _turns(cue: Cue) -> list[Cue]:
    """Split a cue that carries a speaker change part way through it.

    Captioners usually start a cue at `>>`, in which case there is nothing to
    split. When they do not, the turn belongs at the word it happened on and
    not at the start of whatever cue contains it, so the cue is cut there — by
    the same word timings that date a sentence boundary. A track with no word
    timings keeps the cue whole and the turn moves to its start, which is at
    most one cue early.
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

    # Any `>>` left is one this cue did not open with and could not be cut on.
    # The glyph is markup either way and does not belong in the prose.
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
    # Unrecognised. Left exactly as published, and neutralised downstream.
    return match.group(0)


def _tidy(text: str, previous: list[Cue]) -> str:
    """Collapse the gaps and the repetition that removing markup leaves behind."""
    words = text.split()

    merged: list[str] = []
    for word in words:
        # Runs of unheard speech are one fact, not several. Student questions
        # in a lecture hall produce them by the handful, and rendered one per
        # marker they read as shrapnel rather than as "this part is missing".
        if word == _UNHEARD_MARK and merged and merged[-1] == _UNHEARD_MARK:
            continue
        merged.append(word)

    # The same run, arriving one marker per cue rather than several in one.
    if merged == [_UNHEARD_MARK] and previous:
        if previous[-1].text.endswith(_UNHEARD_MARK):
            return ""

    return " ".join(merged)
