"""Markdown notes with a timestamp on every paragraph — the default output.

Any sentence can be traced back to the moment it was said, in one click.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

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

__all__ = ["MarkdownRenderer"]


@renderers.register("markdown", "md")
class MarkdownRenderer(Renderer):
    """Headed, bylined, deep-linked notes."""

    extension = "md"

    def render(self, lecture: Lecture) -> str:
        # Every interpolation is `label`d or `body`d: it is the uploader's text.
        lines = [f"# {label(lecture.meta.title)}", ""]
        lines += [_byline(lecture.meta, lecture.provenance), ""]

        marker = _marker(lecture.corrections)
        # Speaker labels are single-line, so they escape with `label`.
        named = _marker(lecture.corrections, escape=label, resume=label)
        for section in lecture.sections:
            if section.title:
                lines += [f"## {label(section.title)}", ""]
            for passage in section.passages:
                stamp = _stamp(lecture.locator_for(passage, section))
                lines += [f"{stamp}{_who(passage, named)} {marker(passage.text)}", ""]

        lines += _corrections(lecture.corrections)
        return "\n".join(lines).rstrip() + "\n"


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
