"""The properties the product actually promises.

Every other test file asks whether a function does what it says. These ask
whether the finished document is worth reading, which is a different question
and the one that went unasked for too long: the suite reached 702 tests and
100% branch coverage while two thirds of the paragraphs in a dense talk began
mid-sentence, with the timestamp pointing at the fragment.

Coverage could not have caught it. Every line ran. What was missing was any
assertion about the *output* — that a stamp lands where a thought starts, that
a paragraph is short enough to check by ear, that the captioner's markup does
not arrive as backslashed noise.

The other half of why it went unnoticed is the fixture: the only automatic
track here is MIT 6.006 from 2011, which has two full stops in thirty-five
thousand characters. On that recording there are no sentences to align to and
nothing to notice. `synthetic-talk.auto.en.json3` exists to be the punctuated,
cased, word-timed automatic track that YouTube produces now.
"""

from __future__ import annotations

from collections import Counter
from itertools import pairwise

import pytest

from conftest import load_caption, load_synthetic
from youtube_transcript_notes.models import (
    Cue,
    Lecture,
    LectureMeta,
    Provenance,
    TrustTier,
    Word,
)
from youtube_transcript_notes.parse import parse_json3, parse_vtt
from youtube_transcript_notes.refine import (
    build_sections,
    ends_sentence,
    policy_for,
    reflow,
)
from youtube_transcript_notes.render import get_renderer

#: p95 of paragraph length, in seconds. The target is forty; this is the band
#: around it that says the target is being hit rather than merely aimed at.
LONGEST_USEFUL_PARAGRAPH = 60.0

#: Paragraphs in the 2011 automatic vtt once deduplicated. Pinned: the stamps
#: are only all checked if they are all there.
OLD_AUTO_VTT_PASSAGES = 61


def lecture_from(payload: str, tier: TrustTier) -> Lecture:
    cues = parse_json3(payload)
    passages = reflow(cues, policy_for(tier, "json3", cues))
    return Lecture(
        meta=LectureMeta(source_id="fixture", title="Fixture"),
        sections=build_sections(passages),
        provenance=Provenance(
            provider="local",
            tier=tier,
            language="en",
            caption_format="json3",
            retrieved_at=None,  # type: ignore[arg-type]
            content_hash="f" * 64,
        ),
    )


@pytest.fixture(scope="module")
def synthetic_cues() -> list[Cue]:
    return parse_json3(load_synthetic("synthetic-talk.auto.en.json3"))


@pytest.fixture(scope="module")
def manual_cues() -> list[Cue]:
    return parse_json3(load_caption("mit6006-lec1.manual.en.json3"))


@pytest.fixture(scope="module")
def old_auto_cues() -> list[Cue]:
    """The 2011 automatic track: unpunctuated, uncased, no sentences at all."""
    return parse_json3(load_caption("mit6006-lec1.auto.en.json3"))


