"""Writing a file so that it is either whole or absent.

Two places need this: the caption cache, and the documents `--out` writes.
Both used to spell it out themselves, and both spelled it the same way — write
beside the target, then rename over it — which is exactly the kind of
duplication that drifts once one of them is fixed and the other is not.

The scratch name carries the writer's identity. A fixed ``.partial`` suffix is
not enough: cache paths are derived from track identity, so two of its
processes fetching the same track derive the *same* scratch path, and one
renames the other's half-written bytes into place. The pid separates processes
and the counter separates threads within one.

Unique scratch names are not the whole of the concurrency story, though, and
this file used to claim they were. They stop two writers sharing a *source*;
they do nothing about two writers sharing a *destination*. On Windows,
replacing a file that another writer is replacing at the same instant fails
with `PermissionError` — measured at nearly 9% of writes with three
processes on one target — so the replace is retried briefly. It is a sharing
violation, not a verdict: the same call a moment later succeeds.
"""

from __future__ import annotations

import itertools
import os
import time
from pathlib import Path

__all__ = ["atomic_write"]

_SCRATCH_COUNTER = itertools.count()

#: How many times to retry a replace that lost a race, and the base delay
#: doubled after each attempt. Five short sleeps total about 60ms, which was
#: enough to take a measured 8.78% failure rate to zero; the sixth attempt
#: raises rather than waiting longer, because past this the cause is not
#: contention and waiting will not fix it.
_REPLACE_ATTEMPTS = 6
_REPLACE_BACKOFF = 0.002


def scratch_path(path: Path) -> Path:
    """A scratch path beside ``path``, unique to this writer.

    `with_name` rather than `with_suffix`: a lecture title may well contain a
    full stop, and `with_suffix` would eat everything after it.
    """
    token = f"{os.getpid()}.{next(_SCRATCH_COUNTER)}"
    return path.with_name(f"{path.name}.{token}.partial")


def atomic_write(path: Path, text: str, *, overwrite: bool = True) -> None:
    """Write ``text`` to ``path``, leaving no half-written file behind.

    The parent directory is created if it does not exist. A failed write cleans
    up after itself rather than leaving litter that looks like a cache entry.

    With ``overwrite=False`` the name is claimed exclusively first, so a file
    already there is left untouched and `FileExistsError` is raised instead.
    The default stays `True` because the cache shares this primitive and must
    keep replacing its own entries; only `--out` opts out.

    What is guaranteed, precisely, because this primitive backs both the cache
    and the reader's notes and a vague promise about either is worth nothing:

    - **A reader never sees a partial file.** The bytes are written under a
      different name and the destination is replaced in one step, so no other
      process can observe the file mid-write.
    - **An interrupted run leaves the old contents or the new ones**, never a
      mixture — including a power cut. This is what `os.fsync` below buys, and
      it is not a formality: without it the rename can reach the disk before
      the data does, and the file that survives has the new name, the new
      length, and a run of zeros where the notes should be. Old-or-new is a
      claim about content, and it is only true if the content is on the disk
      before the name points at it.

    What is *not* guaranteed: that a write which returned successfully has
    survived a power cut. The rename itself is not forced to disk — there is no
    portable way to sync a directory on Windows — so a crash in the moment
    after can still leave the previous contents. That is a lost update, not a
    corrupted file, and re-running restores it. Notes are regenerable; a
    half-written note would not be.

    Nor, under ``overwrite=False``, that an interrupted run leaves nothing
    behind: a process killed between claiming the name and replacing it leaves
    an empty file, which the next run then refuses. The window is microseconds
    and sits *after* the bytes are durable, so nothing is lost — deleting the
    empty file, or passing ``--force``, clears it.
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
    """Take the name exclusively, or raise `FileExistsError` because it is taken.

    ``O_EXCL`` is the whole point. "Is anything there?" and "put this here" are
    one operation the operating system settles, so two writers cannot both find
    the name free — where a preflight `path.exists()` answers a question about a
    moment already past. That race is why the earlier audit refused a no-clobber
    check outright, and it does not have to be reintroduced to get one.

    Claiming *after* the durable write and before the replace, so the expensive,
    failure-prone half happens first. What exists for the instant in between is
    an empty file: a name reserved, not a note half written. Nothing was there
    to lose, and the bytes that replace it are already on the disk.
    """
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))


def _write_durably(path: Path, text: str) -> None:
    """Write ``text`` and force it to the disk before the file has a real name."""
    with path.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_retrying(temporary: Path, path: Path) -> None:
    """Replace ``path``, waiting out a writer that got there first.

    Only `PermissionError` is retried, and only because on Windows that is how
    a lost race announces itself. A genuine permission problem raises after the
    same handful of attempts — about sixty milliseconds later, which nobody
    notices — rather than being retried forever in the hope of changing.
    """
    for attempt in range(_REPLACE_ATTEMPTS - 1):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            time.sleep(_REPLACE_BACKOFF * 2**attempt)

    # The last attempt is outside the loop so that its failure is simply the
    # call raising, rather than a branch that decides to re-raise — which would
    # leave the loop with an exit path nothing can reach. Same reasoning as the
    # `while True` in `naming._candidates`.
    temporary.replace(path)
