from __future__ import annotations

import pytest

from youtube_transcript_notes.errors import (
    AcquisitionFailed,
    AgeRestricted,
    LectureUnavailable,
    MalformedCaptions,
    MalformedCorrections,
    TrackNotFound,
    TranscriptError,
    UnknownRenderer,
)


class TestErrorMessages:
    """Error text is documentation people actually read, so it is tested."""

    def test_cause_substitutes_context(self) -> None:
        error = LectureUnavailable(source="dQw4w9WgXcQ")
        assert "'dQw4w9WgXcQ'" in error.cause

    def test_str_includes_cause_and_suggestions(self) -> None:
        message = str(LectureUnavailable(source="abc"))

        assert "not available" in message
        assert "What to try:" in message
        assert message.count("\n  - ") == len(LectureUnavailable.TRY)

    def test_str_omits_the_suggestions_block_when_there_are_none(self) -> None:
        message = str(TranscriptError())

        assert "What to try:" not in message
        assert message == TranscriptError.CAUSE

    def test_unclassified_transport_failures_keep_the_detail(self) -> None:
        error = AcquisitionFailed(source="abc", detail="HTTP 503 from the CDN")
        assert "HTTP 503 from the CDN" in error.cause

    def test_malformed_captions_names_the_format(self) -> None:
        error = MalformedCaptions(source="abc", fmt="json3", detail="no 'events' key")
        assert "json3" in error.cause
        assert "no 'events' key" in error.cause


class TestRemedy:
    """The machine-readable half, so an agent can branch on a failure."""

    def test_remedy_carries_code_suggestions_and_context(self) -> None:
        remedy = AgeRestricted(source="abc").remedy

        assert remedy["code"] == "AGE_RESTRICTED"
        assert remedy["context"] == {"source": "abc"}
        assert len(remedy["try"]) == len(AgeRestricted.TRY)

    def test_codes_are_unique_across_the_taxonomy(self) -> None:
        seen: dict[str, type[TranscriptError]] = {}
        pending = [TranscriptError]

        while pending:
            cls = pending.pop()
            pending.extend(cls.__subclasses__())
            # Intermediate families share no code with their leaves.
            if cls.CODE in seen and seen[cls.CODE] is not cls:
                pytest.fail(f"{cls.__name__} reuses code {cls.CODE!r}")
            seen[cls.CODE] = cls

    def test_remedy_is_a_copy_not_a_live_view(self) -> None:
        error = AgeRestricted(source="abc")
        error.remedy["context"]["source"] = "tampered"
        assert error.remedy["context"]["source"] == "abc"


class TestTrackNotFound:
    """Refusing to say what *was* available is the unhelpful version of this."""

    def test_lists_the_available_tracks(self) -> None:
        error = TrackNotFound(
            source="abc",
            languages=["de"],
            tiers=["manual"],
            available=['en ("English", manual)', 'en ("English", asr_platform)'],
        )
        message = str(error)

        assert 'en ("English", manual)' in message
        assert 'en ("English", asr_platform)' in message

    def test_says_so_plainly_when_nothing_was_available(self) -> None:
        error = TrackNotFound(
            source="abc", languages=["en"], tiers=["manual"], available=[]
        )
        assert "(none)" in str(error)

    def test_remedy_context_is_serialisable(self) -> None:
        import json

        error = TrackNotFound(
            source="abc", languages=["en"], tiers=["manual"], available=["en"]
        )
        # Sequences are normalised to lists so the remedy can cross a wire.
        assert json.loads(json.dumps(error.remedy))["code"] == "TRACK_NOT_FOUND"


class TestSeveralLectures:
    def test_names_the_lectures_it_found(self) -> None:
        from youtube_transcript_notes.errors import SeveralLectures

        message = str(SeveralLectures(source="6.006", lectures=["week-03", "week-04"]))

        assert "week-03" in message
        assert "week-04" in message
        assert "holds 2 lectures" in message

    def test_a_long_listing_is_capped_and_says_so(self) -> None:
        """Contract 5 applies to an error message holding three hundred
        lecture names as much as it does to a listing."""
        from youtube_transcript_notes.errors import SeveralLectures

        message = str(
            SeveralLectures(source="huge", lectures=[f"lec{n:03d}" for n in range(300)])
        )

        assert "lec000" in message
        assert "lec299" not in message
        assert "… and 288 more" in message


class TestConfigErrors:
    def test_unknown_renderer_lists_what_is_available(self) -> None:
        error = UnknownRenderer(
            kind="renderer", name="pdf", available="plain, markdown"
        )
        assert "plain, markdown" in error.cause


class TestErrorTextTellsTheTruth:
    def test_the_corrections_remedy_promises_only_fields_that_are_read(self) -> None:
        """`read_corrections` reads wrong, right and evidence; `at` and
        `confidence` are computed. Advice naming fields that vanish without
        comment is worse than no advice, in a project that calls error text
        documentation people actually read."""
        remedy = " ".join(MalformedCorrections.TRY)

        assert '"evidence"' in remedy
        assert '"at"' not in remedy
        assert '"confidence"' not in remedy


class TestTheTaxonomyIsImportable:
    def test_every_error_is_importable_from_the_package_root(self) -> None:
        """A library caller catching `EmptyTranscript` should not need to
        know which submodule it lives in — and for years seven of the
        twenty-seven were missing from the root."""
        import youtube_transcript_notes as pkg
        from youtube_transcript_notes import errors

        for name in errors.__all__:
            assert name in pkg.__all__, f"{name} missing from package __all__"
            assert getattr(pkg, name) is getattr(errors, name)
