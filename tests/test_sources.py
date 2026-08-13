"""Provider and resolver tests.

The invariant worth guarding hardest is that discovery downloads nothing. It
is easy to break by accident — one convenience lookup inside `list` and the
two-stage design quietly becomes one stage that always pays full price.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import CAPTIONS
from youtube_transcript_notes import TranscriptFetcher
from youtube_transcript_notes.errors import (
    EmptyTranscript,
    InputUnreadable,
    LectureUnavailable,
    MalformedCaptions,
    NoCaptionsAvailable,
    SeveralLectures,
    TrackNotFound,
)
from youtube_transcript_notes.models import TrustTier
from youtube_transcript_notes.render import get_renderer
from youtube_transcript_notes.resolve import (
    UNKNOWN_LANGUAGE,
    Track,
    looks_like_language,
    primary_subtag,
)
from youtube_transcript_notes.sources import LocalProvider, provider_for

FIXED_TIME = datetime(2026, 8, 6, 14, 30, tzinfo=timezone.utc)

#: Paragraphs in the measured lecture's human track. Pinned rather than
#: bounded: a paragraphing change that silently halved or doubled the number of
#: citable anchors would leave output that still looked perfectly fine.
MANUAL_PASSAGES = 92


@pytest.fixture
def fetcher() -> TranscriptFetcher:
    return TranscriptFetcher(clock=lambda: FIXED_TIME)


def write(directory: Path, name: str, body: str = "WEBVTT\n") -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


class TestDiscoveryDownloadsNothing:
    """The load-bearing half of the two-stage design."""

    def test_list_never_reads_a_caption_payload(
        self, fetcher: TranscriptFetcher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(self, ref):
            raise AssertionError("list() downloaded a caption payload")

        monkeypatch.setattr(LocalProvider, "load", explode)

        manifest = fetcher.list(str(CAPTIONS))

        assert len(manifest) == 5
        assert manifest.meta.title == "mit6006-lec1"

    def test_fetch_is_what_costs_something(
        self, fetcher: TranscriptFetcher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[object] = []
        original = LocalProvider.load
        monkeypatch.setattr(
            LocalProvider,
            "load",
            lambda self, ref: (calls.append(ref), original(self, ref))[1],
        )

        handle = fetcher.list(str(CAPTIONS)).find(["en"])
        assert calls == []

        handle.fetch()
        assert len(calls) == 1


class TestResolution:
    def test_the_full_fixture_set_is_discovered(
        self, fetcher: TranscriptFetcher
    ) -> None:
        manifest = fetcher.list(str(CAPTIONS))

        assert {h.track.caption_format for h in manifest} == {"json3", "vtt", "srt"}
        assert {h.track.tier for h in manifest} == {
            TrustTier.MANUAL,
            TrustTier.ASR_PLATFORM,
        }

    def test_human_written_beats_automatic(self, fetcher: TranscriptFetcher) -> None:
        assert fetcher.list(str(CAPTIONS)).find(["en"]).track.tier is TrustTier.MANUAL

    def test_json3_wins_within_a_tier(self, fetcher: TranscriptFetcher) -> None:
        # Three human-written tracks are available; json3 carries the most.
        assert fetcher.list(str(CAPTIONS)).find(["en"]).track.caption_format == "json3"

    def test_a_caller_can_demand_a_lower_tier(self, fetcher: TranscriptFetcher) -> None:
        handle = fetcher.list(str(CAPTIONS)).find(
            ["en"], tiers=[TrustTier.ASR_PLATFORM]
        )
        assert handle.track.tier is TrustTier.ASR_PLATFORM

    def test_language_preference_outranks_tier(self, tmp_path: Path) -> None:
        # German only exists as auto-captions; English has a human-written
        # track. Asking for German first must still return German.
        write(tmp_path, "lec.auto.de.vtt")
        write(tmp_path, "lec.manual.en.vtt")
        manifest = TranscriptFetcher().list(str(tmp_path))

        chosen = manifest.find(["de", "en"])
        assert chosen.track.language == "de"
        assert chosen.track.tier is TrustTier.ASR_PLATFORM

    def test_falls_through_to_the_next_language(self, tmp_path: Path) -> None:
        write(tmp_path, "lec.manual.en.vtt")

        assert (
            TranscriptFetcher().list(str(tmp_path)).find(["de", "en"]).track.language
            == "en"
        )

    def test_languages_lists_what_is_on_offer(self, tmp_path: Path) -> None:
        write(tmp_path, "lec.manual.en.vtt")
        write(tmp_path, "lec.manual.fr.vtt")

        assert set(TranscriptFetcher().list(str(tmp_path)).languages()) == {"en", "fr"}

    def test_nothing_matching_says_what_was_available(
        self, fetcher: TranscriptFetcher
    ) -> None:
        with pytest.raises(TrackNotFound) as caught:
            fetcher.list(str(CAPTIONS)).find(["ja"])

        message = str(caught.value)
        assert "mit6006-lec1.manual.en.json3" in message
        assert caught.value.remedy["code"] == "TRACK_NOT_FOUND"


class TestLanguageMatching:
    def test_primary_subtag_strips_youtube_track_identifiers(self) -> None:
        # Real MIT OpenCourseWare labelling. Matching only exact tags would
        # miss the English transcript on a lecture that obviously has one.
        assert primary_subtag("en-j3PyPqV-e1s") == "en"
        assert primary_subtag("en-GB") == "en"
        assert primary_subtag("EN") == "en"

    @pytest.mark.parametrize(
        ("raw", "requested", "expected"),
        [
            ("en", "en", True),
            ("en-GB", "en", True),
            ("en-j3PyPqV-e1s", "en", True),
            ("zh-Hant", "zh-Hant", True),
            ("zh-Hant", "zh", True),
            ("en", "de", False),
            ("de", "en", False),
        ],
    )
    def test_matching(self, raw: str, requested: str, expected: bool) -> None:
        track = Track(
            language=primary_subtag(raw),
            raw_language=raw,
            tier=TrustTier.MANUAL,
            caption_format="vtt",
        )
        assert track.matches(requested) is expected

    def test_an_unlabelled_track_matches_anything(self, tmp_path: Path) -> None:
        # Refusing the only transcript there is, because nobody named its
        # language, would be pedantry.
        write(tmp_path, "lecture.vtt")
        handle = TranscriptFetcher().list(str(tmp_path)).find(["en"])

        assert handle.track.language == UNKNOWN_LANGUAGE

    def test_raw_language_defaults_to_the_language(self) -> None:
        track = Track(language="en", tier=TrustTier.MANUAL, caption_format="vtt")
        assert track.raw_language == "en"

    @pytest.mark.parametrize(
        "tag",
        # `en-j3PyPqV-e1s` is the one that matters: the table is checked
        # against the primary subtag only, because the rest is MIT
        # OpenCourseWare's own track identifier and no standard covers it.
        [
            "en",
            "EN",
            "en-GB",
            "en-j3PyPqV-e1s",
            "zh-Hant",
            "de",
            "fr",
            "eng",
            "deu",
            "fil",
            "haw",
            "und",
            "iw",
        ],
    )
    def test_real_language_tags_are_recognised(self, tag: str) -> None:
        assert looks_like_language(tag) is True

    @pytest.mark.parametrize(
        "text",
        [
            "raw",
            "hd",
            "new",
            "old",
            "tmp",
            "bak",
            "cut",
            "fix",
            "mix",
            "ocr",
            "sub",
            "cc",
            "fin",
            "lec-1",
            "take2",
            "part1",
            "v2",
            "x",
        ],
    )
    def test_filename_junk_is_not_a_language(self, text: str) -> None:
        assert looks_like_language(text) is False


class TestFilenameConvention:
    @pytest.mark.parametrize(
        ("name", "tier"),
        [
            ("lec.en.vtt", TrustTier.MANUAL),
            ("lec.manual.en.vtt", TrustTier.MANUAL),
            ("lec.human.en.vtt", TrustTier.MANUAL),
            ("lec.auto.en.vtt", TrustTier.ASR_PLATFORM),
            ("lec.asr.en.vtt", TrustTier.ASR_PLATFORM),
            ("lec.whisper.en.vtt", TrustTier.ASR_LOCAL),
            ("lec.transcribed.en.vtt", TrustTier.ASR_LOCAL),
            ("lec.translated.fr.vtt", TrustTier.TRANSLATED),
        ],
    )
    def test_tier_markers(self, tmp_path: Path, name: str, tier: TrustTier) -> None:
        write(tmp_path, name)
        assert TranscriptFetcher().list(str(tmp_path)).tracks[0].track.tier is tier

    def test_unmarked_files_are_assumed_human_written(self, tmp_path: Path) -> None:
        write(tmp_path, "lec.en.vtt")
        assert (
            TranscriptFetcher().list(str(tmp_path)).tracks[0].track.tier
            is TrustTier.MANUAL
        )

    def test_files_that_are_not_captions_are_ignored(self, tmp_path: Path) -> None:
        write(tmp_path, "lec.en.vtt")
        write(tmp_path, "notes.md", "# notes")
        write(tmp_path, "thumbnail.jpg", "x")

        assert len(TranscriptFetcher().list(str(tmp_path))) == 1

    def test_filename_parts_that_mean_nothing_are_skipped(self, tmp_path: Path) -> None:
        # "take2" is neither a tier marker nor a language tag; it should not
        # be mistaken for either.
        write(tmp_path, "lec.take2.auto.en.vtt")
        track = TranscriptFetcher().list(str(tmp_path)).tracks[0].track

        assert track.tier is TrustTier.ASR_PLATFORM
        assert track.language == "en"

    @pytest.mark.parametrize(
        "part",
        # Every one of these used to pass for a language, because the test was
        # its shape and nothing else. "take2" was the only case the old test
        # covered, and it was rejected for having a digit in it — so the
        # guarantee the test claimed was never the one the code gave.
        ["raw", "hd", "new", "old", "tmp", "bak", "cut", "fix", "mix", "ocr", "sub"],
    )
    def test_a_short_word_is_not_a_language(self, tmp_path: Path, part: str) -> None:
        write(tmp_path, f"lec.{part}.vtt")
        track = TranscriptFetcher().list(str(tmp_path)).tracks[0].track

        assert track.language == UNKNOWN_LANGUAGE

    def test_an_unrecognised_part_still_matches_any_request(
        self, tmp_path: Path
    ) -> None:
        """The graceful half: a part TranscriptFetcher cannot read costs the track its
        label, not its usability. `und` matches whatever is asked for."""
        write(tmp_path, "lecture.raw.vtt")

        assert TranscriptFetcher().list(str(tmp_path)).find(["en"]) is not None

    def test_the_first_language_in_a_name_wins(self, tmp_path: Path) -> None:
        """`lecture.en.raw.vtt` resolved to `raw` under last-match-wins, so a
        correctly labelled file was broken by a word appended after the label."""
        write(tmp_path, "lec.en.raw.vtt")

        assert TranscriptFetcher().list(str(tmp_path)).tracks[0].track.language == "en"

    def test_the_first_tier_marker_in_a_name_wins(self, tmp_path: Path) -> None:
        write(tmp_path, "lec.auto.manual.en.vtt")
        track = TranscriptFetcher().list(str(tmp_path)).tracks[0].track

        assert track.tier is TrustTier.ASR_PLATFORM
        assert track.language == "en"

    def test_a_bare_name_with_no_extension_is_ignored(self, tmp_path: Path) -> None:
        write(tmp_path, "README")
        write(tmp_path, "lec.en.vtt")

        assert len(TranscriptFetcher().list(str(tmp_path))) == 1

    def test_a_single_file_names_its_own_lecture(self, tmp_path: Path) -> None:
        path = write(tmp_path, "week-03.en.vtt")
        assert TranscriptFetcher().list(str(path)).meta.title == "week-03"

    def test_a_directory_of_one_lecture_takes_the_shared_stem(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "week-03.manual.en.vtt")
        write(tmp_path, "week-03.auto.en.vtt")

        assert TranscriptFetcher().list(str(tmp_path)).meta.title == "week-03"

    def test_a_directory_of_several_lectures_becomes_several_sources(
        self, tmp_path: Path
    ) -> None:
        """This folder used to be one lecture named after the folder, holding
        only the alphabetically first file's text — a course rendered as one
        note whose title, id and provenance named something that did not
        exist. In filename order, so two runs agree."""
        write(tmp_path, "week-04.en.vtt")
        write(tmp_path, "week-03.en.vtt")

        expansion = TranscriptFetcher().expand(str(tmp_path))

        assert [Path(s).name for s in expansion.sources] == ["week-03", "week-04"]
        assert expansion.origin == str(tmp_path)

    def test_listing_a_folder_of_several_lectures_says_so(self, tmp_path: Path) -> None:
        """A manifest describes one lecture, so `list` refuses rather than
        picking one. The CLI never reaches this — it expands first."""
        write(tmp_path, "week-03.en.vtt")
        write(tmp_path, "week-04.en.vtt")

        with pytest.raises(SeveralLectures) as caught:
            TranscriptFetcher().list(str(tmp_path))

        assert "week-03" in str(caught.value)
        assert "week-04" in str(caught.value)
        assert caught.value.remedy["code"] == "SEVERAL_LECTURES"

    def test_a_stem_addresses_one_lecture_in_a_shared_folder(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "week-03.en.vtt")
        write(tmp_path, "week-04.en.vtt")

        manifest = TranscriptFetcher().list(str(tmp_path / "week-03"))

        assert manifest.meta.title == "week-03"
        assert [h.track.label for h in manifest] == ["week-03.en.vtt"]

    def test_naming_one_file_still_means_that_file(self, tmp_path: Path) -> None:
        """The constraint the whole design preserves: asking for the vtt must
        not quietly get you the json3 the resolver would rather have."""
        write(tmp_path, "week-03.en.vtt")
        write(tmp_path, "week-03.en.json3", "{}")

        manifest = TranscriptFetcher().list(str(tmp_path / "week-03.en.vtt"))

        assert [h.track.caption_format for h in manifest] == ["vtt"]

    def test_two_halves_of_a_lecture_stay_one_lecture(self, tmp_path: Path) -> None:
        """Known limitation, pinned so a later change has to face it. The stem
        is read to the first dot, which is what correctly makes `lec1.en.vtt`
        and `lec1.auto.en.vtt` one lecture — and what keeps these two together
        when they are really two halves. Both stay visible in a listing."""
        write(tmp_path, "week-03.part1.en.vtt")
        write(tmp_path, "week-03.part2.en.vtt")

        manifest = TranscriptFetcher().list(str(tmp_path))

        assert manifest.meta.title == "week-03"
        assert len(manifest) == 2

    def test_a_folder_of_nothing_readable_is_refused(self, tmp_path: Path) -> None:
        """Not expanded to nothing: a run that processes zero lectures, finds
        zero failures and prints nothing exits 0 looking like a success."""
        write(tmp_path, "notes.md", "# notes")

        with pytest.raises(NoCaptionsAvailable):
            TranscriptFetcher().expand(str(tmp_path))

    def test_a_folder_expands_without_opening_a_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write(tmp_path, "week-03.en.vtt")
        write(tmp_path, "week-04.en.vtt")

        def explode(self, ref):
            raise AssertionError("expand() read a caption payload")

        monkeypatch.setattr(LocalProvider, "load", explode)

        assert len(TranscriptFetcher().expand(str(tmp_path)).sources) == 2

    def test_a_bare_id_matching_a_local_stem_is_read_from_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The widening, tested rather than left to be discovered: an existing
        path already beat a video id, and a stem address is one now too."""
        write(tmp_path, "HtSuA80QTyo.en.vtt")
        monkeypatch.chdir(tmp_path)

        assert provider_for("HtSuA80QTyo").name == "local"

    def test_an_unreadable_sibling_cannot_capture_a_bare_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write(tmp_path, "HtSuA80QTyo.txt", "not a caption file")
        monkeypatch.chdir(tmp_path)

        assert provider_for("HtSuA80QTyo").name != "local"


