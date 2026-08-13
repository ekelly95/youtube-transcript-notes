"""How much of anything the tool will accept before refusing.

One failed source is supposed to cost one item and leave the rest of a batch
alone — `cli.run` catches per source to make sure of it. That promise is only
worth as much as the process it runs in, and an unbounded read cannot be
caught: a payload large enough to exhaust memory takes the interpreter with it,
along with every lecture that had already succeeded.

So the limits live here rather than at each call site, because "how big is too
big" is one policy, and a number that appears twice will eventually appear as
two different numbers.

The values are set against measurement, not taste. A 53-minute lecture with
YouTube's automatic captions — the wordiest real form, one event per phrase
with word-level timing — runs at roughly 14 KB and 35 events per minute.
Human-written captions are about a fifth of that. Every ceiling below is
therefore far outside anything a lecture produces, which is the point: a limit
that a real recording could reach would be a bug reported as data loss.

Bytes alone are not enough, which is why there are counts too. Roughly forty
bytes of JSON buys one event, so a payload inside the byte ceiling can still
describe hundreds of thousands of objects and cost far more expanded than it
did on the wire. The counts are what make that shape bounded.
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

#: Largest caption payload read from anywhere — a URL or a local file.
#:
#: About forty hours of automatic captions, or eight days of human-written
#: ones. Chosen so that the decoded text and everything derived from it stay
#: within a few hundred megabytes on the way through.
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024

#: Largest number of timed entries in a structured payload: json3 ``events``.
#:
#: About 120 hours of automatic captions. Well under the roughly 900,000 that
#: `MAX_PAYLOAD_BYTES` of minimal JSON objects would otherwise allow, which is
#: deliberate — this is the ceiling that binds when a payload is small on the
#: wire and enormous once parsed.
MAX_EVENTS = 250_000

#: Largest number of cues a parser will produce, for the line-based formats
#: where there is no container to count first. Same order as `MAX_EVENTS`, and
#: for the same reason: a WebVTT cue costs about thirty bytes.
MAX_CUES = 250_000

#: Largest glossary or corrections file. A glossary is a list of names, and a
#: megabyte of them is roughly fifty thousand terms — orders past any list a
#: person maintains, and still small enough that reading it costs nothing.
MAX_GLOSSARY_BYTES = 1024 * 1024

#: Largest number of corrections read from a file. Same reasoning as
#: `MAX_EVENTS`: a small JSON document can describe an enormous number of
#: objects, and every one of them is scanned against every passage.
MAX_CORRECTIONS = 10_000

#: Largest number of glossary entries — terms plus named wrong forms. An
#: order *below* `MAX_CORRECTIONS`, because the two are not the same work: a
#: correction's named form is a dictionary hit per word window, while every
#: glossary term runs an edit distance against every window of every passage.
#: Measured: 4,000 terms cost about thirty seconds on one lecture, and the
#: byte ceiling alone would admit roughly 35,000 — minutes per lecture, which
#: on a long playlist reads as a hang. Two thousand is generations of any
#: hand-maintained list. Refused whole, never truncated.
MAX_GLOSSARY_TERMS = 2_000

#: Largest playlist `expand` will turn into sources. Unlike the byte ceilings
#: this bounds *work* rather than memory: every video in a playlist costs a
#: couple of requests and one output file. A real lecture course runs 20-100
#: videos with recitations included, so 500 is far outside any single course —
#: while a channel's auto-generated "uploads" playlist runs to thousands,
#: which is a channel wearing a playlist URL, and channels are refused by
#: name. Refused whole rather than truncated, like everything else here.
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

    Two checks and not one. `stat` is the cheap refusal that avoids reading a
    huge file at all, but it is a claim about a moment already past: a
    recording still being written, a synced folder, or a path that is not a
    plain file at all can all deliver more than it promised. So the read is
    capped too, and asks for one byte past the limit — enough to tell a file
    that exactly fits from one that does not.

    ``utf-8-sig`` because several caption tools emit a byte order mark, and a
    leading ``\\ufeff`` turns ``WEBVTT`` into a word the parser does not
    recognise while looking identical in any editor.
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

    # Text mode would translate `\r\n` and `\r` to `\n` on the way in. Reading
    # bytes is what makes the size bound possible and skips that, so it is done
    # here instead: most caption files written on Windows have CRLF endings,
    # and a payload that changed shape because of how it was *measured* is a
    # strange bug to go looking for later.
    text = data.decode("utf-8-sig")
    return text.replace("\r\n", "\n").replace("\r", "\n")