class TestAStampPointsAtTheStartOfAThought:
    """T1 and T2 — the promise the whole design exists to keep."""

    @pytest.mark.parametrize("fixture", ["synthetic_cues", "manual_cues"])
    def test_every_paragraph_begins_after_a_sentence_ended(
        self, fixture: str, request: pytest.FixtureRequest
    ) -> None:
        cues = request.getfixturevalue(fixture)
        passages = reflow(cues, policy_for(TrustTier.MANUAL, "json3", cues))

        # A new speaker is the one break that does not wait for a sentence,
        # because the previous speaker being interrupted mid-thought is a fact
        # about the conversation rather than a failure of paragraphing.
        orphans = [
            passage.text[:60]
            for previous, passage in pairwise(passages)
            if not passage.turn and not ends_sentence(previous.text)
        ]

        assert orphans == []

    def test_a_stamp_is_the_time_of_the_word_that_opens_the_paragraph(
        self, synthetic_cues: list[Cue]
    ) -> None:
        """The property in full: not merely a real cue time, but the time of
        the first word of the sentence being stamped.

        Checked against the word timings in the payload rather than against
        anything the pipeline derived, so a bug that moved both the text and
        the anchor together still fails.
        """
        when: dict[str, list[float]] = {}
        for cue in synthetic_cues:
            for word in cue.words:
                when.setdefault(word.text.strip(), []).append(word.start)

        passages = reflow(
            synthetic_cues, policy_for(TrustTier.ASR_PLATFORM, "json3", synthetic_cues)
        )
        # Not any time the word was said: the time *this* instance was. A
        # paragraph opening on the third "So" is stamped with the third "So".
        seen: Counter[str] = Counter()
        for passage in passages:
            words = passage.text.split()
            opening = words[0]
            if opening not in ("—", "(inaudible)"):  # markup, not a spoken word
                said = when[opening][seen[opening]]
                assert abs(said - passage.start) < 0.001, (
                    f"{passage.start} does not name the moment {opening!r} was said"
                )
            seen.update(words)

    def test_the_anchor_is_still_the_start_of_a_real_cue(
        self, synthetic_cues: list[Cue]
    ) -> None:
        """Cutting a cue in half must produce halves that are themselves cues.

        Contract 2 in `AGENT_GUIDE.md` is that a passage starts where a cue
        started. Sentence splitting keeps it by construction — the pieces are
        dated from word timings the source supplied — and this says so.
        """
        passages = reflow(
            synthetic_cues, policy_for(TrustTier.ASR_PLATFORM, "json3", synthetic_cues)
        )
        moments = {word.start for cue in synthetic_cues for word in cue.words}
        moments |= {cue.start for cue in synthetic_cues}

        assert all(passage.start in moments for passage in passages)


class TestBothEncodingsStampTheSameMoment:
    """The same speech encoded twice — json3 with a timed word per segment,
    vtt by rolling repetition — must not only say the same words but put each
    paragraph at the same second. A link that lands three seconds off in one
    format is a citation nobody can check."""

    def test_every_vtt_stamp_is_the_json3_time_of_its_opening_word(
        self, old_auto_cues: list[Cue]
    ) -> None:
        vtt = parse_vtt(load_caption("mit6006-lec1.auto.en.vtt"))
        passages = reflow(vtt, policy_for(TrustTier.ASR_PLATFORM, "vtt", vtt))

        # json3 times each segment, and a segment can hold "a problem": each
        # word in it is said from that moment.
        timed = [
            (token, word.start)
            for cue in old_auto_cues
            for word in cue.words or (Word(cue.text, cue.start),)
            for token in word.text.split()
        ]
        assert [token for token, _ in timed] == " ".join(
            passage.text for passage in passages
        ).split()

        position = 0
        for passage in passages:
            token, said = timed[position]
            assert abs(passage.start - said) < 0.005, (
                f"vtt stamps {token!r} at {passage.start}, json3 at {said}"
            )
            position += len(passage.text.split())

        assert len(passages) == OLD_AUTO_VTT_PASSAGES


class TestAParagraphIsShortEnoughToCheck:
    """T3 — a citation nobody can verify by ear is not a citation."""

    @pytest.mark.parametrize(
        ("fixture", "tier", "count"),
        [
            ("synthetic_cues", TrustTier.ASR_PLATFORM, 4),
            ("manual_cues", TrustTier.MANUAL, 92),
        ],
    )
    def test_paragraphs_stay_near_the_target(
        self,
        fixture: str,
        tier: TrustTier,
        count: int,
        request: pytest.FixtureRequest,
    ) -> None:
        """The band is a target, so it stays a band; the count beside it is
        pinned, because a paragraphing change that merged or split half the
        paragraphs could still land inside the band."""
        cues = request.getfixturevalue(fixture)
        passages = reflow(cues, policy_for(tier, "json3", cues))

        lengths = sorted(passage.end - passage.start for passage in passages)
        p95 = lengths[int(len(lengths) * 0.95)]

        assert len(passages) == count
        assert p95 <= LONGEST_USEFUL_PARAGRAPH

    def test_a_pause_mid_sentence_does_not_start_a_paragraph(
        self, synthetic_cues: list[Cue]
    ) -> None:
        """The fixture holds a 1.8-second breath in the middle of a sentence.

        Before this, that breath ended the paragraph and the next stamp landed
        on "is true on average" — three words into somebody else's thought.
        """
        passages = reflow(
            synthetic_cues, policy_for(TrustTier.ASR_PLATFORM, "json3", synthetic_cues)
        )

        assert not any(p.text.startswith("is true on average") for p in passages)
        assert any("time. That is true on average" in p.text for p in passages)


