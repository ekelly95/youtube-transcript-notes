"""Refine-stage tests.

The most valuable assertions here are the convergence ones: two independent
encodings of the same speech, put through different policies, must arrive at
the same words. Nothing else checks the deduplication logic as convincingly,
because nothing else knows what the right answer is.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from conftest import load_caption
from youtube_transcript_notes.models import Chapter, Cue, Passage, TrustTier, Word
from youtube_transcript_notes.parse import parse_json3, parse_vtt
from youtube_transcript_notes.refine import (
    ReflowPolicy,
    build_sections,
    consume_markup,
    dedupe_rolling_window,
    overlap_length,
    passage_end,
    policy_for,
    reflow,
    speech_end,
)

#: Words in the lecture, according to both automatic encodings once each has
#: been through its own policy.
AUTO_WORDS = 6841

#: Words according to both human-written encodings, once `refine.annotations`
#: has taken the markup out of the prose. It was 6980 before that stage
#: existed, and every one of the 35 is accounted for: 26 belong to the 25
#: `PROFESSOR:` / `AUDIENCE:` / `ERIK DOMANE:` labels, which are now identity
#: rather than text; 7 are the six `[LAUGHTER]` cues and one `[COUGH]`, which
#: nobody said; and 2 are the collapse of `[? O ?]` into `O(?)`. Pinned exactly
#: for the usual reason — a transcript missing four sentences still looks
#: perfectly fine.
MANUAL_WORDS = 6945


@pytest.fixture(scope="module")
def manual_cues() -> list[Cue]:
    return parse_json3(load_caption("mit6006-lec1.manual.en.json3"))


@pytest.fixture(scope="module")
def manual_vtt_cues() -> list[Cue]:
    return parse_vtt(load_caption("mit6006-lec1.manual.en.vtt"))


@pytest.fixture(scope="module")
def auto_json3_cues() -> list[Cue]:
    return parse_json3(load_caption("mit6006-lec1.auto.en.json3"))


@pytest.fixture(scope="module")
def auto_vtt_cues() -> list[Cue]:
    return parse_vtt(load_caption("mit6006-lec1.auto.en.vtt"))


def words_of(passages: tuple[Passage, ...]) -> list[str]:
    return " ".join(passage.text for passage in passages).split()


class TestConvergence:
    def test_deduped_vtt_recovers_the_json3_word_stream_exactly(
        self, auto_vtt_cues: list[Cue], auto_json3_cues: list[Cue]
    ) -> None:
        # The same speech, encoded two ways: json3 marks scrolling
        # structurally, WebVTT by repeating text. After each has been through
        # its own policy they must agree word for word.
        from_vtt = words_of(
            reflow(auto_vtt_cues, policy_for(TrustTier.ASR_PLATFORM, "vtt"))
        )
        from_json3 = words_of(
            reflow(auto_json3_cues, policy_for(TrustTier.ASR_PLATFORM, "json3"))
        )

        assert from_vtt == from_json3
        assert len(from_vtt) == AUTO_WORDS

    def test_both_human_written_encodings_agree(
        self, manual_cues: list[Cue], manual_vtt_cues: list[Cue]
    ) -> None:
        policy = policy_for(TrustTier.MANUAL, "json3")
        from_json3 = reflow(manual_cues, policy)
        from_vtt = reflow(manual_vtt_cues, policy_for(TrustTier.MANUAL, "vtt"))

        assert [p.text for p in from_vtt] == [p.text for p in from_json3]
        assert len(words_of(from_json3)) == MANUAL_WORDS


class TestNothingIsLost:
    @pytest.mark.parametrize(
        ("fixture", "tier", "fmt"),
        [
            ("manual_cues", TrustTier.MANUAL, "json3"),
            ("manual_vtt_cues", TrustTier.MANUAL, "vtt"),
            ("auto_json3_cues", TrustTier.ASR_PLATFORM, "json3"),
        ],
    )
    def test_no_word_is_dropped_when_not_deduplicating(
        self, fixture: str, tier: TrustTier, fmt: str, request: pytest.FixtureRequest
    ) -> None:
        cues: list[Cue] = request.getfixturevalue(fixture)
        # Caption markup is the one thing allowed to disappear, and it does so
        # under its own accounting — `MANUAL_WORDS` pins the total to the
        # token. What this guards is every stage after it: cutting a cue on a
        # sentence and grouping cues into paragraphs must each be exactly
        # lossless, and a bug in either would leave output that reads fine.
        expected = " ".join(cue.text for cue in consume_markup(cues)).split()

        assert words_of(reflow(cues, policy_for(tier, fmt))) == expected


class TestCitationAnchors:
    """The invariant that makes any of this usable academically."""

    @pytest.mark.parametrize(
        ("fixture", "tier", "fmt"),
        [
            ("manual_cues", TrustTier.MANUAL, "json3"),
            ("auto_json3_cues", TrustTier.ASR_PLATFORM, "json3"),
            ("auto_vtt_cues", TrustTier.ASR_PLATFORM, "vtt"),
        ],
    )
    def test_every_passage_starts_where_a_real_cue_started(
        self, fixture: str, tier: TrustTier, fmt: str, request: pytest.FixtureRequest
    ) -> None:
        cues: list[Cue] = request.getfixturevalue(fixture)
        passages = reflow(cues, policy_for(tier, fmt))
        cue_starts = {cue.start for cue in cues}

        assert passages[0].start == cues[0].start
        assert all(passage.start in cue_starts for passage in passages)

    @pytest.mark.parametrize(
        ("fixture", "tier", "fmt"),
        [
            ("manual_cues", TrustTier.MANUAL, "json3"),
            ("auto_json3_cues", TrustTier.ASR_PLATFORM, "json3"),
            ("auto_vtt_cues", TrustTier.ASR_PLATFORM, "vtt"),
        ],
    )
    def test_passages_run_forwards(
        self, fixture: str, tier: TrustTier, fmt: str, request: pytest.FixtureRequest
    ) -> None:
        passages = reflow(request.getfixturevalue(fixture), policy_for(tier, fmt))

        assert all(p.end >= p.start for p in passages)
        assert all(a.start <= b.start for a, b in pairwise(passages))


class TestPolicySelection:
    @pytest.mark.parametrize(
        ("tier", "fmt", "dedupe"),
        [
            (TrustTier.ASR_PLATFORM, "vtt", True),
            (TrustTier.ASR_PLATFORM, "srt", True),
            (TrustTier.ASR_PLATFORM, "json3", False),
            (TrustTier.MANUAL, "vtt", False),
            (TrustTier.MANUAL, "json3", False),
            (TrustTier.ASR_LOCAL, "vtt", False),
            (TrustTier.TRANSLATED, "vtt", False),
        ],
    )
    def test_deduplication_is_only_for_repeating_tracks(
        self, tier: TrustTier, fmt: str, dedupe: bool
    ) -> None:
        assert policy_for(tier, fmt).dedupe is dedupe

    def test_only_platform_asr_is_treated_as_unpunctuated(self) -> None:
        assert not policy_for(TrustTier.ASR_PLATFORM, "json3").punctuated
        assert policy_for(TrustTier.MANUAL, "json3").punctuated
        assert policy_for(TrustTier.ASR_LOCAL, "json3").punctuated


class TestWhyDeduplicationIsGuarded:
    """Pinning the reason `policy_for` exists at all."""

    def test_deduplication_eats_genuine_repetition(self) -> None:
        # Taken from the real lecture: the speaker says "two by two by two".
        cues = [
            Cue(text="two by two", start=0.0, duration=1.0),
            Cue(text="by two rubik's cube", start=1.0, duration=1.0),
        ]
        merged = " ".join(cue.text for cue in dedupe_rolling_window(cues))

        assert merged == "two by two rubik's cube"  # "by two" lost
        assert merged != "two by two by two rubik's cube"

    def test_which_is_why_a_clean_track_never_gets_deduplicated(
        self, auto_json3_cues: list[Cue]
    ) -> None:
        untouched = reflow(auto_json3_cues, policy_for(TrustTier.ASR_PLATFORM, "json3"))
        damaged = reflow(auto_json3_cues, ReflowPolicy(dedupe=True, punctuated=False))

        assert len(words_of(untouched)) == AUTO_WORDS
        assert len(words_of(damaged)) < AUTO_WORDS


class TestDedupe:
    def test_overlap_length_finds_the_longest_run(self) -> None:
        assert overlap_length(["a", "b", "c"], ["b", "c", "d"]) == 2
        assert overlap_length(["a", "b", "c"], ["x", "y"]) == 0
        assert overlap_length([], ["a"]) == 0

    def test_a_pure_repeat_contributes_no_cue(self) -> None:
        cues = [
            Cue(text="hello world", start=0.0, duration=1.0),
            Cue(text="hello world", start=1.0, duration=0.01),
            Cue(text="hello world again", start=1.01, duration=1.0),
        ]
        kept = dedupe_rolling_window(cues)

        assert [cue.text for cue in kept] == ["hello world", "again"]

    def test_a_surviving_cue_keeps_its_own_start(self) -> None:
        cues = [
            Cue(text="hello world", start=0.0, duration=1.0),
            Cue(text="hello world again", start=5.0, duration=1.0),
        ]
        assert dedupe_rolling_window(cues)[1].start == 5.0

    def test_word_timings_survive_when_they_line_up(self) -> None:
        cues = [
            Cue(text="hello world", start=0.0, duration=1.0),
            Cue(
                text="hello world again",
                start=1.0,
                duration=1.0,
                words=(Word("again", 1.5),),
            ),
        ]
        assert dedupe_rolling_window(cues)[1].words == (Word("again", 1.5),)

    def test_word_timings_are_dropped_when_they_would_be_misaligned(self) -> None:
        # Three timed words but only one survives the trim: keeping them would
        # attach the wrong time to the wrong word.
        cues = [
            Cue(text="hello world", start=0.0, duration=1.0),
            Cue(
                text="hello world again",
                start=1.0,
                duration=1.0,
                words=(Word("hello", 1.0), Word("world", 1.2), Word("again", 1.5)),
            ),
        ]
        assert dedupe_rolling_window(cues)[1].words == ()


class TestSpeechEnd:
    def test_word_timings_win_over_an_overrunning_duration(self) -> None:
        # Automatic cues linger on screen long after the words stop.
        cue = Cue(
            text="hello world",
            start=10.0,
            duration=30.0,
            words=(Word("hello", 10.0), Word("world", 10.5)),
        )
        assert speech_end(cue) == 10.5

    def test_cue_end_is_used_when_there_are_no_word_timings(self) -> None:
        assert speech_end(Cue(text="hi", start=10.0, duration=2.0)) == 12.0


class TestWhereAPassageEnds:
    """`speech_end` answers "when did the last word start"; a passage ends
    when that word finishes. `Word` has no duration, so the difference has to
    be bounded rather than known."""

    def test_a_passage_outlasts_the_start_of_its_final_word(self) -> None:
        cue = Cue(
            text="hello world",
            start=10.0,
            duration=30.0,
            words=(Word("hello", 10.0), Word("world", 10.5)),
        )

        assert speech_end(cue) == 10.5
        assert passage_end(cue) > 10.5

    def test_it_never_runs_past_the_cue_itself(self) -> None:
        """The bound that stops this becoming a guess. Automatic cues overrun
        badly — a median 3.2 s past the last word on the measured lecture —
        so `cue.end` is a ceiling, not an answer."""
        cue = Cue(
            text="hello world",
            start=10.0,
            duration=0.6,
            words=(Word("hello", 10.0), Word("world", 10.5)),
        )

        assert passage_end(cue) == cue.end == 10.6

    def test_a_cue_without_word_timings_is_unchanged(self) -> None:
        cue = Cue(text="hi", start=10.0, duration=2.0)

        assert passage_end(cue) == speech_end(cue) == 12.0

    def test_gap_detection_still_uses_the_start_of_the_last_word(self) -> None:
        """`speech_end` is left alone deliberately: the 1.0 s paragraph gap was
        measured against its current definition, and the break counts on the
        fixtures are pinned to it.

        Unpunctuated, because that is what this text is — there is no sentence
        here for a pause to defer to, and it is the path where a pause on its
        own still ends a paragraph.
        """
        cues = [
            Cue(
                text="one two",
                start=0.0,
                duration=10.0,
                words=(Word("one", 0.0), Word("two", 0.5)),
            ),
            Cue(text="three", start=2.0, duration=1.0),
        ]
        policy = ReflowPolicy(paragraph_gap=1.0, punctuated=False)

        # 2.0 - 0.5 is over the gap, so this breaks; 2.0 - 1.5 would not.
        assert len(reflow(cues, policy)) == 2

    def test_an_excerpt_keeps_a_passage_that_overlaps_by_its_last_word(
        self,
    ) -> None:
        """The reason this matters. Ending a passage one word early drops it
        from `between()` when the window opens during that word."""
        cues = [
            Cue(
                text="the final word",
                start=0.0,
                duration=4.0,
                words=(Word("the", 0.0), Word("final", 1.0), Word("word", 2.0)),
            )
        ]
        passage = reflow(cues)[0]

        assert passage.end == 3.0  # 2.0 + the measured word tail
        assert passage.start < 2.5 < passage.end


class TestParagraphing:
    def test_a_pause_starts_a_new_paragraph(self) -> None:
        cues = [
            Cue(text="first thought", start=0.0, duration=1.0),
            Cue(text="still going", start=1.0, duration=1.0),
            Cue(text="new thought", start=5.0, duration=1.0),
        ]
        policy = ReflowPolicy(paragraph_gap=1.0, punctuated=False)
        passages = reflow(cues, policy)

        assert [p.text for p in passages] == [
            "first thought still going",
            "new thought",
        ]

    def test_no_pause_means_no_break(self) -> None:
        cues = [
            Cue(text="one", start=0.0, duration=1.0),
            Cue(text="two", start=1.0, duration=1.0),
        ]
        assert len(reflow(cues, ReflowPolicy(paragraph_gap=1.0))) == 1

    def test_punctuated_text_waits_for_the_end_of_a_sentence(self) -> None:
        cues = [
            Cue(text="one two three", start=0.0, duration=1.0),
            Cue(text="four five six", start=1.0, duration=1.0),
            Cue(text="seven eight nine.", start=2.0, duration=1.0),
            Cue(text="ten eleven", start=3.0, duration=1.0),
        ]
        passages = reflow(cues, ReflowPolicy(max_words=4, punctuated=True))

        # Over length after the first cue, but it holds on until the full stop.
        assert [p.text for p in passages] == [
            "one two three four five six seven eight nine.",
            "ten eleven",
        ]

    def test_unpunctuated_text_breaks_as_soon_as_it_is_too_long(self) -> None:
        cues = [
            Cue(text="one two three", start=0.0, duration=1.0),
            Cue(text="four five six", start=1.0, duration=1.0),
            Cue(text="seven eight nine", start=2.0, duration=1.0),
        ]
        passages = reflow(cues, ReflowPolicy(max_words=2, punctuated=False))

        assert [p.text for p in passages] == [
            "one two three",
            "four five six",
            "seven eight nine",
        ]

    def test_punctuated_text_that_never_ends_a_sentence_still_breaks(self) -> None:
        """The mislabelled-track case, and why `max_words` needs a ceiling.

        A local file's tier comes from its filename, so an automatic track not
        marked `.auto.` is reflowed with `punctuated=True` over text that has
        no sentence endings at all. `_ends_sentence` is then never true, so
        without a hard ceiling these six cues would come back as one 18-word
        paragraph — and a real lecture as one paragraph of several thousand.

        Nine words rather than eight because the running count is tested
        before the incoming cue joins the paragraph, so a paragraph overshoots
        the ceiling by at most one cue. Bounded is the property that matters.
        """
        cues = [
            Cue(text="one two three", start=float(n), duration=1.0) for n in range(6)
        ]
        passages = reflow(cues, ReflowPolicy(max_words=4, punctuated=True))

        assert [len(p.text.split()) for p in passages] == [9, 9]

    def test_the_ceiling_does_not_disturb_text_that_does_end_sentences(self) -> None:
        """Correctly-labelled captions never reach it, so nothing moves."""
        cues = [
            Cue(text="one two three", start=0.0, duration=1.0),
            Cue(text="four five six", start=1.0, duration=1.0),
            Cue(text="seven eight nine.", start=2.0, duration=1.0),
            Cue(text="ten eleven", start=3.0, duration=1.0),
        ]
        passages = reflow(cues, ReflowPolicy(max_words=4, punctuated=True))

        assert [p.text for p in passages] == [
            "one two three four five six seven eight nine.",
            "ten eleven",
        ]

    def test_a_closing_quote_does_not_hide_the_full_stop(self) -> None:
        cues = [
            Cue(text='he said "yes."', start=0.0, duration=1.0),
            Cue(text="then left", start=1.0, duration=1.0),
        ]
        passages = reflow(cues, ReflowPolicy(max_words=1, punctuated=True))

        assert len(passages) == 2

    def test_no_cues_makes_no_passages(self) -> None:
        assert reflow([]) == ()

    def test_the_default_policy_is_usable_without_arguments(self) -> None:
        assert reflow([Cue(text="hi", start=0.0, duration=1.0)])[0].text == "hi"


class TestSections:
    def test_no_chapters_gives_one_untitled_section(self) -> None:
        passages = [Passage(text="a", start=0.0, end=1.0)]
        (section,) = build_sections(passages)

        assert section.title is None
        assert section.passages == tuple(passages)

    def test_no_passages_gives_no_sections(self) -> None:
        assert build_sections([], [Chapter(title="x", start=0.0)]) == ()

    def test_passages_are_grouped_under_their_chapter(self) -> None:
        passages = [
            Passage(text="intro", start=0.0, end=5.0),
            Passage(text="more intro", start=10.0, end=15.0),
            Passage(text="part two", start=100.0, end=105.0),
        ]
        chapters = [Chapter(title="One", start=0.0), Chapter(title="Two", start=60.0)]
        sections = build_sections(passages, chapters)

        assert [s.title for s in sections] == ["One", "Two"]
        assert [len(s.passages) for s in sections] == [2, 1]

    def test_chapters_are_sorted_before_use(self) -> None:
        passages = [Passage(text="late", start=100.0, end=105.0)]
        chapters = [Chapter(title="Two", start=60.0), Chapter(title="One", start=0.0)]

        assert build_sections(passages, chapters)[0].title == "Two"

    def test_a_passage_before_the_first_chapter_gets_an_untitled_section(self) -> None:
        passages = [
            Passage(text="cold open", start=0.0, end=5.0),
            Passage(text="chapter one", start=100.0, end=105.0),
        ]
        chapters = [Chapter(title="One", start=60.0)]
        sections = build_sections(passages, chapters)

        assert [s.title for s in sections] == [None, "One"]
        assert sections[0].start == 0.0

    def test_a_chapter_with_no_passages_is_not_emitted(self) -> None:
        passages = [Passage(text="only this", start=0.0, end=5.0)]
        chapters = [
            Chapter(title="One", start=0.0),
            Chapter(title="Empty", start=999.0),
        ]

        assert [s.title for s in build_sections(passages, chapters)] == ["One"]
