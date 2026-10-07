"""How much of anything the tool will accept before refusing.

A failed source costs one item, but an unbounded read that exhausts memory
takes the whole batch with it, so every input has a ceiling. They live here so
the policy is stated once.

The values are far outside anything a real recording produces: a 53-minute
lecture with automatic captions (the wordiest form) is about 14 KB and 35
events per minute. Counts bound what bytes cannot — about forty bytes of JSON
buys one parsed event.
"""

from __future__ import annotations

from pathlib import Path

from .errors import PayloadTooLarge

__all__ = [
    "MAX_CORRECTIONS",
    "MAX_CUES",
    "MAX_EVENTS",
    "MAX_GLOSSARY_BYTES",
    "MAX_GLOSSARY_TERMS",
    "MAX_PAYLOAD_BYTES",
    "MAX_PLAYLIST_ITEMS",
    "describe_size",
    "read_capped",
]

#: Largest caption payload, from a URL or a file: ~40 hours of automatic captions.
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024

#: Largest number of json3 ``events`` (~120 hours of automatic captions). This
#: is the ceiling that binds when a payload is small on the wire but huge parsed.
MAX_EVENTS = 250_000

#: Largest number of cues from the line-based formats, which have no container
#: to count first.
MAX_CUES = 250_000

#: Largest glossary or corrections file (~50,000 terms).
MAX_GLOSSARY_BYTES = 1024 * 1024

#: Largest number of corrections read from a file.
MAX_CORRECTIONS = 10_000

#: Largest number of glossary entries. Lower than `MAX_CORRECTIONS` because each
#: term runs an edit distance against every word window: 4,000 terms measured at
#: about thirty seconds per lecture. Refused whole, never truncated.
MAX_GLOSSARY_TERMS = 2_000

#: Largest playlist `expand` will unroll. Bounds work, not memory: a course runs
#: 20-100 videos, while a channel's "uploads" playlist runs to thousands.
MAX_PLAYLIST_ITEMS = 500


def describe_size(size: int) -> str:
    """A byte count as something a person can compare to a limit at a glance."""
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.1f} MiB"


def read_capped(path: Path, limit: int = MAX_PAYLOAD_BYTES) -> str:
    """Read a text file, refusing past `limit` rather than allocating it.

    `stat` refuses early, but a growing or non-regular file can deliver more
    than it claimed, so the read is capped too (one byte past the limit tells
    "fits exactly" from "too big"). ``utf-8-sig`` strips the byte order mark
    some caption tools emit, which would otherwise hide a ``WEBVTT`` header.
    """
    size = path.stat().st_size
    if size > limit:
        raise PayloadTooLarge(
            source=path.name,
            measured=describe_size(size),
            limit=describe_size(limit),
        )

    with path.open("rb") as handle:
        data = handle.read(limit + 1)

    if len(data) > limit:
        raise PayloadTooLarge(
            source=path.name,
            measured=f"more than {describe_size(limit)}",
            limit=describe_size(limit),
        )

    # Binary reads skip text mode's newline translation, so do it here.
    text = data.decode("utf-8-sig")
    return text.replace("\r\n", "\n").replace("\r", "\n")
