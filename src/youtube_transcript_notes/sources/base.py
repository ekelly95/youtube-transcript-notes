"""What every source provider has to provide.

Two methods: `list` discovers what exists without downloading captions, and
`load` retrieves one track's payload. Parsing, reassembly and provenance are
shared in `resolve.TrackHandle.fetch`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..cache import Cache, NullCache
from ..errors import TranscriptError, UnknownProvider
from ..registry import Registry
from ..resolve import TrackManifest

__all__ = ["Expansion", "SourceProvider", "get_provider", "provider_for", "providers"]

providers: Registry[type[SourceProvider]] = Registry("provider", UnknownProvider)


@dataclass(frozen=True)
class Expansion:
    """What one source turns into before discovery. Nearly always itself.

    A playlist or folder fans out here, so each item goes through the same
    per-source loop and failure isolation as a source typed by hand.
    """

    sources: tuple[str, ...]

    origin: str | None = None
    """The collection the sources were expanded from, when they were."""

    stale_reason: TranscriptError | None = None
    """Set when the sources came from the cache because the transport could not
    be reached — see `TrackManifest.stale_reason`."""


class SourceProvider(ABC):
    """Somewhere transcripts come from."""

    name = "source"

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        cache: Cache | None = None,
    ) -> None:
        """`clock` pins provenance timestamps in tests. `cache` lives here so
        callers can configure it without knowing which provider is chosen."""
        self._clock = clock or _utc_now
        self.cache = cache if cache is not None else NullCache()

    def now(self) -> datetime:
        return self._clock()

    @classmethod
    def handles(cls, source: str) -> bool:
        """Whether this provider recognises `source`. Used to pick one."""
        return False

    def expand(self, source: str) -> Expansion:
        """The individually fetchable sources this names. Almost always itself."""
        return Expansion(sources=(source,))

    @abstractmethod
    def list(self, source: str) -> TrackManifest:
        """Discover available tracks. Must not download any caption payload."""

    @abstractmethod
    def load(self, ref: Any) -> str:
        """Retrieve one track's raw caption payload."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_provider(name: str, **kwargs: Any) -> SourceProvider:
    return providers.get(name)(**kwargs)


def provider_for(source: str, **kwargs: Any) -> SourceProvider:
    """The first registered provider that recognises `source`.

    The local provider registers first, so an existing path beats a YouTube
    video id: any eleven characters from ``[A-Za-z0-9_-]`` look like an id, and
    a file by that name is what its creator meant.
    """
    for name in providers:
        candidate = providers.get(name)
        if candidate.handles(source):
            return candidate(**kwargs)

    raise UnknownProvider(
        kind="provider", name=source, available=", ".join(providers.names())
    )
