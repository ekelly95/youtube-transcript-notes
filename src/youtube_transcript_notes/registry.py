"""A tiny registry, used for every pluggable component.

One generic registry serves both renderers and source providers. The
alternative — a hand-maintained dict per plugin family — lets them drift; a
single parameterised registry means the CLI's ``--format`` choices, the error
message for an unknown name, and the list of available providers all derive
from the same place and cannot disagree.

Registration is by decorator, so a component is registered next to its
definition rather than in a list somewhere else that someone will forget.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

from .errors import ConfigError

__all__ = ["Registry"]

T = TypeVar("T")


class Registry(Generic[T]):
    """Name-to-component lookup with a helpful failure.

    ``kind`` names what is being registered, and ``error`` is the
    `ConfigError` subclass raised when a lookup misses — so the caller gets
    ``UnknownRenderer`` rather than a bare ``KeyError`` with no list of what
    they could have asked for instead.
    """

    def __init__(self, kind: str, error: type[ConfigError]) -> None:
        self._kind = kind
        self._error = error
        self._items: dict[str, T] = {}
        self._primary: list[str] = []

    def register(self, name: str, *aliases: str) -> Callable[[T], T]:
        """Decorator registering a component under ``name`` plus any aliases."""

        def decorator(item: T) -> T:
            self.add(name, item, *aliases)
            return item

        return decorator

    def add(self, name: str, item: T, *aliases: str) -> None:
        """Register directly. A duplicate name is a programming error, not a
        user error, so it fails loudly at import time rather than resolving to
        whichever module happened to load last."""
        for key in (name, *aliases):
            if key in self._items:
                raise ValueError(f"{self._kind} {key!r} is already registered")
            self._items[key] = item
        self._primary.append(name)

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            raise self._error(
                kind=self._kind,
                name=name,
                available=", ".join(self.names()),
            ) from None

    def names(self) -> tuple[str, ...]:
        """Canonical names, in registration order. For help text and docs."""
        return tuple(self._primary)

    def keys(self) -> tuple[str, ...]:
        """Every accepted name including aliases. For argparse ``choices``."""
        return tuple(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._primary)

    def __len__(self) -> int:
        return len(self._primary)
