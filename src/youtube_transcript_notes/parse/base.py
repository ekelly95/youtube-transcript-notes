"""Shared parsing machinery.

A parser takes a caption payload and returns `Cue` objects that faithfully
represent what the source published. It does not clean up, deduplicate or
reflow — that is the refine stage's job, and keeping the split sharp means a
parser bug and a reflow bug never look like each other.

The one liberty parsers do take is whitespace. Caption formats embed newlines
to control where text wraps on screen; that is presentation, not content, and
carrying it downstream would make every consumer strip it anyway.
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

    The size check is repeated here rather than trusted from the providers.
    Both of them cap what they read, but this is also the library's front door
    — `parse_captions` is exported, and a caller who built a payload some other
    way should meet the same ceiling as one who let the tool fetch it.
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

    A string passes `len` and iterates, so "is it sized" and "is it iterable"
    both wave it through and the parser then treats each character as an entry.
    The check is `isinstance(list)` for that reason.
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

    `NaN` and `Infinity` are refused, and refusing them here is the whole
    point of this function existing. Python's `json` accepts both as literals,
    `float()` converts them happily, and a `NaN` start survives parsing,
    reflow, and section building without complaint — then raises a bare
    ``ValueError: cannot convert float NaN to integer`` out of
    `format_timestamp`, several stages away from the caption that caused it.
    A malformed source should be named where it is read.
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
    """Refuse a container with more entries than `limit`, before it is walked.

    Takes the container rather than a number so the refusal happens at the one
    place that can still be cheap about it — counting is free, and everything
    after this point allocates per entry.
    """
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

    Cues are found by a line that *parses* as a timing rather than by splitting
    on blank lines, which looks equivalent and is not. YouTube's automatic
    WebVTT uses a **whitespace-only line as content** — it is the empty upper
    row of the two-line scrolling window — so a splitter that treats any
    blank-looking line as a delimiter silently loses the cue that follows it.

    Parsing, and not merely looking for ``-->``: an arrow is ordinary text in a
    transcript. A lecturer saying "A --> B is an implication" wrote a caption
    that a substring test reads as a timing line, which ended the real cue and
    began a malformed one — dropping the cue outright when the arrow opened it,
    and truncating it at the arrow otherwise. Both silently, which contract 5
    forbids. WebVTT conformant enough to escape its arrows was never affected,
    since `unescape` runs after this; SubRip has no such rule and was.

    The trade this makes, deliberately: a line that looks like a timing but does
    not parse — ``00:00:0x.000 --> 00:00:03.000`` — is now cue text rather than
    the end of the cue. There is no third answer available, and losing a cue
    costs more than gaining a line of nonsense in one.

    Within a cue, text runs until the first genuinely empty line. Everything
    after that is ignored until the next timing line, which is what discards
    SubRip's sequence numbers without needing to recognise them.

    The cue ceiling is counted here because this is where the line formats can
    still stop early. They have no container to measure up front the way json3
    has ``events``, and a `Cue` costs far more than the thirty-odd bytes of
    text that asked for it — so counting on the way past is the only place the
    refusal is cheaper than the work it prevents.
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
