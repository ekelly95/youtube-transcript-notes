"""CLI tests.

Every test here calls `run` and inspects the returned value. Nothing captures
stdout, which is the entire point of having `run` return its output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import CAPTIONS, FIXTURES, load_caption
from youtube_transcript_notes import cli
from youtube_transcript_notes.cli import EXIT_FAILED, EXIT_OK, EXIT_PARTIAL, main, run
from youtube_transcript_notes.errors import AcquisitionFailed

SOURCE = str(CAPTIONS)
MISSING = "definitely-not-a-real-path-9f3a"
LECTURE = "HtSuA80QTyo"


class TestOutput:
    def test_run_returns_its_output_rather_than_printing(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        result = run([SOURCE])

        assert result.text.startswith("# mit6006-lec1")
        assert result.exit_code == EXIT_OK
        assert capsys.readouterr().out == ""

    def test_markdown_is_the_default(self) -> None:
        assert run([SOURCE]).text.startswith("#")

    @pytest.mark.parametrize(
        ("fmt", "check"),
        [
            ("plain", lambda t: not t.startswith("#")),
            ("markdown", lambda t: t.startswith("# ")),
            ("citation", lambda t: "[Video]" in t),
            ("jsonl", lambda t: json.loads(t.splitlines()[0])["source_id"]),
        ],
    )
    def test_every_registered_format_is_reachable(self, fmt: str, check) -> None:
        assert check(run([SOURCE, "--format", fmt]).text)

    def test_format_choices_come_from_the_registry(self) -> None:
        from youtube_transcript_notes.render import renderers

        with pytest.raises(SystemExit):
            run([SOURCE, "--format", "pdf"])

        # Aliases work too, because the choices are the registry's keys.
        assert "md" in renderers.keys()
        assert run([SOURCE, "--format", "md"]).exit_code == EXIT_OK


class TestListing:
    def test_list_shows_what_exists(self) -> None:
        text = run([SOURCE, "--list"]).text

        assert "mit6006-lec1" in text
        assert "manual, json3" in text
        assert "asr_platform" in text

    def test_list_names_the_channel_when_the_source_has_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Local caption files have no channel; a YouTube lecture does, and the
        # listing should say who gave the lecture.
        from youtube_transcript_notes.api import TranscriptFetcher
        from youtube_transcript_notes.models import LectureMeta, TrustTier
        from youtube_transcript_notes.resolve import Track, TrackHandle, TrackManifest

        meta = LectureMeta(
            source_id="abc", title="Lecture 1", channel="MIT OpenCourseWare"
        )
        manifest = TrackManifest(
            meta=meta,
            tracks=(
                TrackHandle(
                    track=Track(
                        language="en", tier=TrustTier.MANUAL, caption_format="json3"
                    ),
                    meta=meta,
                    provider=None,
                    ref=None,
                ),
            ),
        )
        monkeypatch.setattr(TranscriptFetcher, "list", lambda self, source: manifest)

        assert "MIT OpenCourseWare" in run(["abc", "--list"]).text

    def test_a_long_language_list_says_how_many_it_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing is truncated silently. A lecture with auto-translations
        offers well over a hundred languages, and a bare list of twenty reads
        as though that were all of them."""
        from youtube_transcript_notes.api import TranscriptFetcher
        from youtube_transcript_notes.cli import MAX_LISTED_LANGUAGES
        from youtube_transcript_notes.models import LectureMeta, TrustTier
        from youtube_transcript_notes.resolve import Track, TrackHandle, TrackManifest

        meta = LectureMeta(source_id="abc", title="Lecture 1")
        extra = 7
        manifest = TrackManifest(
            meta=meta,
            tracks=tuple(
                TrackHandle(
                    track=Track(
                        language=f"l{n:03d}",
                        tier=TrustTier.TRANSLATED,
                        caption_format="json3",
                    ),
                    meta=meta,
                    provider=None,
                    ref=None,
                )
                for n in range(MAX_LISTED_LANGUAGES + extra)
            ),
        )
        monkeypatch.setattr(TranscriptFetcher, "list", lambda self, source: manifest)

        assert f"... and {extra} more" in run(["abc", "--list"]).text

    def test_a_short_language_list_is_shown_whole(self) -> None:
        assert "more" not in run([SOURCE, "--list"]).text.splitlines()[1]

    def test_list_downloads_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from youtube_transcript_notes.sources import LocalProvider

        def explode(self, ref):
            raise AssertionError("--list downloaded a caption payload")

        monkeypatch.setattr(LocalProvider, "load", explode)

        assert run([SOURCE, "--list"]).exit_code == EXIT_OK


