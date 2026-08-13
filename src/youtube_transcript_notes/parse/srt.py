"""SubRip.

The simplest of the three and the least informative: no word timings, and
YouTube's exports open with a zero-length empty cue, which is dropped here
along with any other cue that turns out to carry no text.
"""

from __future__ import annotations

import re
from html import unescape

from ..models import Cue
from .base import iter_cue_blocks, normalise, parsers

__all__ = ["parse_srt"]

_TAG = re.compile(r"<[^>]*>")


@parsers.register("srt", "subrip")
def parse_srt(payload: str, source: str = "<unknown>") -> list[Cue]:
    cues = []

    for (start, end), text_lines in iter_cue_blocks(payload, source, "srt"):
        text = normalise(unescape(_TAG.sub("", "\n".join(text_lines))))
        if not text:
            continue

        cues.append(Cue(text=text, start=start, duration=max(end - start, 0.0)))

    return cues