class TestTheCaptionersMarkupIsRead:
    """T4 — structure, not backslashed noise."""

    def test_no_markup_survives_into_the_document(
        self, synthetic_cues: list[Cue]
    ) -> None:
        notes = get_renderer("markdown").render(
            lecture_from(
                load_synthetic("synthetic-talk.auto.en.json3"), TrustTier.ASR_PLATFORM
            )
        )

        for residue in (
            r"\[Music\]",
            r"\[Applause\]",
            r"\[INAUDIBLE\]",
            r"\>\>",
            r"\[?",
        ):
            assert residue not in notes

    def test_a_speaker_change_starts_a_new_paragraph(
        self, synthetic_cues: list[Cue]
    ) -> None:
        passages = reflow(
            synthetic_cues, policy_for(TrustTier.ASR_PLATFORM, "json3", synthetic_cues)
        )
        turns = [passage for passage in passages if passage.turn]

        assert len(turns) == 2
        assert turns[0].text.startswith("That matches what we saw")
        assert all(turn.speaker is None for turn in turns)  # `>>` names nobody

    def test_a_run_of_unheard_speech_is_one_note(
        self, synthetic_cues: list[Cue]
    ) -> None:
        passages = reflow(
            synthetic_cues, policy_for(TrustTier.ASR_PLATFORM, "json3", synthetic_cues)
        )
        text = " ".join(passage.text for passage in passages)

        assert "(inaudible) (inaudible)" not in text
        assert text.count("(inaudible)") == 1

    def test_the_captioners_doubt_survives_as_doubt(
        self, synthetic_cues: list[Cue]
    ) -> None:
        passages = reflow(
            synthetic_cues, policy_for(TrustTier.ASR_PLATFORM, "json3", synthetic_cues)
        )
        text = " ".join(passage.text for passage in passages)

        # The guess is kept — it is the captioner's best reading — and marked.
        assert "introsort(?)" in text


class TestTheOldTrackIsUntouched:
    """T5 — unpunctuated captions keep the behaviour measured against them.

    Not a nicety. The 1.0-second pause threshold was measured on this exact
    recording, and every archived lecture with a pre-punctuation automatic
    track still depends on it. A change that improved modern transcripts by
    quietly regrouping old ones would be a change nobody could audit.
    """

    def test_paragraph_count_is_unchanged(self, old_auto_cues: list[Cue]) -> None:
        policy = policy_for(TrustTier.ASR_PLATFORM, "json3", old_auto_cues)

        assert policy.punctuated is False
        assert len(reflow(old_auto_cues, policy)) == 71

    def test_punctuation_is_measured_not_assumed(
        self, old_auto_cues: list[Cue], synthetic_cues: list[Cue]
    ) -> None:
        """Both tracks are `ASR_PLATFORM`; only one of them has sentences.

        This is the assumption that expired. The tier says automatic captions
        are unpunctuated, which was true of the 2011 track and is not true of
        anything YouTube produces now.
        """
        old = policy_for(TrustTier.ASR_PLATFORM, "json3", old_auto_cues)
        new = policy_for(TrustTier.ASR_PLATFORM, "json3", synthetic_cues)

        assert TrustTier.ASR_PLATFORM.assume_punctuated is False
        assert old.punctuated is False
        assert new.punctuated is True

    def test_the_tier_still_decides_deduplication(
        self, synthetic_cues: list[Cue]
    ) -> None:
        """Measured for punctuation, never for deduplication — the two differ
        in what being wrong costs, and only one of them destroys text."""
        assert policy_for(TrustTier.ASR_PLATFORM, "vtt", synthetic_cues).dedupe is True
        assert policy_for(TrustTier.MANUAL, "vtt", synthetic_cues).dedupe is False