class TestBudget:
    def test_budget_reaches_the_context_renderer(self) -> None:
        generous = run([SOURCE, "--format", "context", "--budget", "100000"]).text
        tight = run([SOURCE, "--format", "context", "--budget", "800"]).text

        assert len(tight) < len(generous)
        assert "## Omitted" in tight
        assert "## Omitted" not in generous

    def test_the_omission_note_tells_a_cli_user_what_to_do(self) -> None:
        """It used to advise `ContextRenderer(budget=...)`, which is only
        reachable from Python — advice the reader could not act on."""
        text = run([SOURCE, "--format", "context", "--budget", "800"]).text

        assert "--budget" in text

    def test_the_default_budget_still_applies_without_the_flag(self) -> None:
        assert run([SOURCE, "--format", "context"]).exit_code == EXIT_OK

    def test_budget_with_a_format_that_ignores_it_is_refused(self) -> None:
        # A TypeError from the renderer would be caught by the batch loop and
        # reported as a failed lecture, which is not what went wrong.
        with pytest.raises(SystemExit):
            run([SOURCE, "--format", "markdown", "--budget", "500"])

    def test_the_refusal_names_the_formats_that_do_take_one(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        with pytest.raises(SystemExit):
            run([SOURCE, "--format", "plain", "--budget", "500"])

        assert "context" in capsys.readouterr().err


class TestSelection:
    def test_languages_are_a_preference_list(self, tmp_path: Path) -> None:
        (tmp_path / "lec.manual.en.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n", encoding="utf-8"
        )
        result = run([str(tmp_path), "--languages", "de", "en"])

        assert "hello" in result.text

    def test_tiers_can_be_restricted(self) -> None:
        auto = run([SOURCE, "--tiers", "asr_platform"]).text
        default = run([SOURCE]).text

        assert auto != default
        assert "PROFESSOR" in default  # speaker labels are human-written only
        assert "PROFESSOR" not in auto

    def test_an_impossible_selection_reports_what_was_available(self) -> None:
        result = run([SOURCE, "--languages", "ja"])

        assert result.exit_code == EXIT_FAILED
        assert "mit6006-lec1.manual.en.json3" in result.report


class TestBatchesSurviveFailures:
    def test_one_bad_source_does_not_lose_the_others(self) -> None:
        result = run([MISSING, SOURCE])

        assert result.exit_code == EXIT_PARTIAL
        assert MISSING in result.report  # the failure is reported
        assert "# mit6006-lec1" in result.text  # and so is the success

    def test_a_failure_never_lands_in_the_document(self) -> None:
        """The reason failures are on their own stream.

        `youtube-transcript-notes a b > notes.md` used to write the error for
        `a` into the top of the notes, where it looks like part of the lecture.
        """
        result = run([MISSING, SOURCE])

        assert MISSING not in result.text
        assert "Could not tell what" not in result.text

    def test_everything_failing_is_a_different_exit_code(self) -> None:
        result = run([MISSING, MISSING + "-2"])

        assert result.exit_code == EXIT_FAILED
        assert result.report.count("Could not tell what") == 2
        assert result.text == ""

    def test_a_mistyped_source_says_something_useful(self) -> None:
        # Reporting a provider-registry miss would be true and useless.
        report = run([MISSING]).report

        assert "Could not tell what" in report
        assert "Check the path" in report

    def test_all_succeeding_is_a_clean_exit(self) -> None:
        assert run([SOURCE, SOURCE]).exit_code == EXIT_OK

    def test_an_unexpected_error_still_costs_only_one_lecture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patched at `TrackHandle.fetch` rather than at `TranscriptFetcher.fetch`: the
        # CLI takes the two stages separately so it can see whether discovery
        # fell back to cache, so the convenience wrapper is no longer on the
        # path. This is the better seam anyway — it plants the failure inside
        # the pipeline rather than in a shortcut around it.
        from youtube_transcript_notes.resolve import TrackHandle

        calls = {"n": 0}
        original = TrackHandle.fetch

        def flaky(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("something nobody predicted")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(TrackHandle, "fetch", flaky)
        result = run([SOURCE, SOURCE])

        assert result.exit_code == EXIT_PARTIAL
        assert "something nobody predicted" in result.report
        assert "# mit6006-lec1" in result.text


class TestPlaylistsExpand:
    """One playlist argument becomes one lecture per video, each isolated
    exactly like a source typed by hand."""

    PLAYLIST = "https://www.youtube.com/playlist?list=PL123"
    IDS = ("Video00000A", "Video00000B", "Video00000C")

    @classmethod
    def _wire(cls, monkeypatch: pytest.MonkeyPatch, dead: str | None = None) -> None:
        """Serve a three-video course through every transport seam.

        Each video arrives with its own id and title, so filenames and
        failure reports can be told apart downstream. `dead` names one video
        that fails per-video, the way a deleted lecture would.
        """
        from youtube_transcript_notes.sources import youtube

        info = json.loads(
            (FIXTURES / "youtube" / "HtSuA80QTyo.info.json").read_text(encoding="utf-8")
        )

        def flat(url: str) -> dict:
            return {
                "id": "PL123",
                "title": "A course",
                "entries": [{"id": video, "title": video} for video in cls.IDS],
            }

        def per_video(url: str) -> dict:
            video = url.rsplit("v=", 1)[1]
            if video == dead:
                raise AcquisitionFailed(source=url, detail="this one is gone")
            return {**info, "id": video, "title": f"Lecture {video}"}

        monkeypatch.setattr(youtube, "_extract_flat_info", flat)
        monkeypatch.setattr(youtube, "_extract_info", per_video)
        monkeypatch.setattr(
            youtube,
            "_open_url",
            lambda url, source="youtube": load_caption("mit6006-lec1.manual.en.json3"),
        )

    def test_a_playlist_becomes_one_document_per_video_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._wire(monkeypatch)

        result = run([self.PLAYLIST])

        assert result.exit_code == EXIT_OK
        positions = [result.text.find(f"# Lecture {video}") for video in self.IDS]
        assert all(found >= 0 for found in positions)
        assert positions == sorted(positions)

    def test_one_dead_video_costs_itself_not_the_course(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._wire(monkeypatch, dead=self.IDS[1])

        result = run([self.PLAYLIST])

        assert result.exit_code == EXIT_PARTIAL
        assert f"watch?v={self.IDS[1]}" in result.report
        assert f"# Lecture {self.IDS[0]}" in result.text
        assert f"# Lecture {self.IDS[2]}" in result.text

    def test_a_failed_expansion_costs_the_playlist_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from youtube_transcript_notes.sources import youtube

        def broken(url: str) -> dict:
            raise AcquisitionFailed(source=url, detail="the transport is down")

        monkeypatch.setattr(youtube, "_extract_flat_info", broken)

        result = run([self.PLAYLIST, SOURCE])

        assert result.exit_code == EXIT_PARTIAL
        assert self.PLAYLIST in result.report
        assert "# mit6006-lec1" in result.text

    def test_an_unclassified_expansion_failure_still_costs_one_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from youtube_transcript_notes.sources import youtube

        def explode(url: str) -> dict:
            raise ValueError("something nobody predicted")

        monkeypatch.setattr(youtube, "_extract_flat_info", explode)

        result = run([self.PLAYLIST, SOURCE])

        assert result.exit_code == EXIT_PARTIAL
        assert "something nobody predicted" in result.report
        assert "# mit6006-lec1" in result.text

    def test_list_shows_every_video(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._wire(monkeypatch)

        result = run([self.PLAYLIST, "--list"])

        assert result.exit_code == EXIT_OK
        for video in self.IDS:
            assert f"Lecture {video}" in result.text

    def test_out_writes_one_file_per_video(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._wire(monkeypatch)

        result = run([self.PLAYLIST, "--out", str(tmp_path)])

        names = [output.path.name for output in result.files]
        assert len(names) == len(set(names)) == len(self.IDS)

    def test_the_envelope_keys_a_child_failure_by_its_watch_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The watch URL is what an agent can act on — retry it, drop it,
        report it. The playlist as typed identifies the whole batch, which is
        not what failed."""
        self._wire(monkeypatch, dead=self.IDS[1])

        payload = json.loads(run([self.PLAYLIST, "--json"]).text)

        assert len(payload["results"]) == 2
        assert payload["errors"][0]["source"].endswith(f"watch?v={self.IDS[1]}")

    def test_a_stale_expansion_is_announced(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A course expanded from cache during an outage reads exactly like
        one enumerated a moment ago, so the difference is said out loud —
        the same rule stale manifests already follow."""
        from youtube_transcript_notes.sources import youtube

        self._wire(monkeypatch)
        assert run([self.PLAYLIST, "--cache", str(tmp_path)]).exit_code == EXIT_OK

        def broken(url: str) -> dict:
            raise AcquisitionFailed(source=url, detail="the transport is down")

        monkeypatch.setattr(youtube, "_extract_flat_info", broken)
        result = run([self.PLAYLIST, "--cache", str(tmp_path)])

        assert result.exit_code == EXIT_OK
        assert "served from cache" in result.report
        assert "the transport is down" in result.report

    def test_a_channel_is_still_refused_with_the_new_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._wire(monkeypatch)

        result = run(["https://www.youtube.com/@MITOCW"])

        assert result.exit_code == EXIT_FAILED
        assert "channel" in result.report
        assert "expanded into its videos automatically" in result.report


class TestServedFromCacheIsAnnounced:
    """A run the transport could not reach must not look like one that did.

    The document on stdout is real and complete, so this is not a failure and
    must not touch the exit code. It is still something the reader has to act
    on — the transport needs fixing — so it goes to stderr with the failures.
    """

    @staticmethod
    def _warm(cache_root: Path) -> None:
        """Fetch the lecture once, into the cache the CLI will use."""
        from youtube_transcript_notes.cache import Cache
        from youtube_transcript_notes.sources import youtube

        info = json.loads(
            (FIXTURES / "youtube" / "HtSuA80QTyo.info.json").read_text(encoding="utf-8")
        )
        youtube.YouTubeProvider(
            extractor=lambda url: info,
            opener=lambda url: load_caption("mit6006-lec1.manual.en.json3"),
            cache=Cache(cache_root),
        ).list(LECTURE).find(["en"]).fetch()

    @staticmethod
    def _break_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
        from youtube_transcript_notes.sources import youtube

        def broken(url: str) -> dict:
            raise AcquisitionFailed(source=url, detail="the transport is down")

        monkeypatch.setattr(youtube, "_extract_info", broken)

    def test_the_lecture_still_renders_and_the_report_says_why(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._warm(tmp_path)
        self._break_the_transport(monkeypatch)

        result = run([LECTURE, "--cache", str(tmp_path)])

        assert result.exit_code == EXIT_OK
        assert result.text.startswith("# Lecture 1: Algorithmic Thinking")
        assert "served from cache" in result.report
        assert "the transport is down" in result.report

    def test_listing_says_it_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._warm(tmp_path)
        self._break_the_transport(monkeypatch)

        result = run([LECTURE, "--list", "--cache", str(tmp_path)])

        assert result.exit_code == EXIT_OK
        assert "track(s)" in result.text
        assert "served from cache" in result.report

    def test_the_envelope_separates_warnings_from_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent that read this as a failure would re-fetch what it has."""
        self._warm(tmp_path)
        self._break_the_transport(monkeypatch)

        payload = json.loads(run([LECTURE, "--json", "--cache", str(tmp_path)]).text)

        assert payload["ok"] is True
        assert payload["errors"] == []
        assert payload["warnings"][0]["code"] == "ACQUISITION_FAILED"
        assert payload["warnings"][0]["source"] == LECTURE

    def test_a_notice_and_a_failure_both_reach_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._warm(tmp_path)
        self._break_the_transport(monkeypatch)

        result = run([LECTURE, MISSING, "--cache", str(tmp_path)])

        assert result.exit_code == EXIT_PARTIAL
        assert "served from cache" in result.report
        assert "Could not tell what" in result.report

    def test_an_ordinary_run_says_nothing(self) -> None:
        assert run([SOURCE]).report == ""


class TestJsonEnvelope:
    def test_success(self) -> None:
        payload = json.loads(run([SOURCE, "--json"]).text)

        assert payload["ok"] is True
        assert payload["errors"] == []
        assert payload["results"][0].startswith("# mit6006-lec1")

    def test_failures_carry_machine_readable_remedies(self) -> None:
        result = run([MISSING, "--json"])
        payload = json.loads(result.text)

        assert payload["ok"] is False
        assert result.exit_code == EXIT_FAILED

        error = payload["errors"][0]
        assert error["source"] == MISSING
        assert error["code"] == "UNKNOWN_PROVIDER"
        assert error["try"]  # an agent can act on this without parsing prose

    def test_the_envelope_is_valid_json_even_when_mixed(self) -> None:
        payload = json.loads(run([MISSING, SOURCE, "--json"]).text)

        assert len(payload["results"]) == 1
        assert len(payload["errors"]) == 1


def _remote_identity(monkeypatch: pytest.MonkeyPatch, identity: dict[str, str]) -> None:
    """Make the caption fixture arrive as a lecture named from somewhere else.

    The overwrite finding is about titles chosen upstream, and the local
    provider names a lecture after its own file — title and id are the same
    string, so a local source can never express the case. Only a remote one
    can, hence the substitution.

    `identity` is read on every fetch rather than captured, so a test can
    change the id between two invocations and model the same title arriving
    from a genuinely different video.
    """
    from youtube_transcript_notes.models import LectureMeta
    from youtube_transcript_notes.resolve import TrackHandle

    real = TrackHandle.fetch

    def named_from_upstream(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        lecture = real(self, *args, **kwargs)
        meta = LectureMeta(source_id=identity["source_id"], title=identity["title"])
        return type(lecture)(
            meta=meta, sections=lecture.sections, provenance=lecture.provenance
        )

    monkeypatch.setattr(TrackHandle, "fetch", named_from_upstream)


class TestWritingFiles:
    def test_run_decides_what_to_write_without_writing_it(self, tmp_path: Path) -> None:
        """The property that keeps `run` testable — and `main` the only effect."""
        target = tmp_path / "vault"
        result = run([SOURCE, "--out", str(target)])

        assert [output.path.name for output in result.files] == ["mit6006-lec1.md"]
        assert result.files[0].text.startswith("# mit6006-lec1")
        assert not target.exists()

    def test_main_writes_the_files(self, tmp_path: Path) -> None:
        target = tmp_path / "vault"

        assert main([SOURCE, "--out", str(target)]) == EXIT_OK
        assert (
            (target / "mit6006-lec1.md")
            .read_text(encoding="utf-8")
            .startswith("# mit6006-lec1")
        )

    def test_the_extension_comes_from_the_renderer(self, tmp_path: Path) -> None:
        for fmt, extension in [("plain", "txt"), ("jsonl", "jsonl"), ("md", "md")]:
            result = run([SOURCE, "--format", fmt, "--out", str(tmp_path)])
            assert result.files[0].path.suffix == f".{extension}"

    def test_re_running_replaces_rather_than_accumulates(self, tmp_path: Path) -> None:
        """Re-rendering is the normal case; a pile of `notes (3).md` is not.

        Still true with no-clobber on, and without `--force`: the file already
        there holds exactly this lecture, so nothing is being destroyed and
        there is nothing to protect. It reports `unchanged` rather than
        claiming a write it did not perform.
        """
        assert main([SOURCE, "--out", str(tmp_path)]) == EXIT_OK
        assert main([SOURCE, "--out", str(tmp_path)]) == EXIT_OK

        assert [path.name for path in sorted(tmp_path.iterdir())] == ["mit6006-lec1.md"]

    def test_an_unchanged_file_says_so_rather_than_claiming_a_write(
        self, tmp_path: Path, capsys
    ) -> None:
        main([SOURCE, "--out", str(tmp_path)])
        capsys.readouterr()

        main([SOURCE, "--out", str(tmp_path)])

        assert capsys.readouterr().out.startswith("unchanged ")

    def test_a_pre_existing_note_at_the_same_name_is_refused(
        self, tmp_path: Path, capsys
    ) -> None:
        """The finding itself. A caption file named after something already in
        the vault replaced it, reported success, and left nothing to restore."""
        mine = tmp_path / "mit6006-lec1.md"
        mine.write_text("notes I wrote myself", encoding="utf-8")

        code = main([SOURCE, "--out", str(tmp_path)])
        captured = capsys.readouterr()

        assert code == EXIT_FAILED
        assert mine.read_text(encoding="utf-8") == "notes I wrote myself"
        assert "already exists" in captured.err
        assert "--force" in captured.err
        assert "wrote " not in captured.out

    def test_force_replaces_a_file_that_is_in_the_way(self, tmp_path: Path) -> None:
        mine = tmp_path / "mit6006-lec1.md"
        mine.write_text("notes I wrote myself", encoding="utf-8")

        assert main([SOURCE, "--out", str(tmp_path), "--force"]) == EXIT_OK
        assert mine.read_text(encoding="utf-8").startswith("# mit6006-lec1")

    def test_a_refusal_leaves_no_litter_behind(self, tmp_path: Path) -> None:
        """The claim is taken with O_EXCL and released on failure, so a refused
        write must not leave the empty file it briefly considered creating."""
        (tmp_path / "mit6006-lec1.md").write_text("mine", encoding="utf-8")

        main([SOURCE, "--out", str(tmp_path)])

        assert [p.name for p in sorted(tmp_path.iterdir())] == ["mit6006-lec1.md"]

    def test_force_without_out_is_refused(self) -> None:
        with pytest.raises(SystemExit):
            run([SOURCE, "--force"])

    def test_an_oversized_file_is_dismissed_without_being_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The identity check can only answer True for a file the same size as
        the document, so anything larger — a video parked at the note's name —
        must be dismissed before it is pulled into memory."""
        from youtube_transcript_notes import cli

        parked = tmp_path / "note.md"
        parked.write_bytes(b"x" * 4096)
        output = cli.OutputFile(path=parked, text="short note\n")

        def reads_anyway(self, *args, **kwargs):
            raise AssertionError("the oversized file was read")

        monkeypatch.setattr(type(parked), "read_text", reads_anyway)

        assert cli._already_says(output) is False

    def test_an_empty_transcript_writes_no_file_and_says_so(
        self, tmp_path: Path, capsys
    ) -> None:
        """It used to write a note holding a title and nothing else, and exit
        0 — a failure indistinguishable from a lecture that simply had little
        to say."""
        source = tmp_path / "src"
        source.mkdir()
        (source / "lec.en.vtt").write_text("WEBVTT\n\nno timings here\n", "utf-8")
        target = tmp_path / "out"

        code = main([str(source / "lec.en.vtt"), "--out", str(target)])
        captured = capsys.readouterr()

        assert code == EXIT_FAILED
        assert not target.exists()
        assert "holds no text" in captured.err

    def test_an_empty_transcript_costs_one_lecture_not_the_folder(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "course"
        source.mkdir()
        (source / "week-01.en.vtt").write_text("WEBVTT\n\nno timings\n", "utf-8")
        (source / "week-02.en.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nreal content here\n", "utf-8"
        )
        target = tmp_path / "out"

        assert main([str(source), "--out", str(target)]) == EXIT_PARTIAL
        assert [p.name for p in sorted(target.iterdir())] == ["week-02.md"]

    def test_something_unreadable_in_the_way_is_still_refused(
        self, tmp_path: Path, capsys
    ) -> None:
        """Whatever it is, it is not this lecture. A file the tool cannot even
        read is the last thing it should decide to replace."""
        (tmp_path / "mit6006-lec1.md").write_bytes(b"\xff\xfe\x00binary")

        code = main([SOURCE, "--out", str(tmp_path)])

        assert code == EXIT_FAILED
        assert (tmp_path / "mit6006-lec1.md").read_bytes().startswith(b"\xff\xfe")
        assert "already exists" in capsys.readouterr().err

    def test_a_directory_in_the_way_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "mit6006-lec1.md").mkdir()

        assert main([SOURCE, "--out", str(tmp_path)]) == EXIT_FAILED
        assert (tmp_path / "mit6006-lec1.md").is_dir()

    def test_a_refusal_is_inside_the_json_document(
        self, tmp_path: Path, capsys
    ) -> None:
        (tmp_path / "mit6006-lec1.md").write_text("mine", encoding="utf-8")

        main([SOURCE, "--out", str(tmp_path), "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)

        assert payload["ok"] is False
        assert payload["files"] == []
        assert payload["errors"][0]["code"] == "OUTPUT_EXISTS"
        # The promise `--json` makes: one self-contained document. A write that
        # failed used to arrive as bare prose on stderr, outside the JSON a
        # consumer is reading, beside `"ok": true`.
        assert captured.err == ""

    def test_a_write_that_failed_is_not_reported_as_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        real_open = Path.open

        def refuse(self, *args, **kwargs):
            if self.name.endswith(".partial"):
                raise OSError("read-only file system")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", refuse)
        main([SOURCE, "--out", str(tmp_path), "--json"])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)

        assert payload["ok"] is False
        assert payload["files"] == []
        assert payload["errors"][0]["code"] == "OUTPUT_UNWRITABLE"
        assert captured.err == ""

    def test_an_unexpected_write_failure_still_costs_one_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """Anything unclassified costs one note, not the run — and not a
        traceback that loses every note already written."""

        def explode(*args, **kwargs):
            raise ValueError("something nobody predicted")

        monkeypatch.setattr(cli, "atomic_write", explode)
        code = main([SOURCE, "--out", str(tmp_path)])

        assert code == EXIT_FAILED
        assert "something nobody predicted" in capsys.readouterr().err

    def test_a_glossary_failure_is_inside_the_json_document(
        self, tmp_path: Path, capsys
    ) -> None:
        """The early return had its own path to stderr, which `--json` was
        never meant to have."""
        main([SOURCE, "--json", "--glossary", str(tmp_path / "nope.txt")])
        captured = capsys.readouterr()

        assert json.loads(captured.out)["ok"] is False
        assert captured.err == ""

    def test_two_lectures_sharing_a_title_do_not_share_a_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from youtube_transcript_notes.models import LectureMeta
        from youtube_transcript_notes.resolve import TrackHandle

        real = TrackHandle.fetch
        seen = {"n": 0}

        def same_title(self, *args, **kwargs):
            seen["n"] += 1
            lecture = real(self, *args, **kwargs)
            meta = LectureMeta(source_id=f"vid{seen['n']}", title="Lecture 1")
            return type(lecture)(
                meta=meta, sections=lecture.sections, provenance=lecture.provenance
            )

        monkeypatch.setattr(TrackHandle, "fetch", same_title)
        result = run([SOURCE, SOURCE, "--out", str(tmp_path)])

        assert [output.path.name for output in result.files] == [
            "Lecture 1 (vid1).md",
            "Lecture 1 (vid2).md",
        ]

    def test_a_pre_existing_unrelated_note_is_not_replaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The audit's High finding, as the vault owner would meet it.

        Someone publishes a lecture titled after a note already in the vault.
        Before the fix the title alone decided the filename, so that note was
        replaced — no confirmation, no recovery, and a success message.
        """
        mine = tmp_path / "Lecture 1.md"
        mine.write_text("notes I wrote myself", encoding="utf-8")

        _remote_identity(monkeypatch, {"title": "Lecture 1", "source_id": "hostile1"})

        assert main([SOURCE, "--out", str(tmp_path)]) == EXIT_OK
        assert mine.read_text(encoding="utf-8") == "notes I wrote myself"
        assert sorted(path.name for path in tmp_path.iterdir()) == [
            "Lecture 1 (hostile1).md",
            "Lecture 1.md",
        ]

    def test_a_shared_title_survives_separate_invocations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`taken` lives for one run only, so it cannot be what keeps these apart.

        The pre-fix suite proved this for two lectures in a single command and
        never for two commands, which is the ordinary way a course is
        processed — one lecture at a time, as each is published.
        """
        identity = {"title": "Lecture 1", "source_id": "vid1"}
        _remote_identity(monkeypatch, identity)

        assert main([SOURCE, "--out", str(tmp_path)]) == EXIT_OK
        identity["source_id"] = "vid2"
        assert main([SOURCE, "--out", str(tmp_path)]) == EXIT_OK

        assert sorted(path.name for path in tmp_path.iterdir()) == [
            "Lecture 1 (vid1).md",
            "Lecture 1 (vid2).md",
        ]

    def test_re_running_one_remote_lecture_still_replaces_its_own_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No-clobber must not become never-update: the id is stable, so the
        second run writes the same name the first one did."""
        _remote_identity(monkeypatch, {"title": "Lecture 1", "source_id": "vid1"})

        assert main([SOURCE, "--out", str(tmp_path)]) == EXIT_OK
        assert main([SOURCE, "--out", str(tmp_path)]) == EXIT_OK

        assert [path.name for path in tmp_path.iterdir()] == ["Lecture 1 (vid1).md"]

    def test_a_failure_writes_no_file_for_itself(self, tmp_path: Path) -> None:
        code = main([MISSING, SOURCE, "--out", str(tmp_path)])

        assert code == EXIT_PARTIAL
        assert [path.name for path in tmp_path.iterdir()] == ["mit6006-lec1.md"]

    def test_nothing_succeeding_writes_nothing(self, tmp_path: Path) -> None:
        target = tmp_path / "vault"

        assert main([MISSING, "--out", str(target)]) == EXIT_FAILED
        assert not target.exists()

    def test_stdout_reports_what_was_written(self, tmp_path: Path) -> None:
        text = run([SOURCE, "--out", str(tmp_path)]).text

        assert text.startswith("wrote ")
        assert "mit6006-lec1.md" in text
        # The document went to the file, not to both places.
        assert "PROFESSOR" not in text

    def test_an_unwritable_target_is_reported_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        real_open = Path.open

        # Only the scratch file is refused, so the run still gets as far as
        # writing — the caption files it reads on the way there open fine.
        def refuse(self, *args, **kwargs):
            if self.name.endswith(".partial"):
                raise OSError("read-only file system")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", refuse)
        code = main([SOURCE, "--out", str(tmp_path)])
        captured = capsys.readouterr()

        assert code == EXIT_FAILED
        assert "Could not write" in captured.err
        # The operating system's own words survive into the message; without
        # them the reader is told a write failed and not why.
        assert "read-only file system" in captured.err
        # And nothing claims otherwise on stdout, which is the whole point.
        assert "wrote " not in captured.out

    def test_out_and_list_together_are_refused(self) -> None:
        # A manifest is not a lecture, so there is nothing to name a file after.
        with pytest.raises(SystemExit):
            run([SOURCE, "--out", "somewhere", "--list"])

    def test_the_json_envelope_reports_paths_instead_of_documents(
        self, tmp_path: Path
    ) -> None:
        payload = json.loads(run([SOURCE, "--out", str(tmp_path), "--json"]).text)

        assert payload["ok"] is True
        assert payload["results"] == []
        assert payload["files"] == [str(tmp_path / "mit6006-lec1.md")]

    def test_the_json_envelope_still_carries_documents_without_out(self) -> None:
        payload = json.loads(run([SOURCE, "--json"]).text)

        assert payload["files"] == []
        assert payload["results"][0].startswith("# mit6006-lec1")


class TestBatchedOutput:
    def test_several_lectures_of_jsonl_stay_valid_jsonl(self) -> None:
        """`render_many` exists for this; the CLI used to join with blank lines."""
        text = run([SOURCE, SOURCE, "--format", "jsonl"]).text

        assert text.count("\n\n") == 0
        for line in text.splitlines():
            json.loads(line)

    def test_several_lectures_of_markdown_are_separated(self) -> None:
        text = run([SOURCE, SOURCE, "--format", "markdown"]).text

        assert text.count("# mit6006-lec1") == 2
        assert "\n\n\n" in text


class TestCaching:
    def test_a_cache_directory_can_be_chosen(self, tmp_path: Path) -> None:
        assert run([SOURCE, "--cache", str(tmp_path)]).exit_code == EXIT_OK

    def test_caching_can_be_turned_off(self) -> None:
        assert run([SOURCE, "--no-cache"]).exit_code == EXIT_OK

    def test_no_cache_directory_is_created_for_local_sources(
        self, tmp_path: Path
    ) -> None:
        run([SOURCE, "--cache", str(tmp_path / "unused")])
        assert not (tmp_path / "unused").exists()


class TestEntryPoint:
    def test_main_prints_and_returns_the_exit_code(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        code = main([SOURCE])

        assert code == EXIT_OK
        assert capsys.readouterr().out.startswith("# mit6006-lec1")

    def test_main_reports_failure_on_stderr(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        code = main([MISSING])
        captured = capsys.readouterr()

        assert code == EXIT_FAILED
        assert MISSING in captured.err
        assert captured.out == ""

    def test_nothing_to_print_prints_nothing(
        self, capsys: pytest.CaptureFixture, tmp_path: Path
    ) -> None:
        # Written rather than printed, so there is genuinely nothing for
        # stdout beyond the one line naming the file. This used to stub out
        # `cli.run`, which `main` no longer calls — it now exercises the real
        # path instead of a fixture agreeing with itself.
        assert main([MISSING, "--out", str(tmp_path)]) == EXIT_FAILED
        assert capsys.readouterr().out == ""

    def test_run_is_decide_then_present(self) -> None:
        """`run` and `main` must not drift into two different runs."""
        argv = [SOURCE, "--format", "plain"]

        assert cli.run(argv) == cli._present(cli._decide(argv))

    def test_python_dash_m_actually_works(self) -> None:
        # The only check that the installed entry point runs at all. In-process
        # tests cannot catch a broken __main__.py.
        import subprocess
        import sys

        finished = subprocess.run(
            [
                sys.executable,
                "-m",
                "youtube_transcript_notes",
                SOURCE,
                "--format",
                "plain",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert finished.returncode == EXIT_OK
        assert "Creative Commons license" in finished.stdout

    def test_python_dash_m_reports_failure_through_its_exit_code(self) -> None:
        import subprocess
        import sys

        finished = subprocess.run(
            [sys.executable, "-m", "youtube_transcript_notes", MISSING],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert finished.returncode == EXIT_FAILED

    def test_version(self, capsys: pytest.CaptureFixture) -> None:
        from youtube_transcript_notes import __version__

        with pytest.raises(SystemExit):
            run(["--version"])

        assert __version__ in capsys.readouterr().out

    def test_the_reported_version_is_the_installed_one(self) -> None:
        """`--version` used to prove only that the CLI prints the same string
        the package holds — and the package held a *second* copy of it, beside
        the one in pyproject.toml, with nothing keeping the two equal.

        Reading the number out of the installed distribution is what closes
        that: it is the version pip resolved and a user would report in a bug.
        `pyproject.toml` now derives it from `__init__.py`, so this asserts
        that derivation actually happened rather than restating a constant.
        """
        from importlib.metadata import version

        from youtube_transcript_notes import __version__

        assert version("youtube-transcript-notes") == __version__


class TestCorrectingNames:
    """`--glossary` and `--corrections`, the two ways to say what a word is.

    Both are read once for the whole run and neither ever edits the
    transcript: a correction is rendered beside the words that were actually
    said, so the note still says what the recording says.
    """

    def test_a_glossary_marks_a_name_without_replacing_it(self, tmp_path: Path) -> None:
        terms = tmp_path / "terms.txt"
        terms.write_text("Erik Demaine: Erik Domane\n", encoding="utf-8")

        result = run([SOURCE, "--glossary", str(terms)])

        assert result.exit_code == EXIT_OK
        assert "Erik Domane [Erik Demaine]" in result.text
        assert "## Corrections" in result.text

    def test_a_corrections_table_is_applied_everywhere_the_phrase_occurs(
        self, tmp_path: Path
    ) -> None:
        table = tmp_path / "found.json"
        table.write_text(
            json.dumps(
                [{"wrong": "Erik Domane", "right": "Erik Demaine", "evidence": "0:22"}]
            ),
            encoding="utf-8",
        )

        result = run([SOURCE, "--corrections", str(table)])

        assert "Erik Domane [Erik Demaine]" in result.text
        assert "0:22" in result.text

    def test_no_corrections_means_no_appendix(self) -> None:
        assert "## Corrections" not in run([SOURCE]).text

    def test_a_corrections_file_that_is_not_json_stops_the_run(
        self, tmp_path: Path
    ) -> None:
        table = tmp_path / "found.json"
        table.write_text("{not json", encoding="utf-8")

        result = run([SOURCE, "--corrections", str(table)])

        # Charged to the run and not to a lecture: the lectures had nothing to
        # do with it, and the whole run would have used the same bad file.
        assert result.exit_code == EXIT_FAILED
        assert result.text == ""
        assert "corrections" in result.report.lower()

    def test_a_corrections_file_that_is_not_a_list_says_so(
        self, tmp_path: Path
    ) -> None:
        table = tmp_path / "found.json"
        table.write_text('{"wrong": "a", "right": "b"}', encoding="utf-8")

        result = run([SOURCE, "--corrections", str(table)])

        assert result.exit_code == EXIT_FAILED
        assert "list" in result.report

    def test_too_many_corrections_are_refused_before_they_are_scanned(
        self, tmp_path: Path
    ) -> None:
        """Every correction is scanned against every passage, so the count is
        the bound that matters — a small file can name an enormous amount of
        work."""
        table = tmp_path / "found.json"
        table.write_text(
            json.dumps([{"wrong": f"a{n}", "right": "b"} for n in range(10_001)]),
            encoding="utf-8",
        )

        result = run([SOURCE, "--corrections", str(table)])

        assert result.exit_code == EXIT_FAILED
        assert "10001" in result.report

    def test_a_missing_glossary_is_reported_rather_than_ignored(
        self, tmp_path: Path
    ) -> None:
        result = run([SOURCE, "--glossary", str(tmp_path / "nope.txt")])

        assert result.exit_code == EXIT_FAILED

    def test_this_runs_findings_beat_a_standing_list(self, tmp_path: Path) -> None:
        terms = tmp_path / "terms.txt"
        terms.write_text("Erik Demaine: Erik Domane\n", encoding="utf-8")
        table = tmp_path / "found.json"
        table.write_text(
            json.dumps([{"wrong": "Erik Domane", "right": "Erik Demaine Jr"}]),
            encoding="utf-8",
        )

        result = run([SOURCE, "--glossary", str(terms), "--corrections", str(table)])

        assert "Erik Domane [Erik Demaine Jr]" in result.text

    def test_a_glossary_that_is_not_text_says_so_rather_than_crashing(
        self, tmp_path: Path
    ) -> None:
        terms = tmp_path / "terms.txt"
        terms.write_bytes(b"Erik Demaine: \xff\xfe not utf-8\n")

        result = run([SOURCE, "--glossary", str(terms)])

        assert result.exit_code == EXIT_FAILED
        assert "UTF-8" in result.report


class TestRendererFailureIsolation:
    """A lecture that will not render costs one lecture, not the batch.

    Fetching and writing both isolate failures per lecture; rendering sits
    between them and used to have nothing, so one renderer exception escaped
    `run` as a traceback and lost every lecture already fetched.
    """

    @staticmethod
    def _explode_on_second(monkeypatch: pytest.MonkeyPatch) -> None:
        """The real renderer for the first lecture, a crash for the second."""
        from youtube_transcript_notes.render.markdown import MarkdownRenderer

        real = MarkdownRenderer.render
        calls = {"n": 0}

        def flaky(self, lecture):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("render exploded")
            return real(self, lecture)

        monkeypatch.setattr(MarkdownRenderer, "render", flaky)

    def test_a_renderer_crash_costs_one_lecture_not_the_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._explode_on_second(monkeypatch)

        result = run([SOURCE, SOURCE])

        assert result.exit_code == EXIT_PARTIAL
        assert result.text.count("# mit6006-lec1") == 1
        assert "mit6006-lec1" in result.report

    def test_a_renderer_crash_with_out_still_writes_the_others(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._explode_on_second(monkeypatch)

        result = run([SOURCE, SOURCE, "--out", str(tmp_path)])

        assert result.exit_code == EXIT_PARTIAL
        assert len(result.files) == 1

    def test_a_renderer_crash_lands_inside_the_json_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._explode_on_second(monkeypatch)

        payload = json.loads(run([SOURCE, SOURCE, "--json"]).text)

        assert payload["ok"] is False
        assert len(payload["results"]) == 1
        assert len(payload["errors"]) == 1
        assert "mit6006-lec1" in payload["errors"][0]["source"]


class TestGlossarySizeAtTheCommandLine:
    def test_too_many_glossary_terms_are_refused_before_they_are_scanned(
        self, tmp_path: Path
    ) -> None:
        from youtube_transcript_notes.limits import MAX_GLOSSARY_TERMS

        terms = tmp_path / "terms.txt"
        terms.write_text(
            "\n".join(f"uniqueterm{n:05}" for n in range(MAX_GLOSSARY_TERMS + 1)),
            encoding="utf-8",
        )

        result = run([SOURCE, "--glossary", str(terms)])

        assert result.exit_code == EXIT_FAILED
        assert f"{MAX_GLOSSARY_TERMS + 1:,} terms" in result.report
