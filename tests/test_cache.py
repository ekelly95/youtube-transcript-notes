"""Where the cache lives, and who gets to decide.

The payload behaviour — keys, atomic writes, misses — is exercised through the
YouTube provider in `test_youtube.py`, which is the only thing that consults a
cache. What is tested here is location resolution, because that is the part
with a branch per platform and no way to notice a wrong answer except by
finding a stray directory later.

Every test here deletes `YOUTUBE_TRANSCRIPT_NOTES_CACHE` first:
`conftest.isolate_cache` sets it
for the whole suite precisely so that nothing else writes to a real user
directory, which would otherwise mask the very defaults under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from youtube_transcript_notes.cache import (
    CACHE_ENV_VAR,
    Cache,
    NullCache,
    default_cache_root,
)


@pytest.fixture
def unset_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CACHE_ENV_VAR, raising=False)


class TestTheDefaultIsNotTheWorkingDirectory:
    def test_the_default_is_absolute(
        self, unset_override: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The whole point. A relative default would follow the caller around."""
        monkeypatch.chdir(tmp_path)
        assert default_cache_root().is_absolute()

    def test_the_default_does_not_move_with_the_working_directory(
        self, unset_override: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        here = default_cache_root()

        nested = tmp_path / "somewhere" / "else"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        assert default_cache_root() == here

    def test_no_cache_directory_is_created_just_by_resolving_one(
        self, unset_override: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Resolution is pure. Only `write` is allowed to make a directory."""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "nothing-here"))

        root = default_cache_root()

        assert root == tmp_path / "nothing-here" / "youtube-transcript-notes"
        assert not root.exists()
        assert list(tmp_path.iterdir()) == []


class TestPlatformDefaults:
    def test_windows_uses_local_appdata(
        self, unset_override: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The expectation is built the way the code builds it, rather than
        spelled out as a literal Windows path.

        A literal is not portable, and not in a way that fails loudly: on
        POSIX, `Path(r"C:\\Users\\someone\\AppData\\Local")` is a single path
        component with backslashes *inside* it, so appending the cache
        directory name gives `C:\\Users\\...\\Local/youtube-transcript-notes`
        — which never equals the all-backslash
        literal, and this test failed everywhere except Windows.
        """
        local_appdata = Path(r"C:\Users\someone\AppData\Local")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

        assert (
            default_cache_root() == local_appdata / "youtube-transcript-notes" / "cache"
        )

    def test_windows_without_local_appdata_falls_back_under_home(
        self, unset_override: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)

        expected = (
            Path.home() / "AppData" / "Local" / "youtube-transcript-notes" / "cache"
        )
        assert default_cache_root() == expected

    def test_macos_uses_library_caches(
        self, unset_override: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")

        assert (
            default_cache_root()
            == Path.home() / "Library" / "Caches" / "youtube-transcript-notes"
        )

    def test_linux_honours_xdg_cache_home(
        self, unset_override: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CACHE_HOME", "/var/tmp/somecache")

        assert default_cache_root() == Path(
            "/var/tmp/somecache/youtube-transcript-notes"
        )

    def test_linux_without_xdg_falls_back_to_dot_cache(
        self, unset_override: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

        assert (
            default_cache_root() == Path.home() / ".cache" / "youtube-transcript-notes"
        )


class TestPrecedence:
    def test_the_environment_variable_wins_over_the_platform_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv(CACHE_ENV_VAR, str(tmp_path / "elsewhere"))

        assert default_cache_root() == tmp_path / "elsewhere"

    def test_an_empty_environment_variable_is_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise `YOUTUBE_TRANSCRIPT_NOTES_CACHE=` would resolve to the CWD."""
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv(CACHE_ENV_VAR, "")

        assert (
            default_cache_root()
            == Path.home() / "Library" / "Caches" / "youtube-transcript-notes"
        )

    def test_an_explicit_root_wins_over_the_environment_variable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(CACHE_ENV_VAR, str(tmp_path / "from-env"))

        assert Cache(tmp_path / "explicit").root == tmp_path / "explicit"

    def test_no_root_falls_back_to_the_resolved_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(CACHE_ENV_VAR, str(tmp_path / "from-env"))

        assert Cache().root == tmp_path / "from-env"

    def test_the_null_cache_still_writes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """It subclasses `Cache`, so it inherits a root it must never use.

        That inherited root is `Path(".")` — the working directory — so the
        test has to *stand* in the directory it then proves stayed empty.
        Asserting on `tmp_path` while the null cache writes relative to
        wherever pytest happened to be launched would pass no matter how badly
        `write` regressed.
        """
        monkeypatch.chdir(tmp_path)
        cache = NullCache()
        cache.write("k" * 32, "payload")

        assert cache.root == Path(".")
        assert cache.read("k" * 32) is None
        assert list(tmp_path.iterdir()) == []


class TestACorruptEntryIsAMiss:
    def test_a_corrupt_cache_entry_reads_as_a_miss(self, tmp_path: Path) -> None:
        """Escaping as `UnicodeDecodeError` reached the CLI's last resort as
        "retry — transient network failures are common", a diagnosis no retry
        could repair because every retry rereads the same bytes. A miss is
        strictly better: the refetch overwrites the bad entry."""
        cache = Cache(tmp_path)
        path = cache.path_for("deadbeef")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe not utf-8")

        assert cache.read("deadbeef") is None

    def test_an_entry_that_was_never_written_reads_as_a_miss(
        self, tmp_path: Path
    ) -> None:
        assert Cache(tmp_path).read("deadbeef") is None

    def test_an_entry_that_cannot_be_opened_reads_as_a_miss(
        self, tmp_path: Path
    ) -> None:
        """A directory standing where an entry should be: `stat` answers, the
        read does not.

        Windows and POSIX refuse it as two different `OSError` subclasses, which
        is why the catch names the base class rather than either of them.
        """
        cache = Cache(tmp_path)
        cache.path_for("deadbeef").mkdir(parents=True)

        assert cache.read("deadbeef") is None


class TestAnUnwritableCacheCostsNothingButTheCache:
    def test_a_write_that_cannot_land_is_not_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cache is an optimisation over work that already succeeded.

        While this raised, a full disk turned a lecture that had been fetched,
        parsed and rendered into a failed run — and reported it as an
        *acquisition* failure, blaming the one part that had worked.
        """

        def no_room(*args: object, **kwargs: object) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("youtube_transcript_notes.cache.atomic_write", no_room)
        cache = Cache(tmp_path)

        cache.write("deadbeef", "payload")

        assert cache.read("deadbeef") is None
