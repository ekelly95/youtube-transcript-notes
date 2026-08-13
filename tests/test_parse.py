"""Parser tests, driven by real captured captions.

The counts below are not arbitrary — they are what MIT 6.006 Lecture 1
actually contains. Pinning them means a parser change that silently loses or
invents cues fails immediately, which is the failure mode that matters most:
a transcript missing four sentences still looks perfectly fine.
"""

from __future__ import annotations

import json
from itertools import pairwise

import pytest

from conftest import load_caption
from youtube_transcript_notes.errors import MalformedCaptions, UnknownCaptionFormat
from youtube_transcript_notes.models import Cue
from youtube_transcript_notes.parse import (
    parse_captions,
    parse_json3,
    parse_srt,
    parse_vtt,
)

#: The lecture has 978 human-written cues, in every format.
MANUAL_CUES = 978

#: Automatic json3: 1866 events, of which 932 are scroll padding and one is a
#: window definition, leaving 933 real cues.
AUTO_CUES = 933

#: Automatic WebVTT encodes the same 933 cues plus a "flush" cue before each,
#: which is the rolling-window repetition left for the refine stage to remove.
AUTO_VTT_CUES = 1851

#: Both automatic formats independently agree on how many cues carry word
#: timings, which is a useful cross-check that neither parser is inventing them.
AUTO_WORD_CUES = 928


@pytest.fixture(scope="module")
def manual_json3() -> list[Cue]:
    return parse_json3(load_caption("mit6006-lec1.manual.en.json3"))


@pytest.fixture(scope="module")
def manual_vtt() -> list[Cue]:
    return parse_vtt(load_caption("mit6006-lec1.manual.en.vtt"))


@pytest.fixture(scope="module")
def manual_srt() -> list[Cue]:
    return parse_srt(load_caption("mit6006-lec1.manual.en.srt"))


@pytest.fixture(scope="module")
def auto_json3() -> list[Cue]:
    return parse_json3(load_caption("mit6006-lec1.auto.en.json3"))


@pytest.fixture(scope="module")
def auto_vtt() -> list[Cue]:
    return parse_vtt(load_caption("mit6006-lec1.auto.en.vtt"))


class TestCrossFormatAgreement:
    """The same lecture, parsed three ways, must say the same thing.

    This is the strongest test here: any format-specific mistake shows up as
    disagreement, without anyone having to guess what the right answer was.
    """

    def test_all_three_formats_find_the_same_cues(
        self, manual_json3: list[Cue], manual_vtt: list[Cue], manual_srt: list[Cue]
    ) -> None:
        assert len(manual_json3) == len(manual_vtt) == len(manual_srt) == MANUAL_CUES

    def test_all_three_formats_agree_on_text(
        self, manual_json3: list[Cue], manual_vtt: list[Cue], manual_srt: list[Cue]
    ) -> None:
        assert [c.text for c in manual_vtt] == [c.text for c in manual_json3]
        assert [c.text for c in manual_srt] == [c.text for c in manual_json3]

    def test_all_three_formats_agree_on_start_times(
        self, manual_json3: list[Cue], manual_vtt: list[Cue], manual_srt: list[Cue]
    ) -> None:
        assert [c.start for c in manual_vtt] == [c.start for c in manual_json3]
        assert [c.start for c in manual_srt] == [c.start for c in manual_json3]

    def test_all_three_formats_agree_on_durations(
        self, manual_json3: list[Cue], manual_vtt: list[Cue], manual_srt: list[Cue]
    ) -> None:
        # Approximate, not exact: json3 states the duration in milliseconds
        # while the text formats state an end time, so the two arrive at the
        # same number by different arithmetic.
        expected = pytest.approx([c.duration for c in manual_json3], abs=1e-6)
        assert [c.duration for c in manual_vtt] == expected
        assert [c.duration for c in manual_srt] == expected


