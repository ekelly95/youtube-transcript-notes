"""Turning caption cues into readable paragraphs.

Caption cues are one to three seconds long and break wherever the text
happened to fill a line. Nobody reads that. This stage reassembles them into
paragraphs of a chosen length, ending each one where a sentence ends.

Both halves of that sentence are recent, and the history matters because the
old behaviour is still in here as the fallback path. Paragraphs used to end
wherever the speaker paused for a second, whatever the text was doing at the
time, and the result was that two thirds of the paragraphs in a dense talk
began on the orphaned tail of the previous sentence — with the timestamp
pointing at the fragment. A pause is a fact about breathing, not about prose.

So a pause now *proposes* a break and a sentence end *takes* it. The pause
threshold below was measured and is unchanged: on MIT 6.006 Lecture 1 the gaps
between human-written cues are bimodal — almost every gap is zero, the rest
over two seconds — so anything between 0.3 s and 2 s yields the same 51 breaks,
and spot-checking those, they land on genuine topic transitions.

Text with no sentence endings has nothing to take a proposal, so it keeps the
original rules exactly: pause breaks, with `max_words` as the valve. That path
is not vestigial — platform captions were unpunctuated for years and archived
lectures still carry those tracks.

The one invariant this stage must not break: every passage starts at the start
of the first cue that fed it. Merging, trimming and regrouping are all fair
game; losing the anchor that makes a quote citable is not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..models import Cue, Passage, TrustTier
from .annotations import consume_markup
from .dedupe import dedupe_rolling_window
from .sentences import ends_sentence, looks_punctuated, split_at_sentences

__all__ = ["ReflowPolicy", "passage_end", "policy_for", "reflow", "speech_end"]

#: How long the last word of a passage may still be running after the moment
#: it started. On the measured lecture the interval between consecutive words
#: has a median of 0.27 s and a ninetieth percentile of exactly 1.0 s, so a
#: word still being spoken a second later is the exception rather than the
#: rule. Used only to extend a passage's end, and never past the cue's own.
_WORD_TAIL = 1.0

#: Formats that mark caption scrolling structurally rather than by repeating
#: text. Tracks in these formats never need deduplication, and running it over
#: them destroys genuine repetition — see `refine.dedupe`.
_STRUCTURED_SCROLL = frozenset({"json3"})

#: How far into a paragraph a pause starts counting as a paragraph break
#: rather than a breath, as a fraction of `target_words`. Without a floor the
#: pause is not a tie-breaker but an independent trigger, and on speech that
#: pauses every few seconds it decides paragraph length on its own — which is
#: how a forty-second target used to produce fifteen-second paragraphs.
_PAUSE_FLOOR = 0.75

#: The multiple of `max_words` at which a paragraph breaks whatever the text
#: looks like. Punctuated text is allowed to overrun while it waits for a
#: sentence to end — but text that contains no sentence endings never offers
#: one, so without a ceiling it would never break on length at all and
#: `max_words` would not be the safety valve it claims to be.
#:
#: That combination is reachable, not hypothetical. A local file's tier comes
#: from its filename, so an automatic track not marked ``.auto.`` is reflowed
#: as though it were human-written prose: unpunctuated text, punctuated
#: policy. Twice the target is far past anything correctly-labelled captions
#: reach — the measured average paragraph is about 135 words — so this changes
#: nothing for a track whose tier is right.
_HARD_CEILING = 2


@dataclass(frozen=True)
class ReflowPolicy:
    """How to turn one particular track's cues into paragraphs."""

    paragraph_gap: float = 1.0
    """Seconds of silence that propose a paragraph break."""

    target_words: int = 90
    """How long a paragraph should be, for text that has sentences to end on.

    Ninety words is about forty seconds at lecture pace, and the paragraph
    actually lands a little longer because it runs on to the next sentence end
    and overshoots by up to one cue. Forty seconds is a readable paragraph and
    a citation precise enough to check by ear: click the stamp and the sentence
    you are quoting is the one being said."""

    max_words: int = 250
    """Safety valve for text that offers no sentence to end on. Not the normal
    path — `target_words` is. Past this a paragraph takes the first ending it
    is offered; past `_HARD_CEILING` times it, it breaks regardless, so this is
    a genuine bound and not only a hint."""

    dedupe: bool = False
    """Whether this track repeats text to encode a scrolling window."""

    punctuated: bool = True
    """Whether the text has sentence punctuation. When it does, paragraphs are
    cut to `target_words` and ended on sentences; when it does not, there is
    nothing to end on and pauses do the work alone."""


