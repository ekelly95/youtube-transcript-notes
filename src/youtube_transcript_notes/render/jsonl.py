"""One JSON object per passage — the shape an embedding pipeline wants.

Each record is self-contained on purpose. A chunk that comes back from a
vector search carries everything needed to cite it without a second lookup:
the title, the section it sat in, the timestamp, and a link that opens at the
right moment.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ..models import Lecture
from .base import Renderer, renderers
from .escape import safe_url

__all__ = ["JsonLinesRenderer"]


@renderers.register("jsonl")
class JsonLinesRenderer(Renderer):
    """One self-contained, citable record per passage."""

    extension = "jsonl"

    #: Newline, not the base class's blank line: a blank line between JSONL
    #: documents would make the combined output invalid JSONL.
    separator = "\n"

    def render(self, lecture: Lecture) -> str:
        return "\n".join(
            json.dumps(record, ensure_ascii=False) for record in _records(lecture)
        )


def _records(lecture: Lecture) -> Iterator[dict[str, Any]]:
    meta = lecture.meta
    provenance = lecture.provenance

    for index, (section, passage) in enumerate(lecture.walk()):
        locator = lecture.locator_for(passage, section)
        yield {
            "id": f"{meta.source_id}:{index}",
            "source_id": meta.source_id,
            "title": meta.title,
            "channel": meta.channel,
            "section": section.title,
            "speaker": passage.speaker,
            "turn": passage.turn,
            "text": passage.text,
            "start": passage.start,
            "end": passage.end,
            "timestamp": locator.timestamp,
            # Validated like every other emitted link: a chunk that comes back
            # from a vector search gets rendered somewhere, and `webpage_url`
            # is transport-supplied. Unsafe becomes null, same as absent.
            "url": safe_url(locator.url),
            "tier": provenance.tier.value,
            "language": provenance.language,
        }
