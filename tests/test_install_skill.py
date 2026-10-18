"""The script that installs the video-notes skill outside the repository.

The installed copy is what a Claude session started anywhere else follows, so
a copy that still says "from the repository root", or names a relative
interpreter, fails quietly: every command runs against the wrong directory.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "install_skill", _REPO / "scripts" / "install_skill.py"
)
assert _spec is not None and _spec.loader is not None
install_skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_skill)

ROOT = Path("/opt/clone")
PYTHON = ROOT / ".venv" / "bin" / "python"


@pytest.fixture
def installed() -> str:
    return install_skill.globalise(
        install_skill.SKILL.read_text(encoding="utf-8"), ROOT, PYTHON
    )


class TestGlobalise:
    def test_the_repository_skill_still_has_the_shape_it_rewrites(self) -> None:
        """Fails when SKILL.md's opening changes, before anyone installs it."""
        assert (
            install_skill.SKILL.read_text(encoding="utf-8").count(install_skill.INTRO)
            == 1
        )

    def test_no_relative_path_survives(self, installed: str) -> None:
        assert ".venv/Scripts" not in installed
        # Absolute interpreters contain `.venv/bin/python`; a relative one
        # starts a word.
        assert not re.search(r"(?<![\w/])\.venv/", installed)
        assert "repository root" not in installed
        assert " glossaries/" not in installed
        assert "`glossaries/" not in installed
        assert " notes/" not in installed
        assert "`notes/" not in installed

    def test_every_command_names_the_clone_interpreter(self, installed: str) -> None:
        commands = [
            line.strip()
            for line in installed.splitlines()
            if "-m youtube_transcript_notes" in line
        ]

        assert commands
        assert all(line.startswith(f"{PYTHON.as_posix()} -m") for line in commands)

    def test_folders_are_absolute(self, installed: str) -> None:
        assert "--glossary /opt/clone/glossaries/<domain>.txt" in installed
        assert "--out /opt/clone/notes/" in installed

    def test_it_says_where_it_came_from(self, installed: str) -> None:
        assert "/opt/clone/.claude/skills/video-notes/SKILL.md" in installed
        assert "/opt/clone/.claude/skills/video-notes/LOCAL.md" in installed

    def test_the_frontmatter_is_untouched(self, installed: str) -> None:
        original = install_skill.SKILL.read_text(encoding="utf-8")
        head = original.split("\n---\n", 1)[0]

        assert installed.startswith(head + "\n---\n")

    def test_a_reworded_skill_is_refused(self) -> None:
        with pytest.raises(install_skill.SkillChanged):
            install_skill.globalise("# Video notes\n\nSomething else.\n", ROOT, PYTHON)


class TestMain:
    def test_install_then_check(self, tmp_path: Path, capsys) -> None:
        dest = tmp_path / "skills" / "video-notes" / "SKILL.md"

        assert install_skill.main(["--check", "--dest", str(dest)]) == 1
        assert not dest.exists()
        assert install_skill.main(["--dest", str(dest)]) == 0
        assert "Installed" in capsys.readouterr().out
        assert install_skill.main(["--check", "--dest", str(dest)]) == 0

    def test_a_stale_copy_is_reported_then_replaced(
        self, tmp_path: Path, capsys
    ) -> None:
        dest = tmp_path / "SKILL.md"
        dest.write_text("an older skill\n", encoding="utf-8")

        assert install_skill.main(["--check", "--dest", str(dest)]) == 1
        assert "stale" in capsys.readouterr().err
        assert dest.read_text(encoding="utf-8") == "an older skill\n"

        assert install_skill.main(["--dest", str(dest)]) == 0
        assert "Updated" in capsys.readouterr().out
        assert "scripts/install_skill.py" in dest.read_text(encoding="utf-8")

    def test_a_reworded_skill_installs_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        skill = tmp_path / "SKILL.md"
        skill.write_text("# reworded\n", encoding="utf-8")
        monkeypatch.setattr(install_skill, "SKILL", skill)
        dest = tmp_path / "installed.md"

        assert install_skill.main(["--dest", str(dest)]) == 2
        assert not dest.exists()
