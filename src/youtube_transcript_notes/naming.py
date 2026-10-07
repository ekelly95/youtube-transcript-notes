"""Turning a title into a filename.

Pure functions: `cli` works out every path before anything is written. The
rules are Windows's, the strictest, so a name legal there is legal everywhere.
Non-ASCII titles are kept as they are.
"""

from __future__ import annotations

import re
from collections.abc import Container, Iterator

__all__ = ["filename_for", "sanitise"]

#: Characters no Windows filename may contain, plus the control range. Stripped
#: on every platform so a synced vault carries the same names everywhere.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_WHITESPACE = re.compile(r"\s+")

#: Reserved device names; Windows refuses ``CON.md`` just as it refuses ``CON``.
_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in range(1, 10)}
    | {f"LPT{digit}" for digit in range(1, 10)}
)

#: Longest stem kept, so a full path still fits Windows's default maximum.
MAX_STEM = 120

#: The name used when a source has neither a usable title nor a usable id.
FALLBACK_STEM = "lecture"


def sanitise(title: str) -> str:
    """A title reduced to something every filesystem will accept.

    Returns the empty string when nothing usable survives; the caller decides
    what to call it instead.
    """
    cleaned = _WHITESPACE.sub(" ", _FORBIDDEN.sub(" ", title)).strip()
    # Windows silently drops trailing dots and spaces.
    cleaned = cleaned.rstrip(". ")

    if len(cleaned) > MAX_STEM:
        cleaned = _truncate(cleaned)

    if cleaned.upper() in _DEVICE_NAMES:
        cleaned = f"{cleaned}_"

    return cleaned


def filename_for(
    title: str, source_id: str, extension: str, taken: Container[str] = ()
) -> str:
    """A filename for this source that nothing else in the run has claimed.

    The name carries the source id because a title is not an identity: an
    uploader chooses it, so a title-only name would let a stranger pick which
    existing note gets replaced, and two videos sharing a title would collide.
    Re-rendering the same source yields the same name; what happens when it is
    occupied is `cli._write`'s decision.

    `taken` holds case-folded stems, because names differing only in case are
    one file on Windows.
    """
    stem = sanitise(title) or sanitise(source_id) or FALLBACK_STEM

    # `_candidates` never runs out, so this always returns.
    return next(
        f"{candidate}.{extension}"
        for candidate in _candidates(stem, sanitise(source_id))
        if candidate.casefold() not in taken
    )


def _candidates(stem: str, identifier: str) -> Iterator[str]:
    """Names to try, best first.

    The id is dropped only when it says nothing — a local file, whose id is its
    own stem. The counter is a backstop for one source passed twice.
    """
    distinguishing = (
        identifier if identifier and identifier.casefold() != stem.casefold() else ""
    )

    yield f"{stem} ({distinguishing})" if distinguishing else stem

    # `while True` rather than `itertools.count`, so there is no unreachable
    # loop exit for the coverage gate to flag.
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
    # One enormous word has no boundary worth respecting.
    kept = at_space if len(at_space) > MAX_STEM // 2 else cut
    return kept.rstrip(". ")
