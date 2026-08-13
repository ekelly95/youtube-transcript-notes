"""Markdown study notes with a timestamp on every paragraph.

The default output, and the one the whole design is really for: readable
prose where any sentence can be traced back to the moment it was said, in one
click.
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
        # Every interpolation below is `label`d or `body`d. The title, the
        # channel, the chapter names and the transcript were all written by
        # whoever published the lecture; see `render.escape`.
        lines = [f"# {label(lecture.meta.title)}", ""]
        lines += [_byline(lecture.meta, lecture.provenance), ""]

        marker = _marker(lecture.corrections)
        # A second annotator for speaker labels, escaping with `label`: a name
        # is a single line wherever it appears, and captions get it wrong in
        # the label as often as in the prose.
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
    """A function that escapes some text and notes the corrections inside it.

    The correction goes *beside* the words rather than over them — "quad code
    [Claude Code]" — so the transcript still says what the recording says and
    stays searchable for it. A reader who disagrees with a correction can see
    exactly what they are disagreeing with.

    The brackets are the tool's own, which is the only reason they can be
    brackets at all: `escape` neutralises every one that came from the source,
    and the replacement goes through `label` on its way in, so a correction
    from a file somebody else wrote cannot open markup either.

    `escape` covers the first piece, `resume` everything after an annotation —
    prose needs the mid-line distinction (see `body_resumed`); a single-line
    speaker label passes `label` for both.
    """
    if not corrections:
        return escape

    # Longest first, so "Quad Code" is not matched as "Code" by a shorter
    # entry that happens to sit inside it.
    ordered = sorted(corrections, key=lambda c: len(c.wrong), reverse=True)
    # One capture group per correction, rather than one group and a lookup
    # from the matched text: case-insensitive matching and `casefold` are not
    # quite the same function, so a lookup needs a "what if it is missing"
    # branch that cannot be reached or tested. `lastgroup` needs no such
    # branch, because the group that matched *is* the correction.
    #
    # Keyed by `str | None` because that is `lastgroup`'s declared type. Every
    # alternative in the pattern below is a named group, so a match reaching the
    # lookup always names one — writing the key type honestly costs nothing,
    # where a `or ""` guard would add a branch no test could reach.
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
            # Pieces after the first resume mid-line, where a leading `--` is
            # punctuation and not a heading underline — see `body_resumed`.
            out.append(escape(piece) if at == 0 else resume(piece))
            out.append(f" [{label(right[found.lastgroup])}]")
            at = found.end()
        tail = text[at:]
        out.append(escape(tail) if at == 0 else resume(tail))
        return "".join(out)

    return mark


def _corrections(corrections: Sequence[Correction]) -> list[str]:
    """The appendix: every correction, once, with what it rests on.

    Present because the inline marks answer "what should this say" and not
    "how much of this document has been second-guessed", which is the question
    a reader deciding whether to quote the thing actually has.
    """
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
    """The speaker, when this passage is where they take over.

    Only on the passage that opens a turn: a long answer runs to several
    paragraphs, and repeating the name on each of them reads like a new person
    interrupting every forty seconds. An anonymous turn gets the dash that
    printed dialogue has always used, because `>>` asserts that the speaker
    changed and nothing whatever about who they are.

    `named` is the correction marker for labels, so a name the recogniser got
    wrong is annotated here exactly as it is in the prose — a note corrected
    in one place and wrong in the other reads as two different people. It only
    fires for names the correction scan actually found, which means a name
    appearing *solely* as a label stays as published: corrections are proposed
    from passage text, and inventing one for a string no passage contains is
    the kind of guess this pipeline refuses.
    """
    if not passage.turn:
        return ""
    if passage.speaker:
        return f" **{named(passage.speaker)}:**"
    return " —"


def _byline(meta: LectureMeta, provenance: Provenance) -> str:
    """Attribution line: who, when, where to watch it — and what the text is.

    The trust tier rides here because the note is where a reader decides
    whether to quote, and a transcript that never says it was a machine's
    guess reads as though a person wrote it down. Always present, so the
    byline never collapses to nothing: a local caption file with no channel,
    date or URL still states what its text is made of.
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
    """Bold timestamp, linked when the source supports deep links.

    The link is checked, not just the byline's. `Locator` builds every deep
    link on `meta.url`, so an unusable source URL would otherwise appear once
    per passage instead of once per document.
    """
    url = safe_url(locator.url)
    if url:
        return f"**[{locator.timestamp}]({url})**"
    return f"**[{locator.timestamp}]**"