class TestProviderContract:
    def test_a_provider_claims_nothing_until_it_says_otherwise(self) -> None:
        # The base answer must be "no", so a new provider cannot accidentally
        # start intercepting sources it has no idea how to handle.
        from youtube_transcript_notes.sources.base import SourceProvider

        assert SourceProvider.handles("anything at all") is False

    def test_expansion_is_the_source_itself_unless_a_provider_says_otherwise(
        self, tmp_path: Path
    ) -> None:
        # The default has to be a passthrough, so a provider that never heard
        # of collections is untouched by expansion existing. Tested against a
        # stub rather than through `LocalProvider`, which now overrides it —
        # this is base-class API that future providers inherit, and nothing
        # shipping exercises it any more.
        from youtube_transcript_notes.sources import Expansion, SourceProvider

        class Bare(SourceProvider):
            def list(self, source):
                raise NotImplementedError

            def load(self, ref):
                raise NotImplementedError

        expansion = Bare().expand("anything at all")

        assert expansion == Expansion(sources=("anything at all",))
        assert expansion.origin is None
        assert expansion.stale_reason is None

    def test_a_single_file_is_not_a_collection(self, tmp_path: Path) -> None:
        from youtube_transcript_notes.sources import Expansion

        source = str(write(tmp_path, "lec.en.vtt"))

        assert LocalProvider().expand(source) == Expansion(sources=(source,))

    def test_the_default_clock_is_the_real_one(self) -> None:
        before = datetime.now(timezone.utc)
        stamped = LocalProvider().now()

        assert stamped.tzinfo is not None
        assert before <= stamped <= datetime.now(timezone.utc)


