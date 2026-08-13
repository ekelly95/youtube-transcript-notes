"""The renderer contract.

A renderer is a pure function of a `Lecture`: same lecture in, same string
out, no I/O, no clock, no network. That is what makes golden-file tests worth
writing, and what lets the CLI re-render from cache without touching the
source again.

Renderers register themselves by decorator, so adding one makes it available
to the CLI without editing an argument list somewhere else.
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

    #: Whether this renderer rations its output against a token budget, and so
    #: whether ``--budget`` means anything to it. Declared here rather than
    #: known to the CLI, so the command line does not hard-code a renderer's
    #: name and a second budgeted format would need no change there.
    takes_budget = False

    #: What joins two rendered lectures in one document. An attribute rather
    #: than a detail of `render_many`, because the CLI renders lectures one at
    #: a time — so a renderer that crashes costs that lecture, not the batch —
    #: and joins the survivors itself. Line-oriented formats override it: a
    #: blank line between JSONL documents is not JSONL.
    separator = "\n\n\n"

    @abstractmethod
    def render(self, lecture: Lecture) -> str:
        """Render one lecture."""

    def render_many(self, lectures: Sequence[Lecture]) -> str:
        """Render several lectures into one document.

        Lives on the base class rather than being reimplemented per renderer,
        which is where that repetition would otherwise accumulate. Defined as
        `separator`-joined single renders, and pinned that way by a test — the
        CLI depends on the decomposition.
        """
        return self.separator.join(self.render(lecture) for lecture in lectures)


def get_renderer(name: str, **options: object) -> Renderer:
    """Look up a renderer by name or alias and instantiate it.

    Raises `UnknownRenderer` listing the available names, rather than a
    `KeyError` that tells the caller nothing.

    `options` reach the renderer's constructor. Passing one a renderer does
    not accept is a caller error, not a user error — the CLI checks
    `takes_budget` before offering `--budget` rather than letting a `TypeError`
    surface as a failed lecture.
    """
    return renderers.get(name)(**options)
