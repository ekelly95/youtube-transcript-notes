"""Markdown notes with a timestamp on every paragraph — the default output.

Any sentence can be traced back to the moment it was said, in one click. A
YAML frontmatter block carries the citation fields for notes apps (Obsidian
properties, Dataview) and lets the CLI recognise a note it wrote itself.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence

from .._version import __version__
from ..models import (
    Correction,
    Lecture,
    LectureMeta,
    Locator,
    Passage,
    Provenance,
    format_date,
)
from .base import Renderer, renderers
from .escape import body, body_resumed, label, safe_url

__all__ = ["GENERATOR", "MarkdownRenderer", "read_frontmatter"]

#: How a note names the tool that wrote it, in its `generator` field.
GENERATOR = "youtube-transcript-notes"

#: Without published chapters, a heading every this many seconds...
_TIME_HEADING_SECONDS = 600

#: ...once the transcript runs at least this long. Shorter ones read fine whole.
_TIME_HEADING_MIN_SPAN = 1200


@renderers.register("markdown", "md")
class MarkdownRenderer(Renderer):
    """Headed, bylined, deep-linked notes."""

    extension = "md"

    def render(self, lecture: Lecture) -> str:
        lines = _frontmatter(lecture)
        # Every interpolation is `label`d or `body`d: it is the uploader's text.
        lines += [f"# {label(lecture.meta.title)}", ""]
        lines += [_byline(lecture.meta, lecture.provenance), ""]

        marker = _marker(lecture.corrections)
        # Speaker labels are single-line, so they escape with `label`.
        named = _marker(lecture.corrections, escape=label, resume=label)
        timed = _wants_time_headings(lecture)
        next_heading = 0.0
        for section in lecture.sections:
            if section.title:
                lines += [f"## {label(section.title)}", ""]
            for passage in section.passages:
                locator = lecture.locator_for(passage, section)
                if timed and passage.start >= next_heading:
                    lines += [f"## {_heading_stamp(locator)}", ""]
                    next_heading = _next_boundary(passage.start)
                stamp = _stamp(locator)
                lines += [f"{stamp}{_who(passage, named)} {marker(passage.text)}", ""]

        lines += _corrections(lecture.corrections)
        return "\n".join(lines).rstrip() + "\n"


def _wants_time_headings(lecture: Lecture) -> bool:
    """Whether a long transcript arrived with no chapters to structure it.

    The headings are timestamps only: the tool states where it is, and never
    invents what a stretch is about.
    """
    if len(lecture.sections) != 1 or lecture.sections[0].title is not None:
        return False
    section = lecture.sections[0]
    return section.end - section.start >= _TIME_HEADING_MIN_SPAN


def _next_boundary(moment: float) -> float:
    """The first heading boundary strictly after `moment`."""
    return (moment // _TIME_HEADING_SECONDS + 1) * _TIME_HEADING_SECONDS


def _heading_stamp(locator: Locator) -> str:
    """A timestamp heading, linked when the source supports deep links."""
    url = safe_url(locator.url)
    return f"[{locator.timestamp}]({url})" if url else locator.timestamp


def _frontmatter(lecture: Lecture) -> list[str]:
    """The YAML block that opens a note.

    Deterministic — no retrieval time — so a rerun is byte-identical and
    reports `unchanged`. Absent fields are omitted rather than left empty.
    """
    meta, provenance = lecture.meta, lecture.provenance
    fields = [("title", _scalar(meta.title)), ("source_id", _scalar(meta.source_id))]
    url = safe_url(meta.url)
    if url:
        fields.append(("url", _scalar(url)))
    if meta.channel:
        fields.append(("channel", _scalar(meta.channel)))
    if meta.published:
        # Unquoted, so notes apps read it as a date rather than a string.
        fields.append(("published", meta.published.isoformat()))
    fields += [
        ("tier", _scalar(provenance.tier.value)),
        ("language", _scalar(provenance.language)),
        ("generator", _scalar(f"{GENERATOR} {__version__}")),
    ]
    return ["---", *(f"{key}: {value}" for key, value in fields), "---", ""]


#: Characters Markdown acts on, escaped inside frontmatter values too: a viewer
#: that does not understand frontmatter renders the block as ordinary text.
_ACTIVE_IN_FRONTMATTER = frozenset("<>[]`")


def _scalar(value: str) -> str:
    """A YAML double-quoted scalar that holds `value` exactly, whatever it is.

    JSON strings are valid YAML double-quoted scalars, and `json.dumps` already
    escapes quotes, backslashes and control characters, so an uploader's title
    cannot close the string or the block. Anything YAML would not accept raw —
    a line separator, a C1 control — and anything Markdown would act on is
    escaped as well, so the block is inert even where it is not understood.
    """
    return "".join(_inert(char) for char in json.dumps(value, ensure_ascii=False))


def _inert(char: str) -> str:
    if char in _ACTIVE_IN_FRONTMATTER:
        return "\\u" + format(ord(char), "04x")
    if not char.isprintable():
        return char.encode("unicode_escape").decode("ascii")
    return char


def read_frontmatter(text: str) -> dict[str, str]:
    """The quoted string fields of a note's frontmatter, as `_frontmatter` wrote them.

    Not a YAML parser: it reads back this renderer's own output and ignores
    everything else, which is all the CLI's overwrite guard needs. An opening
    ``---`` that is never closed is not a frontmatter block.
    """
    lines = text.split("\n")
    if lines[0] != "---":
        return {}

    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return fields
        key, separator, value = line.partition(": ")
        if separator and value.startswith('"'):
            try:
                fields[key] = json.loads(value)
            except ValueError:
                continue
    return {}


def _marker(
    corrections: Sequence[Correction],
    escape: Callable[[str], str] = body,
    resume: Callable[[str], str] = body_resumed,
) -> Callable[[str], str]:
    """A function that escapes text and notes the corrections inside it.

    The correction goes *beside* the words — "quad code [Claude Code]" — so the
    transcript still says what the recording says. The brackets are the tool's
    own: source brackets are escaped, and the replacement goes through `label`.
    `escape` covers the first piece, `resume` everything after an annotation.
    """
    if not corrections:
        return escape

    # Longest first, so "Quad Code" is not matched as "Code".
    ordered = sorted(corrections, key=lambda c: len(c.wrong), reverse=True)
    # One named group per correction, so the group that matched *is* the
    # correction — no case-folding lookup that could miss.
    right: dict[str | None, str] = {f"c{n}": c.right for n, c in enumerate(ordered)}
    alternatives = "|".join(
        f"(?P<c{n}>{re.escape(c.wrong)})" for n, c in enumerate(ordered)
    )
    pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)

    def mark(text: str) -> str:
        out = []
        at = 0
        for found in pattern.finditer(text):
            piece = text[at : found.end()]
            out.append(escape(piece) if at == 0 else resume(piece))
            out.append(f" [{label(right[found.lastgroup])}]")
            at = found.end()
        tail = text[at:]
        out.append(escape(tail) if at == 0 else resume(tail))
        return "".join(out)

    return mark


def _corrections(corrections: Sequence[Correction]) -> list[str]:
    """The appendix: every correction, once, with what it rests on."""
    if not corrections:
        return []

    lines = ["## Corrections", ""]
    lines.append(
        f"{len(corrections)} spelling"
        f"{'' if len(corrections) == 1 else 's'} marked in the transcript "
        "above. The words as transcribed are unchanged; these are suggestions "
        "with what each rests on."
    )
    lines.append("")
    lines.append("| Transcribed | Probably | Times | Confidence | From |")
    lines.append("|---|---|---|---|---|")
    for correction in corrections:
        lines.append(
            f"| {label(correction.wrong)} | {label(correction.right)} "
            f"| {correction.occurrences} | {correction.confidence:.2f} "
            f"| {label(correction.evidence)} |"
        )
    lines.append("")
    return lines


def _who(passage: Passage, named: Callable[[str], str]) -> str:
    """The speaker, on the passage where they take over.

    An anonymous turn gets a dash, as printed dialogue does. Names are marked
    with the same corrections as the prose, so one person is not spelled two
    ways.
    """
    if not passage.turn:
        return ""
    if passage.speaker:
        return f" **{named(passage.speaker)}:**"
    return " —"


def _byline(meta: LectureMeta, provenance: Provenance) -> str:
    """Who, when, where to watch it — and what the text is made of.

    The trust tier is always present, so a machine's guess never reads as
    though a person wrote it down.
    """
    parts = []
    if meta.channel:
        parts.append(label(meta.channel))
    if meta.published:
        parts.append(format_date(meta.published))
    url = safe_url(meta.url)
    if url:
        parts.append(f"[watch]({url})")
    parts.append(f"{provenance.tier.prose} ({label(provenance.language)})")
    return f"*{' · '.join(parts)}*"


def _stamp(locator: Locator) -> str:
    """Bold timestamp, linked when the source supports deep links."""
    url = safe_url(locator.url)
    if url:
        return f"**[{locator.timestamp}]({url})**"
    return f"**[{locator.timestamp}]**"