class TestProviderSelection:
    def test_local_claims_paths_that_exist(self, tmp_path: Path) -> None:
        assert LocalProvider.handles(str(tmp_path))
        assert not LocalProvider.handles("https://example.invalid/watch?v=abc")

    def test_a_source_nobody_claims_is_an_error(self) -> None:
        from youtube_transcript_notes.errors import UnknownProvider

        with pytest.raises(UnknownProvider):
            provider_for("https://example.invalid/watch?v=abc")

    def test_a_provider_can_be_chosen_by_name(self) -> None:
        assert (
            TranscriptFetcher(provider="local").provider_for("anything").name == "local"
        )

    def test_an_explicit_provider_instance_is_used_as_is(self) -> None:
        provider = LocalProvider()
        assert TranscriptFetcher(provider=provider).provider_for("anything") is provider

    def test_expanding_a_source_nobody_claims_returns_it_alone(self) -> None:
        """Expansion answers "what does this name", not "is this valid". The
        `list` call that follows reports an unrecognised source once, where
        every other per-source failure is reported — raising here instead
        would report it at a moment nothing else fails at."""
        source = "https://example.invalid/watch?v=abc"

        assert TranscriptFetcher().expand(source).sources == (source,)

    def test_expanding_through_the_facade_reaches_the_provider(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, "week-03.en.vtt")

        assert TranscriptFetcher().expand(str(tmp_path)).sources == (
            str(tmp_path / "week-03"),
        )


