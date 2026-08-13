"""Removing rolling-window repetition.

Some caption formats draw automatic captions as a scrolling two-line window
and encode that by *repeating* the previous window's text at the head of each
cue. Concatenating such cues naively yields text where most of every sentence
appears twice.

This is applied selectively, never speculatively — see `refine.reflow.policy_for`.
Two measurements on real MIT OpenCourseWare captions decided that:

* Run unguarded over the automatic WebVTT track, the merge below reproduces the
  automatic ``json3`` token stream **exactly** — 6841 tokens either way. Two
  independent encodings of the same speech converging is strong evidence the
  merge is doing the right thing.
* Run over tracks that do *not* repeat, the same merge silently eats words. It
  turned "two by two by two Rubik's cube" into "two by two Rubik's cube",
  because a genuine repetition is indistinguishable from an encoded one.

Guarding the merge — requiring a minimum overlap length or fraction — was tried
and does not work: every threshold that protects the clean tracks also stops it
recovering the repeated one. So the decision has to come from what the track
*is*, not from what its text looks like.
"""

from __future__ import annotations

from dataclasses import replace

from ..models import Cue, Word

__all__ = ["dedupe_rolling_window", "overlap_length"]


def overlap_length(tail: list[str], head: list[str]) -> int:
    """Longest run of tokens that ends ``tail`` and begins ``head``."""
    for size in range(min(len(head), len(tail)), 0, -1):
        if tail[-size:] == head[:size]:
            return size
    return 0


def dedupe_rolling_window(cues: list[Cue]) -> list[Cue]:
    """Drop text each cue has already said, and cues that say nothing new.

    Timing anchors survive: a cue that contributes fresh text keeps its own
    start, so the passage built from it still points at the right moment.
    """
    seen: list[str] = []
    kept: list[Cue] = []

    for cue in cues:
        tokens = cue.text.split()
        fresh = tokens[overlap_length(seen, tokens) :]
        if not fresh:
            continue  # A flush cue: entirely a repeat of what came before.

        seen.extend(fresh)
        kept.append(replace(cue, text=" ".join(fresh), words=_align(cue, fresh)))

    return kept


def _align(cue: Cue, fresh: list[str]) -> tuple[Word, ...]:
    """Keep word timings only when they line up with the text that survived.

    On a repeating track the timed words are exactly the new portion of the
    cue, so after trimming they should match one-for-one. If they do not, the
    timings belong to text that is no longer there — dropping them loses
    precision, whereas keeping them would attach wrong times to words.
    """
    if cue.words and len(cue.words) == len(fresh):
        return cue.words
    return ()
