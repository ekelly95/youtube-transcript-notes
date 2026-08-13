"""Fitting passages to the chapters the lecturer declared.

Chapters and sections are kept as separate types on purpose. A `Chapter` is an
assertion by the source about where topics begin; a `Section` is the result of
fitting actual passages to it. Keeping them apart means a chapter list that is
wrong, empty, or starts halfway through cannot cause text to disappear —
worst case the headings are unhelpful, which is recoverable, rather than the
transcript being incomplete, which is not.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Chapter, Passage, Section

__all__ = ["build_sections"]


def build_sections(
    passages: Sequence[Passage], chapters: Sequence[Chapter] = ()
) -> tuple[Section, ...]:
    """Group passages under chapter headings, or into one untitled section."""
    if not passages:
        return ()

    if not chapters:
        return (Section(title=None, start=passages[0].start, passages=tuple(passages)),)

    ordered = sorted(chapters, key=lambda chapter: chapter.start)
    sections: list[Section] = []
    current: list[Passage] = []
    current_chapter: Chapter | None = None

    for passage in passages:
        chapter = _chapter_at(ordered, passage.start)
        if current and chapter is not current_chapter:
            sections.append(_section(current_chapter, current))
            current = []
        current_chapter = chapter
        current.append(passage)

    # `current` always holds at least the final passage: the empty case
    # returned above, and every iteration appends.
    sections.append(_section(current_chapter, current))
    return tuple(sections)


def _chapter_at(chapters: Sequence[Chapter], moment: float) -> Chapter | None:
    """The last chapter to have started by ``moment``.

    None when the passage precedes every chapter, which happens when a source
    starts its chapter list after an unlabelled introduction. That becomes an
    untitled leading section rather than being forced under the wrong heading.
    """
    found = None
    for chapter in chapters:
        if chapter.start <= moment:
            found = chapter
        else:
            break
    return found


def _section(chapter: Chapter | None, passages: list[Passage]) -> Section:
    return Section(
        title=chapter.title if chapter else None,
        start=chapter.start if chapter else passages[0].start,
        passages=tuple(passages),
    )
