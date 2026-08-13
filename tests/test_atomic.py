"""Writing a file so that it is either whole or absent.

The cache and `--out` both go through this, and both used to spell it out
themselves with a fixed ``.partial`` scratch name. That name is the thing
under test here: cache paths are derived from track identity, so two of its
processes fetching the same track derive the *same* scratch path, and the
failure is not a crash but one process renaming the other's half-written bytes
into place.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from youtube_transcript_notes.atomic import atomic_write, scratch_path


class TestScratchNames:
    def test_two_writers_never_choose_the_same_scratch_path(
        self, tmp_path: Path
    ) -> None:
        """The whole point. A fixed `.partial` name means two concurrent runs
        fetching the same track write the same file, and one renames the
        other's partial bytes over the target."""
        target = tmp_path / "abc123.txt"

        names = {scratch_path(target) for _ in range(50)}

        assert len(names) == 50

    def test_the_scratch_path_is_beside_its_target(self, tmp_path: Path) -> None:
        """It has to be on the same filesystem, or the rename stops being
        atomic and becomes a copy."""
        target = tmp_path / "nested" / "abc123.txt"

        assert scratch_path(target).parent == target.parent

    def test_the_scratch_name_carries_the_process(self, tmp_path: Path) -> None:
        assert str(os.getpid()) in scratch_path(tmp_path / "abc.txt").name

    def test_a_dot_in_the_name_is_not_mistaken_for_an_extension(
        self, tmp_path: Path
    ) -> None:
        """`with_suffix` would eat everything after the first full stop, and
        lecture titles contain full stops — "Lecture 1. Peak Finding.md"."""
        target = tmp_path / "Lecture 1. Peak Finding.md"

        assert scratch_path(target).name.startswith("Lecture 1. Peak Finding.md.")