class TestStructuralInvariants:
    @pytest.mark.parametrize(
        "fixture",
        ["manual_json3", "manual_vtt", "manual_srt", "auto_json3", "auto_vtt"],
    )
    def test_every_parse_is_sane(
        self, fixture: str, request: pytest.FixtureRequest
    ) -> None:
        cues: list[Cue] = request.getfixturevalue(fixture)

        assert cues, "parser produced nothing"
        assert all(cue.text.strip() for cue in cues), "empty cue survived"
        assert all(cue.duration >= 0 for cue in cues), "negative duration"
        assert all(earlier.start <= later.start for earlier, later in pairwise(cues)), (
            "cues are not in order"
        )

    @pytest.mark.parametrize(
        "fixture",
        ["manual_json3", "manual_vtt", "manual_srt", "auto_json3", "auto_vtt"],
    )
    def test_no_display_line_breaks_survive(
        self, fixture: str, request: pytest.FixtureRequest
    ) -> None:
        # Caption formats wrap text to fit the screen. That is presentation,
        # and carrying it downstream would make every consumer strip it.
        cues: list[Cue] = request.getfixturevalue(fixture)
        assert not any("\n" in cue.text for cue in cues)


class TestJson3:
    def test_scroll_padding_is_filtered_out(self, auto_json3: list[Cue]) -> None:
        raw = json.loads(load_caption("mit6006-lec1.auto.en.json3"))

        assert len(raw["events"]) == 1866
        assert len(auto_json3) == AUTO_CUES

    def test_the_window_definition_event_is_skipped(self) -> None:
        # It has no `segs` at all, so it is not a cue.
        payload = json.dumps(
            {
                "events": [
                    {"tStartMs": 0, "dDurationMs": 100, "id": 1, "wpWinPosId": 1},
                    {"tStartMs": 10, "dDurationMs": 90, "segs": [{"utf8": "hello"}]},
                ]
            }
        )
        assert [cue.text for cue in parse_json3(payload)] == ["hello"]

    def test_append_events_are_skipped(self) -> None:
        payload = json.dumps(
            {
                "events": [
                    {"tStartMs": 10, "dDurationMs": 90, "segs": [{"utf8": "hello"}]},
                    {"tStartMs": 20, "aAppend": 1, "segs": [{"utf8": "\n"}]},
                ]
            }
        )
        assert len(parse_json3(payload)) == 1

    def test_manual_captions_get_no_invented_word_timings(
        self, manual_json3: list[Cue]
    ) -> None:
        # The source supplies none. Dividing the duration evenly would produce
        # numbers that later code could not distinguish from real ones.
        assert not any(cue.words for cue in manual_json3)

    def test_automatic_captions_carry_word_timings(self, auto_json3: list[Cue]) -> None:
        assert sum(1 for cue in auto_json3 if cue.words) == AUTO_WORD_CUES

    def test_word_timings_are_ordered_and_inside_their_cue(
        self, auto_json3: list[Cue]
    ) -> None:
        for cue in auto_json3:
            starts = [word.start for word in cue.words]
            assert starts == sorted(starts)
            assert all(word.start >= cue.start for word in cue.words)

    def test_words_reconstruct_the_cue_text(self, auto_json3: list[Cue]) -> None:
        cue = next(cue for cue in auto_json3 if cue.words)
        assert " ".join(word.text for word in cue.words) == cue.text

    def test_a_whitespace_only_event_is_not_a_cue(self) -> None:
        payload = json.dumps(
            {
                "events": [
                    {"tStartMs": 0, "dDurationMs": 10, "segs": [{"utf8": "   \n "}]},
                    {"tStartMs": 10, "dDurationMs": 90, "segs": [{"utf8": "hello"}]},
                ]
            }
        )
        assert [cue.text for cue in parse_json3(payload)] == ["hello"]

    def test_empty_segments_do_not_become_empty_words(self) -> None:
        payload = json.dumps(
            {
                "events": [
                    {
                        "tStartMs": 1000,
                        "dDurationMs": 2000,
                        "segs": [
                            {"utf8": "hello"},
                            {"utf8": " "},
                            {"utf8": " world", "tOffsetMs": 500},
                        ],
                    }
                ]
            }
        )
        (cue,) = parse_json3(payload)

        assert [(w.text, w.start) for w in cue.words] == [
            ("hello", 1.0),
            ("world", 1.5),
        ]


