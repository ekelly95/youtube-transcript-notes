"""YouTube's ``json3`` caption format — the preferred source.

Two properties earn it that status. It carries per-word offsets on
auto-generated tracks, so a timestamp can be accurate to the word rather than
to a three-second cue. And its rolling-window scrolling is expressed
structurally, as separate ``aAppend`` events, rather than by repeating the
previous text the way WebVTT does — so filtering those events yields clean
cues with no text deduplication needed at all.

Observed shapes, from real MIT OpenCourseWare captions:

* manual — every event is ``{tStartMs, dDurationMs, segs}`` with a single
  ``segs`` entry whose ``utf8`` holds the whole cue, newlines and all.
* automatic — one leading window-definition event with no ``segs``, then
  alternating content events and ``aAppend`` events whose only content is a
  newline. Content segs carry ``tOffsetMs`` and ``acAsrConf``.
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import MalformedCaptions
from ..limits import MAX_EVENTS
from ..models import Cue, Word
from .base import (
    check_count,
    normalise,
    parsers,
    require_finite,
    require_list,
    require_object,
)

__all__ = ["parse_json3"]


@parsers.register("json3")
def parse_json3(payload: str, source: str = "<unknown>") -> list[Cue]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise MalformedCaptions(
            source=source, fmt="json3", detail=str(error)
        ) from error

    if not isinstance(data, dict) or "events" not in data:
        raise MalformedCaptions(
            source=source, fmt="json3", detail="no 'events' key at the top level"
        )

    # Typed before it is counted, because `check_count` asks for a length and
    # `None` and `5` have none — the count check would raise a bare TypeError
    # for a payload whose real problem is its shape.
    events = require_list(data["events"], "'events'", source, "json3")

    # Counted before anything is built from it. Bytes alone do not bound this:
    # about forty bytes of JSON buys one event, and one event becomes a dict
    # costing several times that, so a payload well inside the byte ceiling
    # can still expand past what the process can hold. See `limits`.
    check_count(events, MAX_EVENTS, source, "json3", "events")

    cues = []
    for event in events:
        cue = _cue_from_event(event, source)
        if cue is not None:
            cues.append(cue)
    return cues


def _cue_from_event(event: Any, source: str) -> Cue | None:
    require_object(event, "event", source, "json3")

    # The window-definition event that opens an automatic track has no segs.
    raw_segs = event.get("segs")
    if not raw_segs:
        return None

    # Typed before it is walked. `segs` reached `seg.get` unchecked, so
    # `{"segs": [null]}` raised `AttributeError` and `{"segs": 5}` raised
    # `TypeError` — both true, neither in the taxonomy, and the library caller
    # got the raw exception while the CLI filed it as an acquisition failure.
    segs = [
        require_object(seg, "seg", source, "json3")
        for seg in require_list(raw_segs, "'segs'", source, "json3")
    ]

    # Scroll padding: the renderer's way of moving the window up a line. Its
    # only content is a newline, and it duplicates nothing.
    if event.get("aAppend"):
        return None

    text = normalise("".join(str(seg.get("utf8", "")) for seg in segs))
    if not text:
        return None

    if "tStartMs" not in event:
        raise MalformedCaptions(
            source=source,
            fmt="json3",
            detail=f"event has no tStartMs: {event!r:.120}",
        )
    start = require_finite(event["tStartMs"], "tStartMs", source, "json3") / 1000

    return Cue(
        text=text,
        # Clamped, like the negative durations below and for the same reason:
        # `format_timestamp` renders a negative start as "-1:59:55", and every
        # consumer downstream would otherwise have to defend against a lecture
        # that begins before it begins.
        start=max(start, 0.0),
        duration=_duration(event, source),
        words=_words(segs, max(start, 0.0), source),
    )


def _duration(event: dict[str, Any], source: str) -> float:
    """How long this cue lasts, in seconds.

    Absent is fine and means zero — the field is genuinely optional, and a cue
    with no duration still has the start that makes it citable. Present but
    not a number is not fine, and says so rather than raising a bare
    `TypeError` from arithmetic several frames down.

    Negative durations are clamped rather than rejected, which is what
    `parse.vtt` and `parse.srt` already do with a cue that ends before it
    begins. A `Cue` whose `end` precedes its `start` would put a passage's end
    before its own beginning, and every consumer downstream would have to
    defend against it.
    """
    return max(
        require_finite(event.get("dDurationMs", 0), "dDurationMs", source, "json3")
        / 1000,
        0.0,
    )


def _words(segs: list[dict[str, Any]], start: float, source: str) -> tuple[Word, ...]:
    """Per-word timings, but only when the source actually supplied offsets.

    Manual tracks put the whole cue in one seg with no offset; inventing word
    timings for them by dividing the duration would be a fabrication that
    later code could not tell from real data.
    """
    words = []
    has_offsets = False

    for seg in segs:
        text = str(seg.get("utf8", "")).strip()
        if not text:
            continue
        offset = seg.get("tOffsetMs")
        if offset is not None:
            has_offsets = True
        seconds = require_finite(offset or 0, "tOffsetMs", source, "json3") / 1000
        words.append(Word(text=text, start=max(start + seconds, 0.0)))

    return tuple(words) if has_offsets else ()
