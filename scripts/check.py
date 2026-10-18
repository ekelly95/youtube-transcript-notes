"""Run every check a change must pass before it is handed off.

One command on every platform: each gate runs as a module of the interpreter
running this script, so nothing here names `.venv/bin` or `.venv/Scripts`.

    .venv/bin/python scripts/check.py              # macOS and Linux
    .venv/Scripts/python scripts/check.py          # Windows
    .venv/bin/python scripts/check.py --canary     # also the live YouTube check

Every gate runs even after one fails, so a single run shows everything that
needs fixing. The exit code is 1 if any gate failed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The gates, in order, as module arguments. CI runs the same five.
GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tests", ("coverage", "run", "-m", "pytest")),
    ("coverage", ("coverage", "report")),
    ("formatting", ("ruff", "format", "--check", ".")),
    ("lint", ("ruff", "check", ".")),
    ("types", ("mypy",)),
)

#: Opt-in, because it needs the network and an address YouTube does not block.
CANARY = ("canary", ("pytest", "-m", "canary"))

#: Runs one command in the repository root, returning its exit code. Replaced
#: in tests, which must not start a second test suite from inside the first.
Runner = Callable[[Sequence[str]], int]


def _run(command: Sequence[str]) -> int:
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main(argv: Sequence[str] | None = None, runner: Runner = _run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--canary",
        action="store_true",
        help="Also run the live YouTube check (needs the network).",
    )
    args = parser.parse_args(argv)

    gates = (*GATES, CANARY) if args.canary else GATES
    failed = []
    for name, module in gates:
        print(f"==> {name}: python -m {' '.join(module)}", flush=True)
        if runner([sys.executable, "-m", *module]) != 0:
            failed.append(name)

    if failed:
        print(f"\nFailed: {', '.join(failed)}.", file=sys.stderr)
        return 1
    print(f"\nAll {len(gates)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