class TestFailures:
    def test_a_missing_path(self, tmp_path: Path) -> None:
        with pytest.raises(LectureUnavailable):
            LocalProvider().list(str(tmp_path / "nope.vtt"))

    def test_a_directory_with_no_captions(self, tmp_path: Path) -> None:
        write(tmp_path, "notes.md", "# notes")

        with pytest.raises(NoCaptionsAvailable) as caught:
            TranscriptFetcher().list(str(tmp_path))

        assert caught.value.remedy["code"] == "NO_CAPTIONS_AVAILABLE"

    def test_a_track_with_no_timing_lines_is_refused(self, tmp_path: Path) -> None:
        """It used to render a note holding a title and nothing else, and call
        that a success. A file can be perfectly readable and still be no
        transcript — this one has words in it and not one cue."""
        write(tmp_path, "lec.en.vtt", "WEBVTT\n\nthis file has no timing lines\n")

        with pytest.raises(EmptyTranscript) as caught:
            TranscriptFetcher().fetch(str(tmp_path / "lec.en.vtt"))

        assert caught.value.remedy["code"] == "EMPTY_TRANSCRIPT"
        assert "lec" in str(caught.value)

    def test_a_track_that_is_all_non_speech_is_refused_too(
        self, tmp_path: Path
    ) -> None:
        """Measured after reflow rather than on the cues, which is what catches
        this: two real cues, both `[MUSIC]`, and nothing left once the
        captioner's own notes are read rather than repeated."""
        write(
            tmp_path,
            "lec.en.vtt",
            "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n[MUSIC]\n\n"
            "00:00:05.000 --> 00:00:08.000\n[MUSIC]\n",
        )

        with pytest.raises(EmptyTranscript):
            TranscriptFetcher().fetch(str(tmp_path / "lec.en.vtt"))

    def test_a_track_with_text_is_not_refused(self, tmp_path: Path) -> None:
        write(
            tmp_path,
            "lec.en.vtt",
            "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nthere are words here\n",
        )

        assert TranscriptFetcher().fetch(str(tmp_path / "lec.en.vtt")).sections

    def test_a_file_that_vanishes_between_listing_and_reading(
        self, tmp_path: Path
    ) -> None:
        """Discovery and retrieval are two moments, and a synced or tidied
        folder can take the file away in between.

        It used to escape as a bare `FileNotFoundError`, reach the CLI's last
        resort, and be reported as "retry — transient network and rate-limit
        failures are common": advice about a network a local file never touches,
        for a problem no retry repairs.
        """
        path = write(
            tmp_path,
            "lec.en.vtt",
            "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nthere are words here\n",
        )
        handle = TranscriptFetcher().list(str(path)).find(["en"])
        path.unlink()

        with pytest.raises(InputUnreadable) as caught:
            handle.fetch()

        assert caught.value.remedy["code"] == "INPUT_UNREADABLE"
        assert "lec.en.vtt" in caught.value.cause


