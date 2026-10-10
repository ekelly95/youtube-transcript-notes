"""A reference entry, plus an honest note about where the text came from.

The provenance note says whether the words were typed by a person or guessed
by a recogniser — the difference that matters most for technical vocabulary.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from ..models import (
    MONTH_NAMES,
    Lecture,
    LectureMeta,
    Provenance,
    format_date,
)
from .base import Renderer, renderers
from .escape import safe_url

__all__ = ["CitationRenderer"]

#: Sites we can name properly in a reference. Anything else falls back to the
#: provider name rather than guessing.
_SITE_NAMES = {"youtube": "YouTube"}


@renderers.register("citation", "cite")
class CitationRenderer(Renderer):
    """An APA-flavoured reference, plus retrieval provenance.

    Another style should be a separate registered renderer, not a flag.
    """

    extension = "txt"

    def render(self, lecture: Lecture) -> str:
        return "\n\n".join(
            [_reference(lecture.meta, lecture.provenance), _note(lecture.provenance)]
        )


def _reference(meta: LectureMeta, provenance: Provenance) -> str:
    parts: list[str] = []

    if meta.channel:
        parts.append(f"{meta.channel}.")
    parts.append(f"({_apa_date(meta)}).")
    parts.append(f"{meta.title} [Video].")

    site = _SITE_NAMES.get(provenance.provider)
    if site:
        parts.append(f"{site}.")
    # `webpage_url` is transport-supplied; better no URL than a bad one.
    url = safe_url(meta.url)
    if url:
        parts.append(url)

    return " ".join(parts)


def _apa_date(meta: LectureMeta) -> str:
    """APA orders a video date year-first: ``2011, September 12``."""
    if meta.published is None:
        return "n.d."
    published = meta.published
    month = MONTH_NAMES[published.month - 1]
    return f"{published.year}, {month} {published.day}"


def _utc(moment: datetime) -> date:
    """The UTC calendar date: providers stamp retrieval in UTC, and a naive
    stamp read back from older JSONL is taken to be UTC already."""
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(UTC).date()


def _note(provenance: Provenance) -> str:
    source = provenance.tier.prose
    return (
        f"Transcript retrieved {format_date(_utc(provenance.retrieved_at))} "
        f"(UTC) from {source} ({provenance.language}, {provenance.caption_format}). "
        f"Content hash: {provenance.content_hash[:12]}."
    )
