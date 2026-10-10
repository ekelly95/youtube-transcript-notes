"""Writing a file so that it is either whole or absent.

Used by the caption cache and by `--out`. The bytes go to a scratch file beside
the target, are fsynced, and then replace the target in one step.

The scratch name carries the pid and a counter, because cache paths derive from
track identity and two writers of the same track would otherwise share one.
On Windows a replace that races another writer's replace fails with
`PermissionError`, so the replace is retried briefly.
"""

from __future__ import annotations

import itertools
import os
import time
from pathlib import Path

__all__ = ["atomic_write"]

_SCRATCH_COUNTER = itertools.count()

#: Replace attempts and the base backoff, doubled each time (~60 ms in total).
#: Measured to clear Windows sharing violations; past that, waiting won't help.
_REPLACE_ATTEMPTS = 6
_REPLACE_BACKOFF = 0.002


def scratch_path(path: Path) -> Path:
    """A scratch path beside ``path``, unique to this writer.

    `with_name`, not `with_suffix`: a title may contain a full stop.
    """
    token = f"{os.getpid()}.{next(_SCRATCH_COUNTER)}"
    return path.with_name(f"{path.name}.{token}.partial")


def atomic_write(path: Path, text: str, *, overwrite: bool = True) -> None:
    """Write ``text`` to ``path``, leaving no half-written file behind.

    Creates the parent directory. With ``overwrite=False`` the name is claimed
    exclusively first and `FileExistsError` is raised if it is taken.

    Guarantees: a reader never sees a partial file, and an interrupted run
    leaves the old contents or the new ones — the fsync is what makes that hold
    across a power cut. Not guaranteed: that the rename itself survived a crash
    (directories cannot be synced portably), and under ``overwrite=False`` a
    process killed between claim and replace leaves an empty file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = scratch_path(path)
    claimed = False
    try:
        _write_durably(temporary, text)
        if not overwrite:
            _claim(path)
            claimed = True
        _replace_retrying(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        if claimed:
            path.unlink(missing_ok=True)
        raise


def _claim(path: Path) -> None:
    """Take the name exclusively, or raise `FileExistsError`.

    ``O_EXCL`` makes check-and-create one operation, so two writers cannot both
    find the name free. Done after the durable write, so the slow half is over
    before the name is reserved.
    """
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))


def _write_durably(path: Path, text: str) -> None:
    """Write ``text`` and force it to the disk before the file has a real name.

    Newlines are written as ``\n`` on every platform, so a notes folder
    synced between Windows and anything else does not churn on re-render.
    """
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_retrying(temporary: Path, path: Path) -> None:
    """Replace ``path``, retrying only `PermissionError` (a lost race on Windows)."""
    for attempt in range(_REPLACE_ATTEMPTS - 1):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            time.sleep(_REPLACE_BACKOFF * 2**attempt)

    # Outside the loop so the final failure simply raises.
    temporary.replace(path)
