"""Turning lecture titles into filenames.

Worth its own file because the rules are almost entirely about failure cases —
titles containing characters Windows refuses, titles that are reserved device
names, titles that are empty — and none of those show up when you try it once
with a real lecture.
"""

from __future__ import annotations

import pytest

from youtube_transcript_notes.naming import (
    FALLBACK_STEM,
    MAX_STEM,
    filename_for,
    sanitise,
)


class TestSanitise:
    def test_an_ordinary_title_survives_intact(self) -> None:
        assert (
            sanitise("Lecture 4: Dynamic Programming")
            == "Lecture 4 Dynamic Programming"
        )

    @pytest.mark.parametrize("character", list('<>:"/\\|?*'))
    def test_characters_windows_refuses_are_removed(self, character: str) -> None:
        assert character not in sanitise(f"Peak{character}Finding")

    def test_control_characters_are_removed(self) -> None:
        assert sanitise("Peak\x00\x1fFinding") == "Peak Finding"

    def test_runs_of_whitespace_collapse(self) -> None:
        assert sanitise("Peak   \t\n Finding") == "Peak Finding"

    def test_trailing_dots_and_spaces_go(self) -> None:
        """Windows drops them silently, so keeping them is a lie about the name."""
        assert sanitise("Lecture 1. ") == "Lecture 1"
        assert sanitise("What is a peak?") == "What is a peak"

    def test_internal_dots_stay(self) -> None:
        assert sanitise("6.006 Introduction") == "6.006 Introduction"

    def test_non_ascii_survives(self) -> None:
        """Transliterating would make the file harder to find, not safer to write."""
        assert sanitise("Vorlesung 3 — Größenordnungen") == (
            "Vorlesung 3 — Größenordnungen"
        )
        assert sanitise("第一讲 算法思维") == "第一讲 算法思维"

    @pytest.mark.parametrize("name", ["CON", "nul", "Com1", "LPT9", "aux"])
    def test_reserved_device_names_are_escaped(self, name: str) -> None:
        """Windows refuses these whatever extension follows."""
        cleaned = sanitise(name)

        assert cleaned.upper() not in {"CON", "NUL", "COM1", "LPT9", "AUX"}
        assert cleaned.startswith(name)

    def test_a_name_merely_containing_a_device_name_is_left_alone(self) -> None:
        assert sanitise("Concurrency") == "Concurrency"

    def test_a_long_title_is_cut_at_a_word_boundary(self) -> None:
        title = "Peak Finding " * 40
        cleaned = sanitise(title)

        assert len(cleaned) <= MAX_STEM
        assert cleaned.endswith(("Peak", "Finding"))

    def test_a_single_enormous_word_is_cut_mid_word(self) -> None:
        """There is no boundary worth respecting, and half a name beats none."""
        cleaned = sanitise("x" * 400)

        assert cleaned == "x" * MAX_STEM

    def test_nothing_usable_gives_the_empty_string(self) -> None:
        """The caller decides what to do about it — see `filename_for`."""
        assert sanitise("///") == ""
        assert sanitise("   ") == ""
        assert sanitise("") == ""


class TestFilenameFor:
    def test_the_name_carries_the_source_id(self) -> None:
        """A title is not an identity — whoever uploaded the lecture chose it.

        The id is in the name from the start rather than held back for a
        collision, so no title can name a file the tool would otherwise leave
        alone.
        """
        assert filename_for("Peak Finding", "abc", "md") == "Peak Finding (abc).md"

    def test_an_id_that_only_repeats_the_title_is_left_out(self) -> None:
        """Every local file: `sources.local` names a lecture after its stem."""
        assert filename_for("mit6006-lec1", "mit6006-lec1", "md") == "mit6006-lec1.md"

    def test_an_id_differing_from_the_title_in_case_only_is_left_out_too(self) -> None:
        """The two spellings are one file on Windows, so the id adds nothing."""
        assert filename_for("Lecture", "lecture", "md") == "Lecture.md"

    def test_an_untitled_lecture_falls_back_to_its_source_id(self) -> None:
        assert filename_for("", "HtSuA80QTyo", "md") == "HtSuA80QTyo.md"

    def test_a_lecture_with_no_usable_name_at_all_still_gets_one(self) -> None:
        assert filename_for("///", "???", "md") == f"{FALLBACK_STEM}.md"

    def test_the_same_lecture_twice_is_the_same_name(self) -> None:
        """Re-rendering overwrites. A directory of `notes (4).md` helps nobody."""
        assert filename_for("Peak Finding", "abc", "md") == filename_for(
            "Peak Finding", "abc", "md"
        )

    def test_a_file_under_the_bare_title_is_never_a_collision(self) -> None:
        """The heart of the finding, stated as a name.

        `taken` here stands for a note already sitting in the output
        directory. The bare title is not a name the tool claims any more, so
        that note cannot be collided with — and therefore cannot be replaced.
        """
        assert filename_for("Peak Finding", "abc", "md", {"peak finding"}) == (
            "Peak Finding (abc).md"
        )

    def test_collisions_are_case_insensitive(self) -> None:
        """Two names differing only in case are one file on Windows."""
        assert filename_for("PEAK FINDING", "abc", "md", {"peak finding (abc)"}) == (
            "PEAK FINDING (abc 2).md"
        )

    def test_a_second_collision_falls_back_to_a_counter(self) -> None:
        taken = {"peak finding", "peak finding (abc)"}

        assert filename_for("Peak Finding", "abc", "md", taken) == (
            "Peak Finding (abc 2).md"
        )

    def test_a_collision_with_no_source_id_uses_a_counter_directly(self) -> None:
        assert filename_for("Peak Finding", "", "md", {"peak finding"}) == (
            "Peak Finding (2).md"
        )

    def test_the_counter_keeps_going(self) -> None:
        """Reached only by passing the same source three times in one command."""
        taken = {"peak finding", "peak finding (abc)", "peak finding (abc 2)"}

        assert filename_for("Peak Finding", "abc", "md", taken) == (
            "Peak Finding (abc 3).md"
        )
