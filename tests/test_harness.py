"""Tests for the test harness and for the repository's own invariants.

A network guard that silently stopped working would be worse than no guard,
because the suite would still claim to be offline. So the guard is tested.

The Codex mirrors are here for the same reason. `AGENT_GUIDE.md` opens by
saying the tests enforce this project's contracts, then states the mirroring
rule -- which nothing enforced. Drift there is invisible: both copies stay
valid Markdown, every other test passes, and the only symptom is Codex
following a procedure Claude sessions can no longer see.
"""

from __future__ import annotations

import re
import shlex
import socket
from itertools import pairwise
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_SKILLS = _REPO / ".claude" / "skills"
_MIRRORED_SKILLS = _REPO / ".agents" / "skills"

_FIX = (
    "{mirror} does not match {original}.\n"
    "The .claude copy is authoritative -- copy it over the .agents one:\n"
    "    cp {original} {mirror}"
)


def _skill_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }


def _fix(original: Path, mirror: Path) -> str:
    return _FIX.format(
        original=original.relative_to(_REPO).as_posix(),
        mirror=mirror.relative_to(_REPO).as_posix(),
    )


def test_the_guard_refuses_to_connect() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match="tried to open a network connection"):
        sock.connect(("example.invalid", 80))


def test_the_guard_refuses_connect_ex() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match="tried to open a network connection"):
        sock.connect_ex(("example.invalid", 80))


def test_the_guard_refuses_create_connection() -> None:
    with pytest.raises(RuntimeError, match="tried to open a network connection"):
        socket.create_connection(("example.invalid", 80))


def test_constructing_a_socket_is_still_allowed() -> None:
    # Deliberately permitted: libraries build SSL contexts and socket objects
    # without going anywhere. Blocking that caught innocent code and taught us
    # nothing about whether a request was made.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.close()


@pytest.mark.network
def test_the_marker_opts_out_of_the_guard() -> None:
    """The opt-out has to actually opt out.

    Asserting that `connect` merely exists proves nothing: the guard replaces
    it with a function, so it is not None either way and the test passed
    whether or not the marker worked. What separates a guarded socket from an
    unguarded one is the *kind* of failure. The guard raises `RuntimeError`
    without touching anything; a real socket refused by the operating system
    raises `OSError`. `RuntimeError` is not an `OSError`, so a guard still in
    place here fails this test rather than satisfying it.

    Port 0 is reserved and connectable nowhere, and this stays on the
    loopback interface, so the one test permitted to reach the network does
    not actually leave the machine.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        with pytest.raises(OSError):
            sock.connect(("127.0.0.1", 0))
    finally:
        sock.close()


def test_every_skill_has_a_codex_twin() -> None:
    originals = _skill_files(_SKILLS)
    # With both trees gone every comparison in this module would be
    # set() == set(), so the first thing to establish is that there is
    # anything left to compare.
    assert originals, f"no skill files under {_SKILLS}"
    mirrors = _skill_files(_MIRRORED_SKILLS)
    assert originals == mirrors, (
        "the Codex mirrors do not match the Claude skills.\n"
        f"only under .claude/skills: {sorted(originals - mirrors)}\n"
        f"only under .agents/skills: {sorted(mirrors - originals)}\n"
        "A mirror with no original is drift too: nothing regenerates it, so "
        "the orphan outlives the workflow it was copied from."
    )


@pytest.mark.parametrize("name", sorted(_skill_files(_SKILLS)))
def test_each_skill_matches_its_codex_twin(name: str) -> None:
    original = _SKILLS / name
    mirror = _MIRRORED_SKILLS / name
    assert mirror.exists(), _fix(original, mirror)
    # Bytes, not text: the point is that the two files are interchangeable,
    # and a comparison that normalises anything would not prove that.
    assert mirror.read_bytes() == original.read_bytes(), _fix(original, mirror)


def test_the_agent_instructions_match_their_codex_twin() -> None:
    original = _REPO / "CLAUDE.md"
    mirror = _REPO / "AGENTS.md"
    assert mirror.exists(), _fix(original, mirror)
    assert mirror.read_bytes() == original.read_bytes(), _fix(original, mirror)


#: What a skill's placeholders stand for when its commands are checked.
_PLACEHOLDERS = {
    "<source>": "HtSuA80QTyo",
    "<file>": "corrections.json",
    "<domain>": "agent-engineering",
}

_FENCE = re.compile(r"^[ \t]*```(\w+)\n(.*?)^[ \t]*```", re.MULTILINE | re.DOTALL)


def _fences(language: str) -> list[tuple[str, str]]:
    """Every ``language`` code block in every skill, with the skill it is in."""
    return [
        (path.relative_to(_REPO).as_posix(), match.group(2))
        for path in sorted(_SKILLS.rglob("SKILL.md"))
        for match in _FENCE.finditer(path.read_text(encoding="utf-8"))
        if match.group(1) == language
    ]


def _commands() -> list[tuple[str, list[str]]]:
    """This tool's command lines from the skills, as argv after the module."""
    found = []
    for skill, block in _fences("bash"):
        for line in block.replace("\\\n", " ").splitlines():
            words = shlex.split(line)
            if "youtube_transcript_notes" not in words:
                continue
            argv = words[words.index("youtube_transcript_notes") + 1 :]
            for placeholder, value in _PLACEHOLDERS.items():
                argv = [word.replace(placeholder, value) for word in argv]
            found.append((skill, argv))
    return found


def test_the_skills_name_commands() -> None:
    assert _commands(), "no youtube_transcript_notes command found in any skill"


@pytest.mark.parametrize(("skill", "argv"), _commands())
def test_every_command_in_a_skill_parses(skill: str, argv: list[str]) -> None:
    """A renamed flag leaves a skill telling agents to run a command that
    exits 2. Nothing else would notice: the skill is prose to every test."""
    from youtube_transcript_notes.cli import _parse

    try:
        _parse(argv)
    except SystemExit as exit:
        pytest.fail(f"{skill}: {' '.join(argv)} does not parse ({exit.code})")


@pytest.mark.parametrize(("skill", "argv"), _commands())
def test_every_glossary_a_skill_names_exists(skill: str, argv: list[str]) -> None:
    for flag, value in pairwise(argv):
        if flag == "--glossary":
            assert (_REPO / value).is_file(), f"{skill} names missing {value}"


@pytest.mark.parametrize(("skill", "code"), _fences("python"))
def test_every_python_example_in_a_skill_compiles(skill: str, code: str) -> None:
    compile(code, skill, "exec")
