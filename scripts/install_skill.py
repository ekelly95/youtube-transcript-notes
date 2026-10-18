"""Install the video-notes skill for Claude Code everywhere, not just in this clone.

Claude Code reads personal skills from ``~/.claude/skills``. The repository's
copy is written to run from the repository root, with relative paths; this
writes a copy that names this clone's interpreter, glossaries and notes folder
by absolute path, so the skill works from wherever a session starts.

The installed copy is generated, never edited: change the repository's
``SKILL.md`` and run this again. ``--check`` reports whether the installed
copy is current without writing anything.

    .venv/bin/python scripts/install_skill.py            # install or update
    .venv/bin/python scripts/install_skill.py --check    # 1 when stale
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "video-notes" / "SKILL.md"
DESTINATION = Path.home() / ".claude" / "skills" / "video-notes" / "SKILL.md"

#: The repository copy's opening instruction, replaced whole. Matched exactly,
#: so a reworded skill stops this script rather than installing a copy that
#: still says "from the repository root".
INTRO = (
    "Run commands from the repository root. They are written with "
    "`.venv/bin/python`;\n"
    "on Windows use `.venv/Scripts/python`. If `LOCAL.md` exists beside this file,\n"
    "apply its machine-specific output folder and post-processing defaults."
)

#: How the repository copy spells the interpreter in its commands.
RELATIVE_PYTHON = ".venv/bin/python"

#: Folders the skill names relative to the repository root. The lookbehind
#: leaves a longer path alone, such as one already made absolute.
_RELATIVE_FOLDER = re.compile(r"(?<![\w./-])(glossaries|notes)/")


#: Stands in for the intro while paths are rewritten; no skill contains it.
_PLACEHOLDER = "\x00intro\x00"


class SkillChanged(Exception):
    """The repository's skill no longer has the shape this script rewrites."""


def interpreter(root: Path) -> Path:
    """This clone's virtual-environment Python."""
    if sys.platform == "win32":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def globalise(skill: str, root: Path, python: Path) -> str:
    """The repository's skill, rewritten to work from any directory."""
    if skill.count(INTRO) != 1:
        raise SkillChanged(
            "the opening paragraph of SKILL.md has changed; update INTRO in "
            "scripts/install_skill.py to match it"
        )

    source = (root / ".claude" / "skills" / "video-notes" / "SKILL.md").as_posix()
    local = (root / ".claude" / "skills" / "video-notes" / "LOCAL.md").as_posix()
    intro = (
        f"This copy is generated from `{source}` by `scripts/install_skill.py`.\n"
        "Edit that file and rerun the script; changes made here are lost.\n"
        "\n"
        f"Commands work from any directory. Run them with `{python.as_posix()}`.\n"
        f"If `{local}` exists, apply its machine-specific output folder\n"
        "and post-processing defaults."
    )

    # The intro is set aside while paths are rewritten: its own mention of the
    # relative interpreter goes with it, and the new one is absolute already.
    body = skill.replace(INTRO, _PLACEHOLDER)
    body = body.replace(RELATIVE_PYTHON, python.as_posix())
    body = _RELATIVE_FOLDER.sub(lambda match: f"{root.as_posix()}/{match[0]}", body)
    return body.replace(_PLACEHOLDER, intro)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the installed copy is missing or stale; write nothing.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DESTINATION,
        help=f"Where to install the skill. Defaults to {DESTINATION}.",
    )
    args = parser.parse_args(argv)

    try:
        wanted = globalise(SKILL.read_text(encoding="utf-8"), ROOT, interpreter(ROOT))
    except SkillChanged as error:
        print(f"Not installed: {error}.", file=sys.stderr)
        return 2

    try:
        current = args.dest.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None

    if current == wanted:
        print(f"{args.dest} is up to date.")
        return 0
    if args.check:
        state = "missing" if current is None else "stale"
        print(f"{args.dest} is {state}; run scripts/install_skill.py.", file=sys.stderr)
        return 1

    args.dest.parent.mkdir(parents=True, exist_ok=True)
    args.dest.write_text(wanted, encoding="utf-8")
    print(f"{'Installed' if current is None else 'Updated'} {args.dest}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
