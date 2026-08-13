"""Output renderers.

Importing this package registers every built-in renderer. The imports below
look unused and are not: each one runs a `@renderers.register` decorator.
"""

from .base import Renderer, get_renderer, renderers
from .citation import CitationRenderer
from .context import ContextRenderer
from .jsonl import JsonLinesRenderer
from .markdown import MarkdownRenderer
from .plain import PlainRenderer

__all__ = [
    "CitationRenderer",
    "ContextRenderer",
    "JsonLinesRenderer",
    "MarkdownRenderer",
    "PlainRenderer",
    "Renderer",
    "get_renderer",
    "renderers",
]