class TestVtt:
    def test_rolling_window_repetition_is_preserved(
        self, auto_vtt: list[Cue], auto_json3: list[Cue]
    ) -> None:
        # Roughly twice as many cues as json3, because each real cue is
        # preceded by a flush cue repeating the previous text. Removing that is
        # the refine stage's job, not the parser's.
        assert len(auto_vtt) == AUTO_VTT_CUES
        assert len(auto_vtt) > 2 * len(auto_json3) - 20

    def test_the_first_cue_survives_a_whitespace_only_line(
        self, auto_vtt: list[Cue]
    ) -> None:
        # Regression: YouTube's scrolling window writes a line containing a
        # single space as the empty upper row. Treating that as a cue
        # delimiter silently dropped the cue that followed it.
        assert auto_vtt[0].start == pytest.approx(0.030)
        assert auto_vtt[0].text == "the following content is provided under"

    def test_inline_word_tags_become_word_timings(
        self, auto_vtt: list[Cue], auto_json3: list[Cue]
    ) -> None:
        # Independently derived from a different encoding of the same speech,
        # so agreeing with json3 is meaningful.
        assert sum(1 for cue in auto_vtt if cue.words) == AUTO_WORD_CUES

    def test_the_first_word_of_a_cue_is_timed_at_the_cue_start(self) -> None:
        payload = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\n"
            "the<00:00:01.500><c> quick</c><00:00:02.000><c> fox</c>\n"
        )
        (cue,) = parse_vtt(payload)

        assert [(w.text, w.start) for w in cue.words] == [
            ("the", 1.0),
            ("quick", 1.5),
            ("fox", 2.0),
        ]

    def test_only_the_last_head_token_is_the_timed_word(self) -> None:
        # On an automatic track the text before the first timestamp also holds
        # the carried-over previous window; only its final token is the word
        # the first timestamp actually follows.
        payload = (
            "WEBVTT\n\n"
            "00:00:04.000 --> 00:00:06.000\n"
            "a Creative Commons license\n"
            "will<00:00:04.500><c> help</c>\n"
        )
        (cue,) = parse_vtt(payload)

        assert [w.text for w in cue.words] == ["will", "help"]

    def test_a_cue_starting_at_a_timestamp_has_no_untimed_first_word(self) -> None:
        payload = (
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<00:00:01.500><c>quick</c>\n"
        )
        (cue,) = parse_vtt(payload)

        assert [(w.text, w.start) for w in cue.words] == [("quick", 1.5)]

    def test_empty_word_tags_are_ignored(self) -> None:
        payload = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\n"
            "the<00:00:01.200><c></c><00:00:01.500><c> fox</c>\n"
        )
        (cue,) = parse_vtt(payload)

        assert [w.text for w in cue.words] == ["the", "fox"]

    def test_headers_and_comments_are_not_cues(self) -> None:
        payload = (
            "WEBVTT\nKind: captions\nLanguage: en\n\n"
            "NOTE this is a comment\n\n"
            "00:00:01.000 --> 00:00:02.000\nreal text\n"
        )
        assert [cue.text for cue in parse_vtt(payload)] == ["real text"]

    def test_html_entities_are_decoded(self) -> None:
        payload = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nBig&amp;Small\n"
        assert parse_vtt(payload)[0].text == "Big&Small"