class TestEncoding:
    """What a caption file off someone's disk is actually encoded as."""

    def test_a_byte_order_mark_does_not_become_part_of_the_first_word(
        self, tmp_path: Path
    ) -> None:
        """Several caption tools write one. Read as plain UTF-8 the mark
        survives as a `\\ufeff` character glued to the front of the file, which
        turns `WEBVTT` into a word the parser does not recognise while looking
        completely normal in any editor."""
        path = tmp_path / "lec.en.vtt"
        path.write_text(
            "﻿WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello there\n",
            encoding="utf-8",
        )

        lecture = TranscriptFetcher(clock=lambda: FIXED_TIME).fetch(str(path))

        assert lecture.text == "hello there"

    def test_a_file_that_is_not_utf8_says_so_in_the_taxonomy(
        self, tmp_path: Path
    ) -> None:
        """Otherwise a `UnicodeDecodeError` escapes as an unclassified
        acquisition failure — a true statement about the transport, and no
        help at all to someone holding a file their editor saved as Latin-1."""
        path = tmp_path / "lec.en.srt"
        path.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\ncaf\xe9 na\xefve\n")

        with pytest.raises(MalformedCaptions) as caught:
            TranscriptFetcher().fetch(str(path))

        assert caught.value.remedy["code"] == "MALFORMED_CAPTIONS"
        assert "not valid UTF-8" in caught.value.cause
        assert "Re-save it as UTF-8" in caught.value.cause

    def test_a_bad_encoding_costs_one_lecture_not_the_batch(
        self, tmp_path: Path
    ) -> None:
        """It arrives as a `TranscriptError`, so the CLI's batch loop catches it
        by type rather than by the unclassified fallback."""
        from youtube_transcript_notes.errors import TranscriptError

        path = tmp_path / "lec.en.srt"
        path.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\n\xff\xfe\n")

        with pytest.raises(TranscriptError):
            TranscriptFetcher().fetch(str(path))


