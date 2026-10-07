"""A content cache for fetched caption payloads.

A transcript cited last week should still say what was quoted, and a re-run
should be free. Keys come from what identifies a track — source, tier,
language, format — never from the URL, whose signature expires.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from .atomic import atomic_write
from .errors import PayloadTooLarge
from .limits import MAX_PAYLOAD_BYTES, describe_size

__all__ = ["Cache", "NullCache", "RefreshCache", "default_cache_root"]

#: Environment override for the cache location.
CACHE_ENV_VAR = "YOUTUBE_TRANSCRIPT_NOTES_CACHE"


def default_cache_root() -> Path:
    """Where captions are cached when the caller does not say.

    One per-user cache rather than one per working directory, because scripts
    and agents run from unpredictable directories and would re-download every
    time. `YOUTUBE_TRANSCRIPT_NOTES_CACHE` wins if set.
    """
    override = os.environ.get(CACHE_ENV_VAR)
    if override:
        return Path(override)

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "youtube-transcript-notes" / "cache"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "youtube-transcript-notes"

    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "youtube-transcript-notes"


#: Cannot appear in a language tag, format or tier, so key parts cannot collide.
_KEY_SEPARATOR = "|"


class Cache:
    """Stores caption payloads on disk, one file per track."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_cache_root()

    @staticmethod
    def key(*parts: str) -> str:
        """A stable key from the parts that identify a track."""
        joined = _KEY_SEPARATOR.join(parts)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]

    def path_for(self, key: str) -> Path:
        # One level of fan-out keeps directories small.
        return self.root / key[:2] / f"{key}.txt"

    def read(self, key: str) -> str | None:
        """The cached payload, or None if there is not one.

        Capped like every other read: a cache hit is still a file on disk.
        """
        path = self.path_for(key)
        try:
            size = path.stat().st_size
        except OSError:
            # Missing, or swept away by a cleaner mid-run: either way, a miss.
            return None

        if size > MAX_PAYLOAD_BYTES:
            raise PayloadTooLarge(
                source=f"cache entry {key}",
                measured=describe_size(size),
                limit=describe_size(MAX_PAYLOAD_BYTES),
            )
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A corrupt entry is a miss; the refetch overwrites it.
            return None

    def write(self, key: str, payload: str) -> None:
        """Store a payload, or quietly do without one.

        Best-effort: a full disk or read-only cache must not fail a source that
        was fetched and rendered. The cost is a refetch next time.
        """
        try:
            atomic_write(self.path_for(key), payload)
        except OSError:
            return


class RefreshCache(Cache):
    """Reads nothing but stores everything: one fresh run that refills the cache.

    For captions that changed upstream since they were cached. A transport
    failure is reported rather than answered from the cache.
    """

    def read(self, key: str) -> str | None:
        return None


class NullCache(Cache):
    """Caches nothing. For tests, and for anyone who wants every run live."""

    def __init__(self) -> None:
        super().__init__(root=Path("."))

    def read(self, key: str) -> str | None:
        return None

    def write(self, key: str, payload: str) -> None:
        return None
