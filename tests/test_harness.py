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

import socket
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
