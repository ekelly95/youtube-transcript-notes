"""A reference entry, plus an honest note about where the text came from.

Two things get emitted. The reference is what goes in a bibliography. The
provenance note is what stops the citation being quietly misleading: a reader
should know whether they are looking at words a person typed or a guess a
speech recogniser made, because on lecture material that distinction lands
hardest on exactly the technical vocabulary worth quoting.
"""

from __future__ import annotations

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
    """An APA-flavoured reference for the lecture, plus retrieval provenance.

    One style for now. When a second is wanted it should arrive as a separate
    registered renderer (``citation-harvard``) rather than a flag, so the CLI
    picks it up for free.
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
    # A bibliography entry is made to be followed. `webpage_url` is
    # transport-supplied, and a reference is better short than misleading.
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


def _note(provenance: Provenance) -> str:
    source = provenance.tier.prose
    return (
        f"Transcript retrieved {format_date(provenance.retrieved_at.date())} "
        f"from {source} ({provenance.language}, {provenance.caption_format}). "
        f"Content hash: {provenance.content_hash[:12]}."
    )
