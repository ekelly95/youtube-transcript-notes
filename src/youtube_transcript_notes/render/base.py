"""The renderer contract.

A renderer is a pure function of a `Lecture`: no I/O, no clock, no network.
Renderers register by decorator, so a new one reaches the CLI automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from ..errors import UnknownRenderer
from ..models import Lecture
from ..registry import Registry

__all__ = ["Renderer", "get_renderer", "renderers"]

renderers: Registry[type[Renderer]] = Registry("renderer", UnknownRenderer)


class Renderer(ABC):
    """Turns a lecture into text."""

    #: Extension a file of this output would conventionally carry.
    extension = "txt"

    #: Whether this renderer rations output against ``--budget``.
    takes_budget = False

    #: What joins two rendered lectures in one document. The CLI renders one
    #: at a time and joins them with this; JSONL overrides it.
    separator = "\n\n\n"

    @abstractmethod
    def render(self, lecture: Lecture) -> str:
        """Render one lecture."""

    def render_many(self, lectures: Sequence[Lecture]) -> str:
        """Render several lectures into one document.

        Defined as `separator`-joined single renders; the CLI relies on that,
        and a test pins it.
        """
        return self.separator.join(self.render(lecture) for lecture in lectures)


def get_renderer(name: str, **options: object) -> Renderer:
    """Look up a renderer by name or alias and instantiate it.

    Raises `UnknownRenderer` listing the available names. `options` go to the
    renderer's constructor.
    """
    return renderers.get(name)(**options)
