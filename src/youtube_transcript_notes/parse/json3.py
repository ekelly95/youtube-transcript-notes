"""YouTube's ``json3`` caption format — the preferred source.

It carries per-word offsets on automatic tracks, and expresses rolling-window
scrolling as separate ``aAppend`` events rather than repeated text, so
dropping those events yields clean cues with no deduplication.

Observed shapes, from MIT OpenCourseWare captions:

* manual — each event is ``{tStartMs, dDurationMs, segs}`` with one seg
  holding the whole cue.
* automatic — a leading window-definition event with no ``segs``, then
  content events alternating with newline-only ``aAppend`` events. Content
  segs carry ``tOffsetMs`` and ``acAsrConf``.
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

    events = require_list(data["events"], "'events'", source, "json3")

    # Counted before anything is built: a payload inside the byte ceiling can
    # still describe far more events than memory allows. See `limits`.
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

    segs = [
        require_object(seg, "seg", source, "json3")
        for seg in require_list(raw_segs, "'segs'", source, "json3")
    ]

    # Scroll padding: only a newline, duplicating nothing.
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
        # Clamped, as negative durations are, so nothing begins before zero.
        start=max(start, 0.0),
        duration=_duration(event, source),
        words=_words(segs, max(start, 0.0), source),
    )


def _duration(event: dict[str, Any], source: str) -> float:
    """How long this cue lasts, in seconds.

    Absent means zero. Negative is clamped, as `parse.vtt` and `parse.srt` do,
    so a cue never ends before it begins.
    """
    return max(
        require_finite(event.get("dDurationMs", 0), "dDurationMs", source, "json3")
        / 1000,
        0.0,
    )


def _words(segs: list[dict[str, Any]], start: float, source: str) -> tuple[Word, ...]:
    """Per-word timings, only when the source actually supplied offsets.

    Manual tracks have none, and dividing the duration would fabricate them.
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
