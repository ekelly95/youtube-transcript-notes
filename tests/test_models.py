from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from youtube_transcript_notes.errors import MalformedLecture
from youtube_transcript_notes.models import (
    SCHEMA_VERSION,
    Chapter,
    Correction,
    Cue,
    Lecture,
    LectureMeta,
    Locator,
    Passage,
    Provenance,
    Section,
    TrustTier,
    Word,
    content_hash,
    format_date,
    format_timestamp,
    sort_by_tier,
)


class TestRoundTrip:
    """The contract that makes caching and reproducibility possible."""

    def test_full_lecture_round_trips_exactly(self, full_lecture: Lecture) -> None:
        assert Lecture.from_dict(full_lecture.to_dict()) == full_lecture

    def test_minimal_lecture_round_trips_exactly(
        self, minimal_lecture: Lecture
    ) -> None:
        assert Lecture.from_dict(minimal_lecture.to_dict()) == minimal_lecture

    def test_serialised_form_is_plain_json_types(self, full_lecture: Lecture) -> None:
        import json

        # If this raises, something is leaking a dataclass or an enum into the
        # serialised form and the cache would not be able to write it.
        assert json.loads(json.dumps(full_lecture.to_dict())) == full_lecture.to_dict()

    def test_rejects_unknown_schema_version(self, full_lecture: Lecture) -> None:
        data = full_lecture.to_dict()
        data["v"] = SCHEMA_VERSION + 1

        with pytest.raises(MalformedLecture) as caught:
            Lecture.from_dict(data)

        assert caught.value.remedy["code"] == "MALFORMED_LECTURE"

    def test_rejects_missing_schema_version(self, full_lecture: Lecture) -> None:
        data = full_lecture.to_dict()
        del data["v"]

        with pytest.raises(MalformedLecture):
            Lecture.from_dict(data)

    def test_cue_round_trips_with_word_timings(self) -> None:
        cue = Cue(
            text="dynamic programming",
            start=12.4,
            duration=1.1,
            words=(Word("dynamic", 12.4), Word("programming", 12.9)),
        )
        assert Cue.from_dict(cue.to_dict()) == cue

    def test_cue_round_trips_without_word_timings(self) -> None:
        cue = Cue(text="right so", start=0.0, duration=2.0)
        assert Cue.from_dict(cue.to_dict()) == cue
        assert cue.words == ()

    def test_a_correction_round_trips(self) -> None:
        correction = Correction(
            wrong="quad code",
            right="Claude Code",
            at=124.5,
            confidence=0.9,
            evidence="title",
            occurrences=23,
        )
        assert Correction.from_dict(correction.to_dict()) == correction

    def test_a_correction_round_trips_with_only_what_it_must_have(self) -> None:
        correction = Correction(wrong="a", right="b")

        assert Correction.from_dict(correction.to_dict()) == correction
        assert Correction.from_dict({"wrong": "a", "right": "b"}) == correction

    def test_seeing_a_correction_again_only_counts_it(self) -> None:
        once = Correction(wrong="a", right="b", at=4.0)

        assert once.again() == Correction(wrong="a", right="b", at=4.0, occurrences=2)

    def test_chapter_round_trips_without_end(self) -> None:
        chapter = Chapter(title="Q&A", start=3000.0)
        assert Chapter.from_dict(chapter.to_dict()) == chapter


class TestContentHash:
    def test_str_and_bytes_agree(self) -> None:
        assert content_hash("hello") == content_hash(b"hello")

    def test_different_payloads_differ(self) -> None:
        assert content_hash("a") != content_hash("b")


class TestFormatTimestamp:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "0:00"),
            (7.9, "0:07"),
            (307.0, "5:07"),
            (3907.4, "1:05:07"),
            (36000.0, "10:00:00"),
        ],
    )
    def test_formats_lecture_length_times(self, seconds: float, expected: str) -> None:
        assert format_timestamp(seconds) == expected


class TestFormatDate:
    def test_long_form_is_locale_independent(self) -> None:
        assert format_date(date(2011, 9, 12)) == "12 September 2011"

    def test_no_leading_zero_on_the_day(self) -> None:
        assert format_date(date(2011, 9, 1)) == "1 September 2011"

    def test_unknown_date_is_n_d(self) -> None:
        assert format_date(None) == "n.d."