class TestProvenance:
    def test_a_fetched_lecture_records_where_it_came_from(
        self, fetcher: TranscriptFetcher
    ) -> None:
        lecture = fetcher.fetch(str(CAPTIONS))
        provenance = lecture.provenance

        assert provenance.provider == "local"
        assert provenance.tier is TrustTier.MANUAL
        assert provenance.caption_format == "json3"
        assert provenance.language == "en"
        assert provenance.retrieved_at == FIXED_TIME
        assert len(provenance.content_hash) == 64

    def test_the_hash_is_of_the_payload_that_was_parsed(
        self, fetcher: TranscriptFetcher
    ) -> None:
        from youtube_transcript_notes.models import content_hash

        expected = content_hash(
            (CAPTIONS / "mit6006-lec1.manual.en.json3").read_text(encoding="utf-8")
        )
        assert fetcher.fetch(str(CAPTIONS)).provenance.content_hash == expected


class TestEndToEnd:
    def test_caption_files_in_study_notes_out(self, fetcher: TranscriptFetcher) -> None:
        lecture = fetcher.fetch(str(CAPTIONS))
        notes = get_renderer("markdown").render(lecture)

        assert notes.startswith("# mit6006-lec1")
        assert "**[0:00]**" in notes
        assert "Creative Commons license" in notes
        assert len(lecture.passages) == MANUAL_PASSAGES

    def test_the_convenience_method_equals_the_long_way_round(
        self, fetcher: TranscriptFetcher
    ) -> None:
        direct = fetcher.fetch(str(CAPTIONS), ["en"])
        stepwise = fetcher.list(str(CAPTIONS)).find(["en"]).fetch()

        assert direct == stepwise

    def test_choosing_the_automatic_track_changes_the_words(
        self, fetcher: TranscriptFetcher
    ) -> None:
        manual = fetcher.fetch(str(CAPTIONS), ["en"])
        auto = fetcher.fetch(str(CAPTIONS), ["en"], tiers=[TrustTier.ASR_PLATFORM])

        assert manual.text != auto.text
        # Speaker labels only exist in the human track, and they are read as
        # identity rather than left in the prose, so this is where they land.
        assert {p.speaker for p in manual.passages} > {None}
        assert {p.speaker for p in auto.passages} == {None}
        assert auto.provenance.tier is TrustTier.ASR_PLATFORM

    def test_a_single_file_works_as_a_source(self, fetcher: TranscriptFetcher) -> None:
        path = CAPTIONS / "mit6006-lec1.manual.en.srt"
        lecture = fetcher.fetch(str(path))

        assert lecture.provenance.caption_format == "srt"
        assert len(lecture.passages) == MANUAL_PASSAGES
