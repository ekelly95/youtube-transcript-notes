"""A content cache for fetched caption payloads.

Re-running an analysis should be free and should give the same answer. That
matters more here than it does for most caching: a transcript you cited last
week ought to still say what you quoted, and a lecture that has since been
edited or taken down should not silently change your notes.

Keys are derived from what identifies a track — source, tier, language,
format — never from the URL, because YouTube's caption URLs carry expiring
signatures and would miss on every run.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from .atomic import atomic_write
from .errors import PayloadTooLarge
from .limits import MAX_PAYLOAD_BYTES, describe_size

__all__ = ["Cache", "NullCache", "default_cache_root"]

#: Environment override for the cache location, consulted before the platform
#: default. Named after the project so it cannot collide with anything.
CACHE_ENV_VAR = "YOUTUBE_TRANSCRIPT_NOTES_CACHE"


def default_cache_root() -> Path:
    """Where captions are cached when the caller does not say.

    Deliberately *not* a directory beneath the working directory. Cache keys
    are derived from track identity, so the same track is the same entry
    wherever it is asked for — which is only worth anything if there is one
    cache rather than one per directory someone happened to be standing in.
    Anything driving the tool from a script or an agent has an unpredictable
    working directory, and would otherwise scatter caches and re-download on
    every run.

    `YOUTUBE_TRANSCRIPT_NOTES_CACHE` wins if set. Otherwise the platform's own
    location for data that is expensive to fetch and safe to delete.
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


#: Separator for key parts. A vertical bar cannot appear in a language tag,
#: a caption format or a tier name, so no two different tracks can collide by
#: their parts running together.
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
        # One level of fan-out, so a few thousand lectures do not land in a
        # single directory.
        return self.root / key[:2] / f"{key}.txt"

    def read(self, key: str) -> str | None:
        """The cached payload, or None if there is not one.

        Capped like every other read. Nothing this version writes can exceed
        the ceiling, so the entry that trips this was either written by an
        older version or put there by hand — but a cache hit is still a read
        of a file, and "the payload came from us last week" is not a reason to
        take it on trust now.
        """
        path = self.path_for(key)
        try:
            size = path.stat().st_size
        except OSError:
            # "Is it there?" and "how big is it?" used to be two questions with
            # a gap between them, and a cache directory is exactly the place a
            # cleaner or a parallel run sweeps a file during that gap. Asking
            # once and treating the refusal as absence covers the entry that was
            # never written and the one that vanished mid-answer alike.
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
            # A corrupt or unreadable entry is a miss, not an error: the refetch
            # overwrites it. Escaping instead reached the CLI's last resort as
            # "retry — transient network failures are common", a diagnosis no
            # retry could ever repair because every retry rereads the same bytes.
            # Same reasoning as `_recall`'s ValueError catch on the manifest.
            return None

    def write(self, key: str, payload: str) -> None:
        """Store a payload, or quietly do without one.

        Best-effort on purpose, and the only method here allowed to fail
        silently. A cache is an optimisation over work that has already
        succeeded, so a full disk or a read-only cache directory must not turn
        a lecture that was fetched, parsed and rendered into a failed run —
        which is what it did while this raised, and it was reported as an
        *acquisition* failure, blaming the one part of the run that worked.

        The cost of swallowing it is that the next run misses and fetches
        again. That is exactly what `--no-cache` asks for on purpose, so it is
        a mode the rest of the tool already handles.
        """
        try:
            # A cache entry is either whole or absent, never a half-written file
            # left behind by an interrupted run — see `atomic`.
            atomic_write(self.path_for(key), payload)
        except OSError:
            return


class NullCache(Cache):
    """Caches nothing. For tests, and for anyone who wants every run live."""

    def __init__(self) -> None:
        super().__init__(root=Path("."))

    def read(self, key: str) -> str | None:
        return None

    def write(self, key: str, payload: str) -> None:
        return None