class TestAtomicWrite:
    def test_the_text_arrives(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.md"
        atomic_write(target, "hello")

        assert target.read_text(encoding="utf-8") == "hello"

    def test_missing_parent_directories_are_created(self, tmp_path: Path) -> None:
        target = tmp_path / "deep" / "nested" / "notes.md"
        atomic_write(target, "hello")

        assert target.read_text(encoding="utf-8") == "hello"

    def test_nothing_is_left_beside_the_finished_file(self, tmp_path: Path) -> None:
        atomic_write(tmp_path / "notes.md", "hello")

        assert [p.name for p in tmp_path.iterdir()] == ["notes.md"]

    def test_rewriting_replaces_rather_than_appends(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.md"
        atomic_write(target, "first")
        atomic_write(target, "second")

        assert target.read_text(encoding="utf-8") == "second"
        assert [p.name for p in tmp_path.iterdir()] == ["notes.md"]

    def test_unicode_survives_the_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.md"
        atomic_write(target, "café — 数学 — naïve")

        assert target.read_text(encoding="utf-8") == "café — 数学 — naïve"


class TestRefusingAnOccupiedName:
    """`overwrite=False`, which `--out` uses and the cache must never."""

    def test_an_existing_file_is_refused_rather_than_replaced(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.md"
        target.write_text("mine", encoding="utf-8")

        with pytest.raises(FileExistsError):
            atomic_write(target, "theirs", overwrite=False)

        assert target.read_text(encoding="utf-8") == "mine"

    def test_the_refused_file_is_not_deleted_by_the_cleanup(
        self, tmp_path: Path
    ) -> None:
        """The property the whole thing rests on. The failure path unlinks what
        it created, and it must not mistake somebody's file for its own."""
        target = tmp_path / "notes.md"
        target.write_text("mine", encoding="utf-8")

        with pytest.raises(FileExistsError):
            atomic_write(target, "theirs", overwrite=False)

        assert target.exists()
        assert [p.name for p in tmp_path.iterdir()] == ["notes.md"]

    def test_a_free_name_is_written_normally(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.md"
        atomic_write(target, "hello", overwrite=False)

        assert target.read_text(encoding="utf-8") == "hello"
        assert [p.name for p in tmp_path.iterdir()] == ["notes.md"]

    def test_the_claim_is_exclusive_not_a_preflight_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Asserted rather than described, because avoiding the check-then-act
        race is the entire reason this uses O_EXCL — and a preflight `exists()`
        would pass every other test in this class."""
        seen: list[int] = []
        real_open = os.open

        def record(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            seen.append(flags)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", record)
        atomic_write(tmp_path / "notes.md", "hello", overwrite=False)

        assert any(flags & os.O_EXCL for flags in seen)

    def test_a_claimed_name_is_released_when_the_replace_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise a failed write leaves the empty file it claimed, and the
        next run refuses a name nothing is really using."""

        def refuse(self, target):  # type: ignore[no-untyped-def]
            raise OSError("no")

        monkeypatch.setattr(Path, "replace", refuse)

        with pytest.raises(OSError):
            atomic_write(tmp_path / "notes.md", "hello", overwrite=False)

        assert list(tmp_path.iterdir()) == []


class TestFailedWrites:
    def test_a_failed_rename_leaves_no_litter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scratch file left behind sits next to real cache entries looking
        like one, and nothing ever cleans it up."""

        def refuse(self: Path, target: object) -> None:
            raise OSError("rename refused")

        monkeypatch.setattr(Path, "replace", refuse)

        with pytest.raises(OSError, match="rename refused"):
            atomic_write(tmp_path / "notes.md", "hello")

        assert list(tmp_path.iterdir()) == []

    def test_a_failed_write_still_propagates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cleanup must not swallow the failure — the caller decides what
        an unwritable file costs, and for the CLI it costs one lecture."""

        def refuse(self: Path, *args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        # `open` rather than `write_text`: the scratch file goes through an
        # open handle now, so the bytes can be fsynced before the file is given
        # its real name.
        monkeypatch.setattr(Path, "open", refuse)

        with pytest.raises(OSError, match="disk full"):
            atomic_write(tmp_path / "notes.md", "hello")

        assert list(tmp_path.iterdir()) == []


class TestDurability:
    """What "old contents or new, never a mixture" actually requires.

    The claim is about *content*, and it is only true if the content reached
    the disk before the name pointed at it. Without the fsync, a rename can be
    recorded while the data blocks are still in flight: what survives a power
    cut then has the new name, the new length, and a run of zeros where the
    notes should be — which is neither the old contents nor the new ones.
    """

    def test_the_bytes_are_forced_to_disk_before_the_rename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order: list[str] = []

        real_fsync = os.fsync
        real_replace = Path.replace

        def record_fsync(fd: int) -> None:
            order.append("fsync")
            return real_fsync(fd)

        def record_replace(self: Path, target):  # type: ignore[no-untyped-def]
            order.append("replace")
            return real_replace(self, target)

        monkeypatch.setattr(os, "fsync", record_fsync)
        monkeypatch.setattr(Path, "replace", record_replace)

        atomic_write(tmp_path / "notes.md", "hello")

        assert order == ["fsync", "replace"]

    def test_the_content_still_arrives(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.md"
        atomic_write(target, "hello")

        assert target.read_text(encoding="utf-8") == "hello"


class TestConcurrentWriters:
    """Two writers, one destination.

    Unique scratch names stop two writers sharing a *source*. They do nothing
    about two writers replacing the same *destination*, which on Windows fails
    with `PermissionError` — measured at 8.78% of writes with three writers on
    one target before the retry existed.
    """

    def test_a_replace_that_loses_a_race_is_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_replace = Path.replace
        attempts = {"n": 0}

        def busy_once(self: Path, target):  # type: ignore[no-untyped-def]
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise PermissionError(13, "Access is denied")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", busy_once)

        atomic_write(tmp_path / "notes.md", "hello")

        assert attempts["n"] == 2
        assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "hello"

    def test_a_permission_error_that_never_clears_still_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A genuinely read-only target must not be retried forever in the
        hope of it changing its mind."""

        def always_busy(self: Path, target):  # type: ignore[no-untyped-def]
            raise PermissionError(13, "Access is denied")

        monkeypatch.setattr(Path, "replace", always_busy)

        with pytest.raises(PermissionError):
            atomic_write(tmp_path / "notes.md", "hello")

        assert list(tmp_path.iterdir()) == []  # no scratch left behind

    def test_many_writers_on_one_target_all_succeed(self, tmp_path: Path) -> None:
        """The measured case, as a test. Before the retry this failed on
        Windows for close to one write in ten."""
        import threading

        target = tmp_path / "contended.md"
        payload = "x" * 100_000
        failures: list[BaseException] = []

        def write_once() -> None:
            try:
                atomic_write(target, payload)
            except OSError as error:
                failures.append(error)

        threads = [threading.Thread(target=write_once) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert failures == []
        assert target.read_text(encoding="utf-8") == payload
        assert list(tmp_path.glob("*.partial")) == []
