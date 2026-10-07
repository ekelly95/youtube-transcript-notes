"""Turning caption cues into readable paragraphs.

Cues are one to three seconds long and break wherever a line filled. This
stage regroups them into paragraphs of a target length that end where a
sentence ends: a pause *proposes* a break, and the next sentence end *takes*
it. Text with no sentence punctuation (older automatic tracks) has nothing to
end on, so there pauses alone decide, bounded by `max_words`.

The pause threshold was measured: on MIT 6.006 Lecture 1, gaps between
human-written cues are bimodal (zero, or over two seconds), so anything from
0.3 s to 2 s yields the same breaks, and they land on real topic changes.

Invariant: every passage starts at the start of its first cue.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..models import Cue, Passage, TrustTier
from .annotations import consume_markup
from .dedupe import dedupe_rolling_window
from .sentences import ends_sentence, looks_punctuated, split_at_sentences

__all__ = ["ReflowPolicy", "passage_end", "policy_for", "reflow", "speech_end"]

#: How long a passage's last word may still be running after it started. The
#: measured gap between consecutive words has a 90th percentile of 1.0 s.
_WORD_TAIL = 1.0

#: Formats that mark caption scrolling structurally rather than by repeating
#: text. Deduplicating them would delete genuine repetition.
_STRUCTURED_SCROLL = frozenset({"json3"})

#: Fraction of `target_words` before a pause counts as a paragraph break rather
#: than a breath. Without it, frequent pauses decide paragraph length alone.
_PAUSE_FLOOR = 0.75

#: Multiple of `max_words` at which a paragraph breaks regardless. Punctuated
#: policy waits for a sentence end, and mislabelled unpunctuated text never
#: offers one; the measured average paragraph is about 135 words.
_HARD_CEILING = 2


@dataclass(frozen=True)
class ReflowPolicy:
    """How to turn one particular track's cues into paragraphs."""

    paragraph_gap: float = 1.0
    """Seconds of silence that propose a paragraph break."""

    target_words: int = 90
    """Paragraph length for text with sentences to end on — about forty seconds
    at lecture pace, short enough to check a quote by ear."""

    max_words: int = 250
    """Safety valve for text that offers no sentence to end on."""

    dedupe: bool = False
    """Whether this track repeats text to encode a scrolling window."""

    punctuated: bool = True
    """Whether the text has sentence punctuation to break on."""


def policy_for(
    tier: TrustTier, caption_format: str, cues: Sequence[Cue] = ()
) -> ReflowPolicy:
    """Choose a policy for one track.

    Deduplication is decided from what the track *is* (tier and format), never
    detected: no content threshold both repairs a rolling track and leaves a
    clean one intact, and a wrong guess deletes words. Punctuation is detected
    from the text, because platform captions are now punctuated; the tier is
    only the fallback for a track too short to measure. A wrong punctuation
    guess only moves a paragraph break.
    """
    punctuated = looks_punctuated(cues)
    return ReflowPolicy(
        dedupe=(
            tier is TrustTier.ASR_PLATFORM and caption_format not in _STRUCTURED_SCROLL
        ),
        punctuated=tier.assume_punctuated if punctuated is None else punctuated,
    )


def speech_end(cue: Cue) -> float:
    """When speech in this cue actually stopped.

    Automatic cue durations overrun the next cue by seconds; word timings, where
    present, say what happened.
    """
    return cue.words[-1].start if cue.words else cue.end


def passage_end(cue: Cue) -> float:
    """When a passage ending with this cue stops, including its last word.

    `speech_end` is when the last word *began*. A `Word` has no duration, so
    its end is bounded by `_WORD_TAIL` and by the cue's own end.
    """
    return min(cue.end, speech_end(cue) + _WORD_TAIL)


def reflow(
    cues: Sequence[Cue], policy: ReflowPolicy | None = None
) -> tuple[Passage, ...]:
    """Reassemble cues into paragraphs."""
    policy = policy or ReflowPolicy()
    working = list(cues)
    if policy.dedupe:
        working = dedupe_rolling_window(working)
    if policy.punctuated:
        # After deduplication: the overlap merge needs whole window cues.
        working = split_at_sentences(working)
    # Last, because it is the only stage that makes text and word timings disagree.
    working = consume_markup(working)

    return tuple(_passage(run) for run in _group(working, policy))


def _group(cues: list[Cue], policy: ReflowPolicy) -> list[list[Cue]]:
    runs: list[list[Cue]] = []
    current: list[Cue] = []
    words = 0
    wanted = False

    for cue in cues:
        if current:
            # Latched: a pause proposes a break that may be taken several cues
            # later, at the next sentence end.
            wanted = wanted or _wants_a_break(current[-1], cue, words, policy)
            # A new speaker always starts a paragraph, even mid-sentence, so
            # one person's words never run into another's.
            if cue.turn or _breaks_here(current[-1], wanted, words, policy):
                runs.append(current)
                current, words, wanted = [], 0, False
        current.append(cue)
        words += len(cue.text.split())

    if current:
        runs.append(current)
    return runs


def _wants_a_break(previous: Cue, cue: Cue, words: int, policy: ReflowPolicy) -> bool:
    """Whether a paragraph ought to end somewhere around here."""
    if cue.start - speech_end(previous) > policy.paragraph_gap:
        # Early in a paragraph a pause is a breath; unpunctuated text has no
        # better signal, so there it always counts.
        return not policy.punctuated or words >= policy.target_words * _PAUSE_FLOOR
    return policy.punctuated and words >= policy.target_words


def _breaks_here(previous: Cue, wanted: bool, words: int, policy: ReflowPolicy) -> bool:
    """Whether the paragraph ends *here*, at this cue boundary."""
    if words >= policy.max_words * _HARD_CEILING:
        return True
    if not policy.punctuated:
        return wanted or words >= policy.max_words
    # Punctuated text takes a proposed break at the next sentence end.
    return (wanted or words >= policy.max_words) and ends_sentence(previous.text)


def _passage(run: list[Cue]) -> Passage:
    return Passage(
        text=" ".join(cue.text for cue in run),
        start=run[0].start,
        end=max(passage_end(run[-1]), run[0].start),
        # A run never spans a turn, so the first cue speaks for all of them.
        speaker=run[0].speaker,
        turn=run[0].turn,
    )
