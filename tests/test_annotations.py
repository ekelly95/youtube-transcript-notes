"""Caption markup read as structure.

What a captioner writes in brackets is data: `[MUSIC]` says nobody is
speaking, `[INAUDIBLE]` says they could not hear, `[? maybe ?]` says they heard
something and are unsure, `>>` says the speaker changed, `PROFESSOR:` says who
it is. All five used to arrive in the finished note as literal text, and — as
`render.escape` neutralises brackets without inspecting them — as backslashed
literal text. The tests below are about consuming them instead, and about the
two places where consuming them must *not* invent anything.
"""

from __future__ import annotations

import pytest

from youtube_transcript_notes.models import Cue, Word
from youtube_transcript_notes.refine import consume_markup


def cue(text: str, start: float = 0.0, duration: float = 2.0) -> Cue:
    return Cue(text=text, start=start, duration=duration)


def timed(text: str, start: float = 0.0, step: float = 0.5) -> Cue:
    words = text.split()
    return Cue(
        text=text,
        start=start,
        duration=len(words) * step,
        words=tuple(
            Word(word, start + index * step) for index, word in enumerate(words)
        ),
    )


class TestNonSpeech:
    @pytest.mark.parametrize(
        "marker", ["[Music]", "[MUSIC]", "[Applause]", "[LAUGHTER]", "[ cough ]"]
    )
    def test_a_cue_that_is_only_a_noise_disappears(self, marker: str) -> None:
        assert consume_markup([cue(marker)]) == []

    def test_a_noise_inside_speech_is_taken_out_of_it(self) -> None:
        (kept,) = consume_markup([cue("and then [LAUGHTER] we moved on")])

        assert kept.text == "and then we moved on"

    def test_an_unrecognised_bracket_is_left_exactly_as_published(self) -> None:
        """Parsers are faithful and this stage only consumes what it knows.
        Anything else is still a stranger's text, and `render.escape` is what
        makes it inert."""
        (kept,) = consume_markup([cue("the [foo bar] method")])

        assert kept.text == "the [foo bar] method"


class TestUnheardSpeech:
    def test_it_is_kept_as_a_note_rather_than_a_bracket(self) -> None:
        (kept,) = consume_markup([cue("so [INAUDIBLE] happened")])

        assert kept.text == "so (inaudible) happened"

    def test_a_run_within_one_cue_becomes_one_note(self) -> None:
        (kept,) = consume_markup([cue("[INAUDIBLE] [INAUDIBLE] [INAUDIBLE] the fix")])

        assert kept.text == "(inaudible) the fix"

    def test_a_run_spread_across_cues_becomes_one_note(self) -> None:
        kept = consume_markup(
            [cue("we tried [INAUDIBLE]"), cue("[INAUDIBLE]"), cue("[INAUDIBLE]")]
        )

        assert [k.text for k in kept] == ["we tried (inaudible)"]

    def test_a_separated_run_is_two_notes(self) -> None:
        kept = consume_markup(
            [cue("we tried [INAUDIBLE]"), cue("and then"), cue("[INAUDIBLE]")]
        )

        assert [k.text for k in kept] == [
            "we tried (inaudible)",
            "and then",
            "(inaudible)",
        ]


class TestCaptionerDoubt:
    def test_the_guess_is_kept_and_marked(self) -> None:
        (kept,) = consume_markup([cue("it is [? a cure. ?] really")])

        assert kept.text == "it is a cure.(?) really"

    def test_doubt_about_nothing_is_still_doubt(self) -> None:
        (kept,) = consume_markup([cue("and then [? ?] happened")])

        assert kept.text == "and then (?) happened"


class TestSpeakers:
    def test_a_label_becomes_identity_and_leaves_the_prose(self) -> None:
        (kept,) = consume_markup([cue("PROFESSOR: Hi, everyone.")])

        assert kept.text == "Hi, everyone."
        assert kept.speaker == "PROFESSOR"
        assert kept.turn is True

    def test_a_label_carries_forward_until_it_changes(self) -> None:
        kept = consume_markup(
            [
                cue("GRAHAM NEUBIG: Hi."),
                cue("Today we will look at agents."),
                cue("AUDIENCE: A question."),
                cue("Is that always true?"),
            ]
        )

        assert [k.speaker for k in kept] == [
            "GRAHAM NEUBIG",
            "GRAHAM NEUBIG",
            "AUDIENCE",
            "AUDIENCE",
        ]
        assert [k.turn for k in kept] == [True, False, True, False]

    def test_an_ordinary_sentence_with_a_colon_is_not_a_label(self) -> None:
        (kept,) = consume_markup([cue("So here is the thing: it does not scale.")])

        assert kept.speaker is None
        assert kept.text.startswith("So here is the thing:")

    def test_a_turn_marker_says_somebody_else_without_saying_who(self) -> None:
        kept = consume_markup([cue("PROFESSOR: Yes."), cue(">> I disagree.")])

        assert [k.text for k in kept] == ["Yes.", "I disagree."]
        # Not "PROFESSOR" — keeping the name would put one person's words in
        # another's mouth — and not "Speaker 2" either, which the captions
        # never said.
        assert [k.speaker for k in kept] == ["PROFESSOR", None]
        assert [k.turn for k in kept] == [True, True]

    def test_a_turn_part_way_through_a_cue_is_cut_at_the_word_it_happened_on(
        self,
    ) -> None:
        kept = consume_markup([timed("I agree. >> But I do not.", step=1.0)])

        assert [k.text for k in kept] == ["I agree.", "But I do not."]
        assert [k.turn for k in kept] == [False, True]
        assert kept[1].start == 3.0  # when "But" was said, not when ">>" was

    def test_a_mid_cue_turn_without_word_timings_keeps_the_cue_whole(self) -> None:
        """Nothing to date the second half with, so the glyph goes and the
        turn is not claimed. One cue early would be a guess."""
        (kept,) = consume_markup([cue("I agree. >> But I do not.")])

        assert kept.text == "I agree. But I do not."
        assert kept.turn is False


class TestWordTimingsSurvive:
    def test_a_leading_marker_moves_the_start_to_the_first_real_word(self) -> None:
        (kept,) = consume_markup([timed(">> That matches", start=4.0, step=0.5)])

        assert kept.text == "That matches"
        assert kept.start == 4.5
        assert [word.text for word in kept.words] == ["That", "matches"]

    def test_markup_removed_from_the_middle_drops_the_timings(self) -> None:
        """The remaining words cannot be matched to their starts by position,
        and one word's start standing in for another's is exactly the
        fabrication the rest of this package refuses to make."""
        (kept,) = consume_markup([timed("we tried [LAUGHTER] again", step=0.5)])

        assert kept.text == "we tried again"
        assert kept.words == ()
        assert kept.start == 0.0
