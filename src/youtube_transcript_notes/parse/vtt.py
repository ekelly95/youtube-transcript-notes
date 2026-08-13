"""WebVTT.

Straightforward for human-written tracks. Automatic tracks are the awkward
case: YouTube renders them as a scrolling two-line window, and expresses that
by *repeating* the previous window's text as the leading line of each cue,
punctuated by ten-millisecond "flush" cues. Real observed sequence::

    00:00:01.819 --> 00:00:01.829
    the following content is provided under

    00:00:01.829 --> 00:00:04.579
    the following content is provided under
    a<00:00:01.979><c> Creative</c><00:00:02.100><c> Commons</c>

That repetition is left intact here on purpose. It is what the source
published, and removing it is the deduplication stage's job — a parser that
quietly dropped text would be indistinguishable from a parser that lost it.
"""

from __future__ import annotations

import re
from html import unescape

from ..models import Cue, Word
from .base import iter_cue_blocks, normalise, parse_clock, parsers

__all__ = ["parse_vtt"]

#: An inline word timing: ``<00:00:00.149><c> following</c>``. The ``<c>`` tag
#: may carry style classes, as in ``<c.colorE5E5E5>``.
_WORD = re.compile(r"<(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})><c[^>]*>([^<]*)</c>")

_TAG = re.compile(r"<[^>]*>")


@parsers.register("vtt", "webvtt")
def parse_vtt(payload: str, source: str = "<unknown>") -> list[Cue]:
    cues = []

    for (start, end), text_lines in iter_cue_blocks(payload, source, "vtt"):
        raw = "\n".join(text_lines)
        text = normalise(unescape(_TAG.sub("", raw)))
        if not text:
            continue

        cues.append(
            Cue(
                text=text,
                duration=max(end - start, 0.0),
                start=start,
                words=_words(raw, start),
            )
        )

    return cues


def _words(raw: str, start: float) -> tuple[Word, ...]:
    """Per-word timings from inline tags.

    The first word of a cue has no tag of its own — it sits immediately before
    the first timestamp. On an automatic track the text preceding that
    timestamp also contains the carried-over previous window, so only its last
    token is the word actually being timed.
    """
    matches = list(_WORD.finditer(raw))
    if not matches:
        return ()

    words = []
    head = normalise(unescape(_TAG.sub("", raw[: matches[0].start()]))).split()
    if head:
        words.append(Word(text=head[-1], start=start))

    for match in matches:
        moment = parse_clock(match.group(1))
        text = normalise(unescape(match.group(2)))
        if text and moment is not None:
            words.append(Word(text=text, start=moment))

    return tuple(words)
