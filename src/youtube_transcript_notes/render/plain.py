"""Plain reading text — no timestamps, no headings, no markup."""

from __future__ import annotations

from ..models import Lecture
from .base import Renderer, renderers

__all__ = ["PlainRenderer"]


@renderers.register("plain", "text")
class PlainRenderer(Renderer):
    """Just the words, as paragraphs.

    For reading straight through, or for pasting into something that will do
    its own formatting. Everything citable is deliberately stripped — reach
    for `markdown` when the timestamps matter.
    """

    extension = "txt"

    def render(self, lecture: Lecture) -> str:
        return lecture.text