def policy_for(
    tier: TrustTier, caption_format: str, cues: Sequence[Cue] = ()
) -> ReflowPolicy:
    """Choose a policy for one track.

    Deduplication is decided from what the track *is*, once, rather than being
    detected per track. Detection was tried and does not work: no content-based
    threshold both recovers a repeating track and leaves a clean one intact,
    and getting it wrong eats words that cannot be recovered.

    Punctuation is decided the other way round, from the text, because the
    tier's answer went stale. `TrustTier.assume_punctuated` says platform
    automatic captions have no sentence punctuation, which was true when it was
    written and is not true now — YouTube's recogniser punctuates and cases its
    output, and every recent automatic transcript is full prose. A tier flag
    cannot notice that; counting full stops can. The tier remains the fallback
    for a track too short to measure.

    The asymmetry is not inconsistency. The two decisions differ in what being
    wrong costs: a bad deduplication guess destroys text, while a bad
    punctuation guess only means a paragraph breaks on length rather than on a
    sentence, still bounded by `max_words`.
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

    Automatic cue durations overrun into the following cue, because the
    display window lingers after the words finish — on the measured lecture
    the median cue "ends" 2.8 seconds after the next one starts. Word timings,
    where present, say what actually happened.
    """
    return cue.words[-1].start if cue.words else cue.end


def passage_end(cue: Cue) -> float:
    """When a passage ending with this cue stops — including its last word.

    `speech_end` answers a different question: when the last word *began*.
    That is the right conservative input to gap detection, and the wrong
    answer for an excerpt, because it puts the end of a passage one word
    before the passage actually ends.

    `Word` carries a start and no duration, so the end of that final word can
    only be bounded, not known: at most a word's length later, and never past
    where the cue itself claims to end. The bound is measured — see
    `_WORD_TAIL` — not guessed, and it can only ever move an end later.
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
        # After deduplication, never before: the overlap merge reads a rolling
        # window's token stream, and cutting cues in half first would leave it
        # matching against fragments of the window it is trying to undo.
        working = split_at_sentences(working)
    # Last, because it is the only stage that makes a cue's text and its word
    # timings disagree. Everything that needs them aligned has already run.
    working = consume_markup(working)

    return tuple(_passage(run) for run in _group(working, policy))


def _group(cues: list[Cue], policy: ReflowPolicy) -> list[list[Cue]]:
    runs: list[list[Cue]] = []
    current: list[Cue] = []
    words = 0
    wanted = False

    for cue in cues:
        if current:
            # Latched, not recomputed. A pause proposes a break at the moment
            # it happens; the sentence it is waiting for may be several cues
            # away, and forgetting the proposal in between would lose every
            # break that did not land on a boundary by luck.
            wanted = wanted or _wants_a_break(current[-1], cue, words, policy)
            # A new speaker is a paragraph whatever the prose is doing. This
            # one is not deferred to a sentence end: the previous speaker
            # stopping mid-sentence is a fact about the conversation, and
            # holding their interruption open until they finish a thought they
            # never finished would put one person's words in another's mouth.
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
        # A pause. Early in a paragraph it is a breath rather than a boundary,
        # and breaking on it is what used to put a timestamp on a sentence
        # fragment. Unpunctuated text has no sentence to prefer instead, so
        # there the pause is still the whole of the signal.
        return not policy.punctuated or words >= policy.target_words * _PAUSE_FLOOR
    return policy.punctuated and words >= policy.target_words


def _breaks_here(previous: Cue, wanted: bool, words: int, policy: ReflowPolicy) -> bool:
    """Whether the paragraph ends *here*, at this cue boundary."""
    if words >= policy.max_words * _HARD_CEILING:
        return True
    if not policy.punctuated:
        return wanted or words >= policy.max_words
    # Punctuated text takes a proposed break at the first sentence end after
    # it, so the next paragraph — and the timestamp on it — starts where a
    # thought starts.
    return (wanted or words >= policy.max_words) and ends_sentence(previous.text)


def _passage(run: list[Cue]) -> Passage:
    return Passage(
        text=" ".join(cue.text for cue in run),
        start=run[0].start,
        end=max(passage_end(run[-1]), run[0].start),
        # The run never spans a turn, so the first cue speaks for all of them.
        speaker=run[0].speaker,
        turn=run[0].turn,
    )