class TestSrt:
    def test_the_leading_empty_cue_is_dropped(self, manual_srt: list[Cue]) -> None:
        # YouTube's SubRip export opens with cue 1 spanning 0.000 to 0.050 and
        # containing nothing at all.
        raw = load_caption("mit6006-lec1.manual.en.srt")

        assert raw.startswith("1\n00:00:00,000 --> 00:00:00,050\n\n")
        assert len(manual_srt) == MANUAL_CUES
        assert manual_srt[0].start == pytest.approx(0.050)

    def test_sequence_numbers_never_become_text(self, manual_srt: list[Cue]) -> None:
        assert not any(cue.text.strip().isdigit() for cue in manual_srt)

    def test_a_broken_timing_line_costs_one_cue_not_the_lecture(self) -> None:
        payload = (
            "1\n00:00:AA,000 --> nonsense\nlost\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nkept\n"
        )
        assert [cue.text for cue in parse_srt(payload)] == ["kept"]

    def test_comma_decimal_separator(self) -> None:
        payload = "1\n00:00:01,500 --> 00:00:02,750\nhello\n"
        (cue,) = parse_srt(payload)

        assert cue.start == pytest.approx(1.5)
        assert cue.duration == pytest.approx(1.25)


class TestArrowsInCueText:
    """An arrow is ordinary text in a lecture transcript.

    `A --> B` is how implication is written on a board, so it reaches captions
    on any programming or mathematics course. A scanner that finds cues by
    looking for `-->` anywhere in a line reads that sentence as a timing line:
    it ends the real cue, and starts one that parses as nothing.
    """

    @staticmethod
    def _wrap(fmt: str, body: str) -> str:
        cue = f"00:00:01.000 --> 00:00:04.000\n{body}\n"
        return f"WEBVTT\n\n{cue}" if fmt == "vtt" else f"1\n{cue}"

    @pytest.mark.parametrize("fmt", ["vtt", "srt"])
    def test_an_arrow_in_cue_text_does_not_end_the_cue(self, fmt: str) -> None:
        payload = self._wrap(fmt, "recall that\nA --> B is an implication\nso we write")
        (cue,) = parse_captions(payload, fmt)

        assert cue.text == "recall that A --> B is an implication so we write"

    @pytest.mark.parametrize("fmt", ["vtt", "srt"])
    def test_an_arrow_on_the_first_line_does_not_lose_the_cue(self, fmt: str) -> None:
        """The worse half: the cue used to vanish outright rather than be cut
        short, because the arrow replaced the timing that opened it."""
        payload = self._wrap(fmt, "A --> B is an implication")
        (cue,) = parse_captions(payload, fmt)

        assert cue.text == "A --> B is an implication"
        assert cue.start == 1.0

    def test_a_conformant_escaped_arrow_still_round_trips(self) -> None:
        """WebVTT requires the arrow escaped, and `unescape` runs after the
        blocks are split — so conformant tracks were never affected. Pinned so
        a change to that ordering cannot break it quietly."""
        payload = self._wrap("vtt", "A --&gt; B is an implication")

        assert [cue.text for cue in parse_vtt(payload)] == ["A --> B is an implication"]

    @pytest.mark.parametrize("fmt", ["vtt", "srt"])
    def test_a_malformed_timing_line_becomes_text(self, fmt: str) -> None:
        """The trade this makes, stated as a test rather than left to be
        discovered: a line shaped like a timing that does not parse is now part
        of the cue, where it used to end it."""
        payload = self._wrap(fmt, "before\n00:00:0x.000 --> 00:00:03.000\nafter")
        (cue,) = parse_captions(payload, fmt)

        assert cue.text == "before 00:00:0x.000 --> 00:00:03.000 after"


