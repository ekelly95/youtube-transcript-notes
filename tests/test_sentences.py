"""Cutting cues where sentences end.

The cut only happens where the source supplied word timings, because the
second half needs a start and the only honest one is the timing of the word
that opens it. Most of what follows is about *not* cutting: a full stop that
ends an abbreviation, an initial, or a decimal point is not the end of a
thought, and a cue cut there would put a timestamp in the middle of one.
"""

from __future__ import annotations

import pytest

from youtube_transcript_notes.models import Cue, Word
from youtube_transcript_notes.refine import (
    cut_at,
    ends_sentence,
    looks_punctuated,
    split_at_sentences,
)


def timed(text: str, start: float = 0.0, step: float = 0.5, duration: float = 0.0):
    """A cue whose words are evenly spaced, which is all these tests need."""
    words = text.split()
    return Cue(
        text=text,
        start=start,
        duration=duration or len(words) * step,
        words=tuple(
            Word(word, start + index * step) for index, word in enumerate(words)
        ),
    )


class TestWhereACueIsCut:
    def test_a_cue_carrying_two_sentences_becomes_two_cues(self) -> None:
        cue = timed("to an end. Here are those", start=10.0)

        pieces = split_at_sentences([cue])

        assert [piece.text for piece in pieces] == ["to an end.", "Here are those"]
        assert pieces[0].start == 10.0
        assert pieces[1].start == 11.5  # when "Here" was said

    def test_the_pieces_still_add_up_to_the_cue(self) -> None:
        cue = timed("one. Two. Three.", start=4.0, step=1.0)

        pieces = split_at_sentences([cue])

        assert " ".join(piece.text for piece in pieces) == cue.text
        assert pieces[0].start == cue.start
        assert pieces[-1].end == cue.end

    def test_a_cue_with_no_word_timings_is_never_cut(self) -> None:
        """There is no honest start for the second half, and dividing the
        duration by the word count would invent one that later code could not
        tell from a measured timing."""
        cue = Cue(text="to an end. Here are those", start=10.0, duration=3.0)

        assert split_at_sentences([cue]) == [cue]

    def test_a_cue_whose_words_have_drifted_is_never_cut(self) -> None:
        cue = Cue(
            text="to an end. Here we are",
            start=0.0,
            duration=3.0,
            words=(Word("to", 0.0), Word("an end.", 0.5)),
        )

        assert split_at_sentences([cue]) == [cue]

    def test_a_word_timed_before_its_cue_cannot_reorder_the_pieces(self) -> None:
        """Parsers clamp word offsets at zero, so one can land before the cue
        that carries it. Letting that through would put a piece's start before
        its predecessor's."""
        cue = Cue(
            text="done. Next",
            start=8.0,
            duration=2.0,
            words=(Word("done.", 8.0), Word("Next", 0.0)),
        )

        pieces = split_at_sentences([cue])

        assert [piece.start for piece in pieces] == [8.0, 8.0]

    def test_an_index_outside_the_cue_is_ignored(self) -> None:
        cue = timed("one two three")

        assert cut_at(cue, [0, 3, 99]) == [cue]


class TestWhatIsNotASentenceEnding:
    @pytest.mark.parametrize(
        "text",
        [
            "we tested e.g. Python and Ruby",
            "ask Dr. Smith about it",
            "written by J. Smith originally",
            "born in the U.S. Later he moved",
            "it cost 3.5 million dollars",
            "compare this vs. that one",
        ],
    )
    def test_these_do_not_cut(self, text: str) -> None:
        assert split_at_sentences([timed(text)]) == [timed(text)]

    def test_a_lower_case_word_does_not_open_a_sentence(self) -> None:
        assert split_at_sentences([timed("that is all. and then more")]) == [
            timed("that is all. and then more")
        ]

    def test_a_digit_can_open_a_sentence(self) -> None:
        pieces = split_at_sentences([timed("that is all. 20 bugs remained")])

        assert [piece.text for piece in pieces] == ["that is all.", "20 bugs remained"]

    def test_a_quoted_sentence_still_ends(self) -> None:
        pieces = split_at_sentences([timed('he said "stop." Then he left')])

        assert [piece.text for piece in pieces] == ['he said "stop."', "Then he left"]

    def test_an_opening_quote_does_not_hide_the_next_sentence(self) -> None:
        pieces = split_at_sentences([timed('it ended. "Then we began again')])

        assert len(pieces) == 2


class TestEndsSentence:
    @pytest.mark.parametrize("text", ["done.", "really?", "stop!", 'said "stop."'])
    def test_these_end_one(self, text: str) -> None:
        assert ends_sentence(text)

    @pytest.mark.parametrize("text", ["and then", "a comma,", "an em dash --"])
    def test_these_do_not(self, text: str) -> None:
        assert not ends_sentence(text)


class TestMeasuringPunctuation:
    def test_too_little_text_to_judge_says_so(self) -> None:
        assert looks_punctuated([timed("hello there. How are you")]) is None

    def test_prose_is_punctuated(self) -> None:
        cues = [timed("this is a sentence. And here is another one.")] * 30

        assert looks_punctuated(cues) is True

    def test_a_stream_of_words_is_not(self) -> None:
        cues = [timed("and then we take the thing and we move it over here")] * 30

        assert looks_punctuated(cues) is False
