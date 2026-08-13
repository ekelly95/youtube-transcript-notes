"""Turning a lecture title into a filename.

Pure functions, deliberately. Naming a file is a decision and writing one is an
effect; keeping them apart is what lets `cli.run` work out every path it
intends to write without touching a disk, which is the same trick that keeps
renderers testable.

The rules are mostly Windows's, because Windows has the strictest ones and a
name that is legal there is legal everywhere. Non-ASCII survives: lecture
titles are often not in English, and transliterating them would make the files
harder to find rather than safer to write.
"""

from __future__ import annotations

import re
from collections.abc import Container, Iterator

__all__ = ["filename_for", "sanitise"]

#: Characters no Windows filename may contain, plus the control range. The
#: forward slash matters on POSIX too; the rest are stripped everywhere anyway
#: so that a vault synced between machines carries the same names.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_WHITESPACE = re.compile(r"\s+")

#: Reserved device names. Windows refuses these whatever extension follows, so
#: ``CON.md`` fails exactly as ``CON`` does.
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in range(1, 10)}
    | {f"LPT{digit}" for digit in range(1, 10)}
)

#: Longest stem kept. Comfortably inside every filesystem's per-component
#: limit, and short enough that a full path still fits Windows's default
#: maximum with a directory in front of it.
MAX_STEM = 120

#: A lecture with no usable title and no usable id still has to be called
#: something. Reaching this means the source named itself nothing at all.
FALLBACK_STEM = "lecture"


def sanitise(title: str) -> str:
    """A title reduced to something every filesystem will accept.

    Returns the empty string when nothing usable survives. That case is the
    caller's to handle — inventing a name here would hide a lecture whose
    title is missing entirely behind one that looks deliberate.
    """
    cleaned = _WHITESPACE.sub(" ", _FORBIDDEN.sub(" ", title)).strip()
    # Windows silently drops trailing dots and spaces, so a name ending in one
    # is not the name that was asked for.
    cleaned = cleaned.rstrip(". ")

    if len(cleaned) > MAX_STEM:
        cleaned = _truncate(cleaned)

    if cleaned.upper() in _DEVICE_NAMES:
        # An escape rather than a rename: `CON` is unusable, `CON_` is not,
        # and the reader can still see what it was.
        cleaned = f"{cleaned}_"

    return cleaned


def filename_for(
    title: str, source_id: str, extension: str, taken: Container[str] = ()
) -> str:
    """A filename for this lecture that nothing else in the run has claimed.

    The name carries the source id, because **a title is not an identity**.
    Whoever uploaded a lecture chose its title, so a filename derived from the
    title alone hands a stranger the power to pick which file in the output
    directory gets replaced: publish a video called "Exam Notes", wait for it
    to be rendered into a vault that already contains ``Exam Notes.md``, and
    the writer replaces the note. The same collision happens by accident every
    time two lectures in a course share a title. The id is the part of a
    lecture's identity nobody upstream can aim at an existing file.

    Re-running the same lecture still yields the same name on purpose.
    Re-rendering is the normal case, and a directory that grew
    ``notes (1).md``, ``notes (2).md`` on every run would be worse. What
    happens when that name is occupied is not decided here: `cli._write`
    refuses a file it did not just render, and leaves an identical one alone.

    `taken` holds case-folded stems, because two names differing only in case
    are the same file on Windows.
    """
    stem = sanitise(title) or sanitise(source_id) or FALLBACK_STEM

    # `_candidates` never runs out, so this loop always returns.
    return next(
        f"{candidate}.{extension}"
        for candidate in _candidates(stem, sanitise(source_id))
        if candidate.casefold() not in taken
    )


def _candidates(stem: str, identifier: str) -> Iterator[str]:
    """Names to try, in descending order of preference.

    The source id is in the first candidate, not held back for a collision.
    Holding it back is what let a title alone decide which existing file to
    replace; see `filename_for`.

    It is dropped only when it would say nothing. An id equal to the stem is
    every local file — `sources.local` names a lecture after its own stem — and
    ``mit6006-lec1 (mit6006-lec1).md`` is noise, not identity. Compared
    case-folded for the same reason `taken` is: on Windows the two spellings
    are one file.

    The counter after that is a backstop for the same source passed twice in
    one command; it does not appear in ordinary use.
    """
    # Empty stays empty: an id the caller never supplied cannot distinguish
    # anything, and `"" != stem` would otherwise make it look as though it did.
    distinguishing = (
        identifier if identifier and identifier.casefold() != stem.casefold() else ""
    )

    yield f"{stem} ({distinguishing})" if distinguishing else stem

    # `while True` rather than `itertools.count`, so there is no loop-exit
    # branch that can never be taken — the coverage gate would flag it, and it
    # would be right to.
    attempt = 2
    while True:
        yield (
            f"{stem} ({distinguishing} {attempt})"
            if distinguishing
            else f"{stem} ({attempt})"
        )
        attempt += 1


def _truncate(text: str) -> str:
    """Cut to `MAX_STEM`, at a word boundary when there is a sensible one."""
    cut = text[:MAX_STEM]
    at_space = cut.rsplit(" ", 1)[0]
    # A title that is one enormous word has no boundary worth respecting;
    # chopping it back to almost nothing would lose more than it saves.
    kept = at_space if len(at_space) > MAX_STEM // 2 else cut
    return kept.rstrip(". ")