class TestDispatch:
    @pytest.mark.parametrize("fmt", ["json3", "vtt", "webvtt", "srt", "subrip"])
    def test_every_format_is_reachable_by_name(self, fmt: str) -> None:
        payloads = {
            "json3": '{"events": [{"tStartMs": 0, "dDurationMs": 10, '
            '"segs": [{"utf8": "hi"}]}]}',
            "vtt": "WEBVTT\n\n00:00:00.000 --> 00:00:00.010\nhi\n",
            "webvtt": "WEBVTT\n\n00:00:00.000 --> 00:00:00.010\nhi\n",
            "srt": "1\n00:00:00,000 --> 00:00:00,010\nhi\n",
            "subrip": "1\n00:00:00,000 --> 00:00:00,010\nhi\n",
        }
        assert parse_captions(payloads[fmt], fmt)[0].text == "hi"

    def test_unknown_format_names_the_supported_ones(self) -> None:
        with pytest.raises(UnknownCaptionFormat) as caught:
            parse_captions("...", "ttml")

        assert "json3" in str(caught.value)


class TestMalformedInput:
    def test_invalid_json(self) -> None:
        with pytest.raises(MalformedCaptions) as caught:
            parse_json3("{not json", source="abc")

        assert caught.value.remedy["code"] == "MALFORMED_CAPTIONS"
        assert "json3" in caught.value.cause

    def test_json_without_events(self) -> None:
        with pytest.raises(MalformedCaptions, match="events"):
            parse_json3('{"wireMagic": "pb3"}')

    def test_json_that_is_not_an_object(self) -> None:
        with pytest.raises(MalformedCaptions):
            parse_json3("[1, 2, 3]")

    def test_event_that_is_not_an_object(self) -> None:
        with pytest.raises(MalformedCaptions, match="not an object"):
            parse_json3('{"events": ["nope"]}')

    def test_event_without_a_start_time(self) -> None:
        with pytest.raises(MalformedCaptions, match="tStartMs"):
            parse_json3('{"events": [{"segs": [{"utf8": "hi"}]}]}')

    @pytest.mark.parametrize("duration", ["nonsense", None, [], {}])
    def test_an_unusable_duration_is_named_not_a_bare_type_error(
        self, duration: object
    ) -> None:
        """`tStartMs` was already guarded and `dDurationMs` was not, so the
        field one line below the careful one raised a bare `TypeError` out of
        arithmetic — which the CLI reports as an unclassified acquisition
        failure and no parser is mentioned anywhere in it."""
        payload = json.dumps(
            {
                "events": [
                    {"tStartMs": 0, "dDurationMs": duration, "segs": [{"utf8": "hi"}]}
                ]
            }
        )
        with pytest.raises(MalformedCaptions, match="dDurationMs"):
            parse_json3(payload)

    def test_a_missing_duration_is_fine_and_means_zero(self) -> None:
        """Genuinely optional, unlike the start. A cue with no duration still
        has the anchor that makes it citable."""
        payload = json.dumps({"events": [{"tStartMs": 5000, "segs": [{"utf8": "hi"}]}]})
        (cue,) = parse_json3(payload)

        assert cue.start == 5.0
        assert cue.duration == 0.0

    def test_a_negative_duration_is_clamped_rather_than_kept(self) -> None:
        """What `parse.vtt` and `parse.srt` already do with a cue that ends
        before it begins, and json3 did not. A cue whose `end` precedes its
        `start` puts a passage's end before its own beginning, and every
        consumer downstream would have to defend against it."""
        payload = json.dumps(
            {
                "events": [
                    {"tStartMs": 5000, "dDurationMs": -9000, "segs": [{"utf8": "hi"}]}
                ]
            }
        )
        (cue,) = parse_json3(payload)

        assert cue.duration == 0.0
        assert cue.end >= cue.start

    def test_a_line_that_only_looks_like_a_timing_is_not_one(self) -> None:
        # `banana --> split` no longer opens a cue at all, so it is discarded
        # as a header would be rather than becoming a cue that is then dropped.
        # Either way one bad line costs that line, never the lecture.
        payload = (
            "WEBVTT\n\n"
            "banana --> split\nignored\n\n"
            "00:00:01.000 --> 00:00:02.000\nkept\n"
        )
        assert [cue.text for cue in parse_vtt(payload)] == ["kept"]

    def test_empty_payloads_yield_no_cues(self) -> None:
        assert parse_vtt("WEBVTT\n") == []
        assert parse_srt("") == []
        assert parse_json3('{"events": []}') == []


