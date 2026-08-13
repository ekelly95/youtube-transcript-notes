"""What every source of lectures has to provide.

The contract is deliberately two methods. `list` discovers what exists without
downloading captions; `load` retrieves one track's payload. Everything after
that — parsing, reassembly, provenance — is shared, and lives in
`resolve.TrackHandle.fetch`, so a new provider cannot accidentally reinvent
half the pipeline.
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

    A playlist and a folder are the exceptions: each names N lectures, and the
    provider contract below is one manifest for one lecture. So the fan-out
    happens here, before `list` is ever called, and the children go through the
    same per-source loop — and the same failure isolation — as sources typed by
    hand.
    """

    sources: tuple[str, ...]

    origin: str | None = None
    """The collection URL the sources were expanded from, when they were."""

    stale_reason: TranscriptError | None = None
    """Mirrors `TrackManifest.stale_reason`: set when the sources came from
    the cache because the transport could not be reached, so the caller can
    say so rather than pass off last week's roster as today's."""


class SourceProvider(ABC):
    """Somewhere lectures come from."""

    name = "source"

    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        cache: Cache | None = None,
    ) -> None:
        """`clock` exists so provenance timestamps can be pinned in tests.

        Everything downstream of a `Lecture` is a pure function of it, and
        that is only true if the one impure value in the model — when it was
        retrieved — can be controlled.

        `cache` lives on the base class so that callers can configure caching
        without knowing which provider will be chosen. Providers for which
        retrieval is already free simply never consult it.
        """
        self._clock = clock or _utc_now
        self.cache = cache if cache is not None else NullCache()

    def now(self) -> datetime:
        return self._clock()

    @classmethod
    def handles(cls, source: str) -> bool:
        """Whether this provider recognises `source`. Used to pick one."""
        return False

    def expand(self, source: str) -> Expansion:
        """The individually fetchable sources this names. Almost always itself.

        Concrete rather than abstract: only a provider whose addresses can
        name collections has anything to override.
        """
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

    Registration order decides ties, and the local provider is registered
    first — `sources/__init__.py` imports it before `youtube`, and ruff's
    import sorting keeps that order stable.

    Deliberately this way round. A path that exists is strong evidence about
    what the caller meant; a YouTube video ID is any eleven characters from
    ``[A-Za-z0-9_-]``, which a filename can match by accident. So a caption
    file named ``HtSuA80QTyo`` in the working directory is read from disk
    rather than fetched from YouTube, which is the answer someone who created
    that file wanted.
    """
    for name in providers:
        candidate = providers.get(name)
        if candidate.handles(source):
            return candidate(**kwargs)

    raise UnknownProvider(
        kind="provider", name=source, available=", ".join(providers.names())
    )
