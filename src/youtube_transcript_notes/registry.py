"""One generic registry for every pluggable component.

Renderers, parsers and providers all register here by decorator, so the CLI's
``--format`` choices, the unknown-name error and the provider list derive from
the same place.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

from .errors import ConfigError

__all__ = ["Registry"]

T = TypeVar("T")


class Registry(Generic[T]):
    """Name-to-component lookup whose miss raises ``error`` listing what exists."""

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
        """Register directly. A duplicate name is a programming error and fails
        at import time."""
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
        """Canonical names, in registration order."""
        return tuple(self._primary)

    def keys(self) -> tuple[str, ...]:
        """Every accepted name including aliases, for argparse ``choices``."""
        return tuple(self._items)

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._primary)

    def __len__(self) -> int:
        return len(self._primary)