class TestNestedSchemaValidation:
    """Every nested container and object, checked before it is walked.

    The library contract is that callers meet the documented taxonomy and
    nothing else. Before this, `{"events": null}` raised `TypeError` and
    `{"segs": [null]}` raised `AttributeError` — both true, neither in the
    taxonomy, and the CLI filed them as acquisition failures, which sends the
    reader to check their network over a malformed file.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            '{"events": null}',
            '{"events": 5}',
            '{"events": "abc"}',
            '{"events": {"a": 1}}',
            '{"events": true}',
        ],
    )
    def test_json3_events_must_be_a_list(self, payload: str) -> None:
        with pytest.raises(MalformedCaptions, match="not a list"):
            parse_json3(payload)

    @pytest.mark.parametrize("segs", [5, "xy", True, {"a": 1}])
    def test_json3_segs_must_be_a_list(self, segs: object) -> None:
        payload = json.dumps({"events": [{"tStartMs": 0, "segs": segs}]})

        with pytest.raises(MalformedCaptions, match="not a list"):
            parse_json3(payload)

    @pytest.mark.parametrize("seg", [None, 5, "text", [1]])
    def test_json3_each_seg_must_be_an_object(self, seg: object) -> None:
        payload = json.dumps({"events": [{"tStartMs": 0, "segs": [seg]}]})

        with pytest.raises(MalformedCaptions, match="not an object"):
            parse_json3(payload)


class TestNonFiniteTimestamps:
    """`NaN` and `Infinity` are JSON literals Python accepts.

    They survived parsing, reflow and section building without complaint, then
    came out of `format_timestamp` as a bare `ValueError: cannot convert float
    NaN to integer` — a renderer crash, stages away from the caption that
    caused it, on a lecture that had already been reported as fetched.
    """

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
    def test_json3_start(self, value: str) -> None:
        payload = f'{{"events":[{{"tStartMs":{value},"segs":[{{"utf8":"hi"}}]}}]}}'

        with pytest.raises(MalformedCaptions, match="not a time"):
            parse_json3(payload)

    @pytest.mark.parametrize("value", ["NaN", "Infinity"])
    def test_json3_duration(self, value: str) -> None:
        payload = (
            f'{{"events":[{{"tStartMs":0,"dDurationMs":{value},'
            f'"segs":[{{"utf8":"hi"}}]}}]}}'
        )

        with pytest.raises(MalformedCaptions, match="not a time"):
            parse_json3(payload)

    def test_json3_word_offset(self) -> None:
        payload = '{"events":[{"tStartMs":0,"segs":[{"utf8":"hi","tOffsetMs":NaN}]}]}'

        with pytest.raises(MalformedCaptions, match="not a time"):
            parse_json3(payload)

    def test_an_extreme_but_finite_timestamp_is_allowed_through(self) -> None:
        """Finite is the line. A lecture with an absurd timestamp is a lecture
        with an absurd timestamp; it renders, and the reader can see it."""
        payload = '{"events":[{"tStartMs":1e15,"segs":[{"utf8":"hi"}]}]}'

        (cue,) = parse_json3(payload)
        assert cue.start == 1e12


class TestNegativeTimestampsAreClamped:
    """Clamped rather than refused, which is what the parsers already did with
    a duration that ended before it began — and for the same reason. A start
    below zero renders as "-1:59:55" and every consumer downstream would have
    to defend against a lecture that begins before it begins."""

    def test_json3_negative_start(self) -> None:
        payload = '{"events":[{"tStartMs":-5000,"segs":[{"utf8":"hi"}]}]}'

        (cue,) = parse_json3(payload)
        assert cue.start == 0.0
