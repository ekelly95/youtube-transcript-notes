"""Removing rolling-window repetition.

Automatic vtt/srt tracks draw a scrolling two-line window by repeating the
previous window's text at the head of each cue. This merge removes it — but
only for tracks known to repeat (see `refine.reflow.policy_for`), because:

* on MIT OpenCourseWare's automatic WebVTT it reproduces the automatic json3
  token stream exactly (6841 tokens either way), and
* on a track that does not repeat it eats genuine repetition ("two by two by
  two Rubik's cube" lost a "by two"). No overlap threshold separates the two.
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

    A cue that contributes fresh text keeps its own start.
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
    """Keep word timings only when they match the surviving text one-for-one."""
    if cue.words and len(cue.words) == len(fresh):
        return cue.words
    return ()
