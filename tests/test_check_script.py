"""The one command the agent instructions tell every change to pass.

If it quietly dropped a gate, or stopped at the first failure, a change could
be handed off with a check nobody ran.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("check", _REPO / "scripts" / "check.py")
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


class _Recorder:
    """Stands in for the subprocess, failing the gates it is told to."""

    def __init__(self, failing: Sequence[str] = ()) -> None:
        self.failing = set(failing)
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> int:
        self.commands.append(list(command))
        return 1 if command[2] in self.failing else 0


def test_every_gate_runs_through_this_interpreter(capsys) -> None:
    recorder = _Recorder()

    assert check.main([], runner=recorder) == 0
    assert [command[:2] for command in recorder.commands] == [
        [sys.executable, "-m"]
    ] * len(check.GATES)
    assert [command[2] for command in recorder.commands] == [
        "coverage",
        "coverage",
        "ruff",
        "ruff",
        "mypy",
    ]
    assert "All 5 checks passed." in capsys.readouterr().out


def test_a_failure_does_not_stop_the_rest(capsys) -> None:
    recorder = _Recorder(failing=["ruff"])

    assert check.main([], runner=recorder) == 1
    assert len(recorder.commands) == len(check.GATES)
    assert "Failed: formatting, lint." in capsys.readouterr().err


def test_the_canary_is_opt_in() -> None:
    plain, live = _Recorder(), _Recorder()

    check.main([], runner=plain)
    check.main(["--canary"], runner=live)

    assert ["pytest", "-m", "canary"] not in [c[2:] for c in plain.commands]
    assert live.commands[-1][2:] == ["pytest", "-m", "canary"]


@pytest.mark.parametrize("instructions", ["CLAUDE.md", "AGENT_GUIDE.md"])
def test_the_instructions_name_it(instructions: str) -> None:
    text = (_REPO / instructions).read_text(encoding="utf-8")

    assert ".venv/bin/python scripts/check.py" in text
    assert ".venv/Scripts/python scripts/check.py" in text
