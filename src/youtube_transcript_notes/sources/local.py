"""Caption files already on disk.

Useful in its own right — for lectures downloaded elsewhere, exported from a
course platform, or transcribed with a separate tool — and useful as the
second implementation of `SourceProvider`, which is what stops the abstraction
being shaped entirely around YouTube.

**Filename convention.** Everything between the stem and the extension is read
as metadata::

    6006-lec1.en.json3            English, assumed human-written
    6006-lec1.auto.en.json3       English, platform auto-captions
    6006-lec1.whisper.en.vtt      English, locally transcribed
    lecture.vtt                   language unknown, matches any request

Tracks default to `MANUAL` when unmarked, because that is what a file someone
put on disk deliberately usually is. Mark auto-generated captions with
``.auto.`` — the tier decides whether rolling-window deduplication runs, and
an auto-captioned WebVTT track mistaken for a human-written one will keep its
repeated text.

**Folders.** One lecture per stem — the name up to the first dot. Files sharing
it are tracks of one lecture, different stems are different lectures, and
`expand` turns a folder into one source per lecture before `list` is ever
called. Not recursive, and files the tool does not read form no group, so a
`notes.md` beside the captions is ignored rather than becoming a lecture with no
tracks. A lecture inside a folder can be named by its stem — ``6.006/week-03``
— which is also how expansion addresses them.

A part the tool does not recognise is **skipped, not guessed at**, and the first
recognised one wins. Both halves of that were once wrong: any two or three
letters counted as a language, so `lecture.raw.vtt` was a lecture in the `raw`
language; and the last match won, so `lecture.en.raw.vtt` was too — a correct
label broken by a word appended after it. See `resolve.LANGUAGE_CODES` for what
counts as a language and why the table is not simply ISO 639-3.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..errors import (
    InputUnreadable,
    LectureUnavailable,
    MalformedCaptions,
    NoCaptionsAvailable,
    SeveralLectures,
)
from ..limits import read_capped
from ..models import LectureMeta, TrustTier
from ..parse import parsers
from ..resolve import (
    UNKNOWN_LANGUAGE,
    Track,
    TrackHandle,
    TrackManifest,
    looks_like_language,
    primary_subtag,
)
from .base import Expansion, SourceProvider, providers

__all__ = ["LocalProvider"]

#: Filename markers naming a track's trust tier.
_TIER_MARKERS = {
    "manual": TrustTier.MANUAL,
    "human": TrustTier.MANUAL,
    "auto": TrustTier.ASR_PLATFORM,
    "asr": TrustTier.ASR_PLATFORM,
    "whisper": TrustTier.ASR_LOCAL,
    "transcribed": TrustTier.ASR_LOCAL,
    "translated": TrustTier.TRANSLATED,
}


@providers.register("local")
class LocalProvider(SourceProvider):
    """Reads caption files from a path or a directory of them."""

    name = "local"

    @classmethod
    def handles(cls, source: str) -> bool:
        try:
            path = Path(source)
            return path.exists() or _names_a_stem(path)
        except OSError:  # pragma: no cover - malformed paths vary by platform
            return False

    def expand(self, source: str) -> Expansion:
        """Turn a folder into the lectures in it. Anything else comes back alone.

        A folder is the local playlist. `list` answers for one lecture and a
        folder of a course is N, so the fan-out happens here, before `list` is
        ever called — and each lecture then goes through `cli.run`'s existing
        loop with the failure isolation, exit codes and `--out` naming a
        playlist's videos already get. One `iterdir` and no file opened, which
        is the same costs-nothing rule `list` keeps.

        Each lecture is addressed by its stem — ``lectures/week-03`` — because
        that is the one address naming a group of files without being one of
        them. Naming a file explicitly still means that file and nothing beside
        it.
        """
        path = Path(source)
        if not path.is_dir():
            return Expansion(sources=(source,))

        groups = _by_stem(sorted(p for p in path.iterdir() if p.is_file()))
        if not groups:
            # Refused rather than expanded to nothing, on `PlaylistEmpty`'s
            # reasoning: a run that processes zero lectures, finds zero
            # failures and prints nothing exits 0 looking like a success.
            raise NoCaptionsAvailable(source=source)

        return Expansion(
            sources=tuple(str(path / stem) for stem in groups), origin=source
        )

    def list(self, source: str) -> TrackManifest:
        path = Path(source)
        files = _lecture_files(path)
        if files is None:
            raise LectureUnavailable(source=source)

        groups = _by_stem(files)
        if not groups:
            raise NoCaptionsAvailable(source=source)
        if len(groups) > 1:
            raise SeveralLectures(source=source, lectures=list(groups))

        ((stem, members),) = groups.items()
        captions = [(f, d) for f in members if (d := _describe(f)) is not None]
        meta = LectureMeta(source_id=stem, title=stem)

        tracks = [
            TrackHandle(
                track=Track(
                    language=primary_subtag(language),
                    raw_language=language,
                    tier=tier,
                    caption_format=fmt,
                    label=file.name,
                ),
                meta=meta,
                provider=self,
                ref=file,
            )
            for file, (tier, language, fmt) in captions
        ]

        return TrackManifest(meta=meta, tracks=tuple(tracks))

    def load(self, ref: Any) -> str:
        """Read one caption file.

        ``utf-8-sig`` rather than ``utf-8``: several caption tools write a byte
        order mark, and a leading ``﻿`` turns ``WEBVTT`` into a word the
        parser does not recognise while looking identical in any editor. The
        codec is plain UTF-8 when there is no mark to strip, so this costs
        nothing for the files that never had one.
        """
        path = Path(ref)
        try:
            return read_capped(path)
        except OSError as error:
            # `list` established that this file existed; `load` is a later
            # moment, and a folder being synced, tidied or written to can move
            # or lock the file in between. Without this the failure escapes as a
            # bare `FileNotFoundError`, reaches the CLI's last resort, and is
            # reported as "retry — transient network and rate-limit failures are
            # common" — advice about a network this source never touches, for a
            # problem no retry fixes. `InputUnreadable` says to check the path,
            # which is the only thing that will help.
            raise InputUnreadable(
                source=path.name, detail=error.strerror or str(error)
            ) from error
        except UnicodeDecodeError as error:
            # Without this the failure escapes as a bare `UnicodeDecodeError`,
            # which the CLI reports as an unclassified acquisition failure —
            # true, and no help at all to someone holding a caption file their
            # editor saved as Latin-1.
            raise MalformedCaptions(
                source=path.name,
                fmt=path.suffix.lstrip("."),
                detail=(
                    f"the file is not valid UTF-8 ({error.reason}, at byte "
                    f"{error.start}). Re-save it as UTF-8."
                ),
            ) from error


def _describe(path: Path) -> tuple[TrustTier, str, str] | None:
    """Read tier, language and format out of a filename, or None if not a caption."""
    parts = path.name.split(".")
    if len(parts) < 2:
        return None

    fmt = parts[-1].lower()
    if fmt not in parsers:
        return None

    tier, language = _read_markers(parts[1:-1])
    return tier, language, fmt


def _read_markers(parts: list[str]) -> tuple[TrustTier, str]:
    """Tier and language from the dotted middle of a filename. First wins.

    First rather than last, because the convention this module documents reads
    left to right — ``6006-lec1.auto.en.json3`` — and a later coincidence must
    not overrule what the name already said. Last-match-wins is what made
    ``lecture.en.raw.vtt`` resolve to `raw`: a file labelled correctly, broken
    by a word appended after the label.

    A part that means nothing to the tool is skipped rather than guessed at.
    People put dates, part numbers and initials in filenames, and an unlabelled
    track is not a silent failure — it lists as `und`, which matches whatever
    language is asked for.
    """
    tier: TrustTier | None = None
    language: str | None = None

    for part in parts:
        marker = _TIER_MARKERS.get(part.lower())
        if marker is not None:
            if tier is None:
                tier = marker
        elif language is None and looks_like_language(part):
            language = part

    return tier or TrustTier.MANUAL, language or UNKNOWN_LANGUAGE


def _is_lecture_file(path: Path) -> bool:
    return _describe(path) is not None


def _stem_siblings(path: Path) -> list[Path]:
    """Files belonging to the lecture ``path`` names by stem, e.g. ``6.006/week-03``."""
    prefix = f"{path.name}."
    try:
        return sorted(
            child
            for child in path.parent.iterdir()
            if child.name.startswith(prefix) and child.is_file()
        )
    except OSError:
        return []


def _names_a_stem(path: Path) -> bool:
    """Whether ``path`` addresses a lecture by stem rather than by filename.

    Deliberately narrow: a sibling counts only if it is a caption file
    the tool would actually read, so an unrelated ``week-03.txt`` cannot capture
    the name. This widens the rule that an existing path beats a video id —
    typing a bare id while standing in a folder holding its captions now reads
    the local file, which is almost certainly a download of that very lecture.
    """
    return any(_is_lecture_file(child) for child in _stem_siblings(path))


def _lecture_files(path: Path) -> list[Path] | None:
    """The files this source names, or None when it names nothing at all.

    None and ``[]`` are different answers on purpose: a path matching nothing is
    `LectureUnavailable`, while a folder holding nothing readable is
    `NoCaptionsAvailable`. Telling somebody a folder they are looking at does
    not exist is the wrong diagnosis.
    """
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.is_file())
    return _stem_siblings(path) or None


def _by_stem(files: Sequence[Path]) -> dict[str, list[Path]]:
    """Group caption files by the lecture they belong to.

    The lecture is everything before the first dot, which is this module's
    documented filename convention: the stem names the lecture and every dotted
    part after it describes one of its tracks. So ``lec1.en.vtt`` and
    ``lec1.auto.en.json3`` are two tracks of one lecture, while
    ``week-03.en.vtt`` and ``week-04.en.vtt`` are two lectures — the whole
    difference between rendering a course and rendering the alphabetically
    first file in it, which is what a folder used to do.

    Files the tool does not recognise form no group, so a ``notes.md`` beside the
    captions is ignored rather than becoming a lecture with no tracks. Insertion
    order over sorted input, so a course renders in filename order and two runs
    agree.
    """
    groups: dict[str, list[Path]] = {}
    for file in files:
        if _is_lecture_file(file):
            groups.setdefault(file.name.split(".")[0], []).append(file)
    return groups
