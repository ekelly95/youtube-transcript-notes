"""Shared parsing machinery.

A parser turns a caption payload into `Cue` objects that faithfully represent
what the source published. Cleanup, deduplication and reflow belong to
`refine`, so a parser bug and a reflow bug never look alike. The one liberty
parsers take is collapsing on-screen line wrapping.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterator, Sized
from typing import Any

from ..errors import MalformedCaptions, PayloadTooLarge, UnknownCaptionFormat
from ..limits import MAX_CUES, MAX_PAYLOAD_BYTES, describe_size
from ..models import Cue
from ..registry import Registry

__all__ = [
    "CaptionParser",
    "check_count",
    "iter_cue_blocks",
    "normalise",
    "parse_captions",
    "parse_clock",
    "parse_timing",
    "parsers",
    "require_finite",
    "require_list",
    "require_object",
]

#: ``(payload, source) -> cues``. ``source`` is only used for error messages.
CaptionParser = Callable[[str, str], list[Cue]]

parsers: Registry[CaptionParser] = Registry("caption format", UnknownCaptionFormat)

_WHITESPACE = re.compile(r"\s+")

#: ``HH:MM:SS.mmm`` or ``MM:SS.mmm``, with either a dot (WebVTT) or a comma
#: (SubRip) before the milliseconds.
_CLOCK = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def parse_captions(payload: str, fmt: str, source: str = "<unknown>") -> list[Cue]:
    """Parse ``payload`` using the parser registered for ``fmt``.

    The size ceiling is checked here too, because this is an exported entry
    point and a caller may have built the payload themselves.
    """
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise PayloadTooLarge(
            source=source,
            measured=describe_size(len(payload)),
            limit=describe_size(MAX_PAYLOAD_BYTES),
        )
    return parsers.get(fmt)(payload, source)


def require_list(value: Any, what: str, source: str, fmt: str) -> list[Any]:
    """``value`` as a list, or a `MalformedCaptions` naming what it was instead.

    `isinstance(list)`, because a string is sized and iterable too.
    """
    if not isinstance(value, list):
        raise MalformedCaptions(
            source=source,
            fmt=fmt,
            detail=f"{what} is {type(value).__name__}, not a list",
        )
    return value


def require_object(value: Any, what: str, source: str, fmt: str) -> dict[str, Any]:
    """``value`` as an object, or a `MalformedCaptions` naming what it was."""
    if not isinstance(value, dict):
        raise MalformedCaptions(
            source=source,
            fmt=fmt,
            detail=f"{what} is {type(value).__name__}, not an object",
        )
    return value


def require_finite(value: Any, what: str, source: str, fmt: str) -> float:
    """``value`` as a real number of seconds, or `MalformedCaptions`.

    `json` accepts ``NaN`` and ``Infinity``, which would otherwise survive
    every stage and crash `format_timestamp` far from the caption that caused it.
    """
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise MalformedCaptions(
            source=source,
            fmt=fmt,
            detail=f"{what} is not a number: {value!r:.60}",
        ) from error

    if not math.isfinite(number):
        raise MalformedCaptions(
            source=source,
            fmt=fmt,
            detail=f"{what} is {number}, which is not a time",
        )
    return number


def check_count(items: Sized, limit: int, source: str, fmt: str, what: str) -> None:
    """Refuse a container with more entries than `limit`, before it is walked."""
    count = len(items)
    if count > limit:
        raise PayloadTooLarge(
            source=source,
            measured=f"{count:,} {what} ({fmt})",
            limit=f"{limit:,} {what}",
        )


def normalise(text: str) -> str:
    """Collapse caption line-wrapping into ordinary single-spaced text."""
    return _WHITESPACE.sub(" ", text).strip()


def parse_clock(value: str) -> float | None:
    """Parse a caption timestamp to seconds, or None if it is not one."""
    match = _CLOCK.fullmatch(value.strip())
    if match is None:
        return None
    hours, minutes, seconds, fraction = match.groups()
    return (
        int(hours or 0) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction.ljust(3, "0")) / 1000
    )


def parse_timing(line: str) -> tuple[float, float] | None:
    """Parse a ``START --> END`` line to seconds, ignoring any cue settings."""
    left, _, right = line.partition("-->")
    start = parse_clock(left)
    tail = right.split()
    end = parse_clock(tail[0]) if tail else None
    if start is None or end is None:
        return None
    return start, end


def iter_cue_blocks(
    payload: str, source: str = "<unknown>", fmt: str = "captions"
) -> Iterator[tuple[tuple[float, float], list[str]]]:
    """Yield ``((start, end), text_lines)`` for each cue in a line-based format.

    A cue starts at a line that *parses* as a timing — not at a blank line,
    because YouTube's automatic WebVTT uses a whitespace-only line as content,
    and not at any ``-->``, because an arrow is ordinary transcript text. A
    malformed timing line therefore becomes cue text rather than ending a cue.

    Text runs until the first truly empty line; the rest up to the next timing
    is ignored, which drops SubRip sequence numbers. The cue ceiling is counted
    here because line formats have no container to measure up front.
    """
    span: tuple[float, float] | None = None
    text: list[str] = []
    collecting = False
    yielded = 0

    for line in payload.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        found = parse_timing(line)
        if found is not None:
            if span is not None:
                yielded += 1
                _check_cue_count(yielded, source, fmt)
                yield span, text
            span, text, collecting = found, [], True
        elif collecting:
            if line == "":
                collecting = False
            else:
                text.append(line)

    if span is not None:
        _check_cue_count(yielded + 1, source, fmt)
        yield span, text


def _check_cue_count(count: int, source: str, fmt: str) -> None:
    if count > MAX_CUES:
        raise PayloadTooLarge(
            source=source,
            measured=f"more than {MAX_CUES:,} cues ({fmt})",
            limit=f"{MAX_CUES:,} cues",
        )