class TestTrustTier:
    def test_manual_outranks_everything(self) -> None:
        assert TrustTier.MANUAL.rank < TrustTier.ASR_PLATFORM.rank
        assert TrustTier.ASR_PLATFORM.rank < TrustTier.ASR_LOCAL.rank
        assert TrustTier.ASR_LOCAL.rank < TrustTier.TRANSLATED.rank

    def test_only_platform_asr_is_assumed_unpunctuated(self) -> None:
        assert not TrustTier.ASR_PLATFORM.assume_punctuated
        assert TrustTier.MANUAL.assume_punctuated
        assert TrustTier.ASR_LOCAL.assume_punctuated
        assert TrustTier.TRANSLATED.assume_punctuated

    def test_sort_by_tier_puts_most_trusted_first(self) -> None:
        assert sort_by_tier(
            [TrustTier.TRANSLATED, TrustTier.MANUAL, TrustTier.ASR_PLATFORM]
        ) == (TrustTier.MANUAL, TrustTier.ASR_PLATFORM, TrustTier.TRANSLATED)


class TestCue:
    def test_end_is_start_plus_duration(self) -> None:
        assert Cue(text="x", start=10.0, duration=2.5).end == 12.5


class TestSection:
    def test_end_comes_from_the_last_passage(self, full_lecture: Lecture) -> None:
        assert full_lecture.sections[0].end == 131.2

    def test_empty_section_ends_where_it_starts(self) -> None:
        assert Section(title="Intro", start=42.0, passages=()).end == 42.0

    def test_text_joins_passages_as_paragraphs(self, full_lecture: Lecture) -> None:
        assert full_lecture.sections[0].text.count("\n\n") == 1


class TestLocator:
    def test_reference_includes_section_when_known(self) -> None:
        locator = Locator(
            source_id="abc", start=307.0, end=310.0, section="Memoisation"
        )
        assert locator.reference() == "[Memoisation, 5:07]"

    def test_reference_falls_back_to_timestamp_alone(self) -> None:
        assert Locator(source_id="abc", start=307.0, end=310.0).reference() == "[5:07]"

    def test_url_is_none_without_a_base(self) -> None:
        assert Locator(source_id="abc", start=307.0, end=310.0).url is None

    def test_url_appends_to_an_existing_query_string(self) -> None:
        locator = Locator(
            source_id="abc",
            start=307.9,
            end=310.0,
            base_url="https://www.youtube.com/watch?v=abc",
        )
        assert locator.url == "https://www.youtube.com/watch?v=abc&t=307"

    def test_url_starts_a_query_string_when_there_is_none(self) -> None:
        locator = Locator(
            source_id="abc", start=307.0, end=310.0, base_url="https://youtu.be/abc"
        )
        assert locator.url == "https://youtu.be/abc?t=307"

    def test_timestamp_delegates_to_the_shared_formatter(self) -> None:
        assert Locator(source_id="a", start=3907.0, end=1.0).timestamp == "1:05:07"


class TestLecture:
    def test_walk_pairs_each_passage_with_its_section(
        self, full_lecture: Lecture
    ) -> None:
        pairs = list(full_lecture.walk())
        assert [section.title for section, _ in pairs] == [
            "Memoisation",
            "Memoisation",
            "Bottom-up tables",
        ]

    def test_passages_flattens_every_section(self, full_lecture: Lecture) -> None:
        assert len(full_lecture.passages) == 3

    def test_text_joins_every_passage(self, full_lecture: Lecture) -> None:
        assert full_lecture.text.count("\n\n") == 2

    def test_locators_carry_section_and_deep_link(self, full_lecture: Lecture) -> None:
        locators = list(full_lecture.locators())

        assert locators[0].section == "Memoisation"
        assert locators[0].url == ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12")
        assert locators[-1].reference() == "[Bottom-up tables, 1:01:01]"

    def test_locator_for_without_a_section(self, full_lecture: Lecture) -> None:
        passage = full_lecture.passages[0]
        assert full_lecture.locator_for(passage).section is None


class TestProvenance:
    def test_survives_a_round_trip_with_timezone(self) -> None:
        provenance = Provenance(
            provider="youtube",
            tier=TrustTier.ASR_PLATFORM,
            language="en",
            caption_format="json3",
            retrieved_at=datetime(2026, 8, 6, 14, 30, tzinfo=timezone.utc),
            content_hash="c" * 64,
        )
        restored = Provenance.from_dict(provenance.to_dict())

        assert restored == provenance
        assert restored.retrieved_at.tzinfo is not None


class TestLectureMeta:
    def test_publication_date_round_trips(self) -> None:
        meta = LectureMeta(
            source_id="x", title="Lecture 1", published=date(2011, 9, 12)
        )
        assert LectureMeta.from_dict(meta.to_dict()).published == date(2011, 9, 12)

    def test_passage_round_trips(self) -> None:
        passage = Passage(text="hello", start=1.0, end=2.0)
        assert Passage.from_dict(passage.to_dict()) == passage
