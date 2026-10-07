"""Caption files already on disk.

For captions downloaded elsewhere, exported from a course platform, or made by
a separate transcription tool.

**Filename convention.** Everything between the stem and the extension is
metadata::

    6006-lec1.en.json3            English, assumed human-written
    6006-lec1.auto.en.json3       English, platform auto-captions
    6006-lec1.whisper.en.vtt      English, locally transcribed
    lecture.vtt                   language unknown, matches any request

Unmarked tracks are `MANUAL`. Mark automatic captions with ``.auto.``: the
tier decides whether rolling-window deduplication runs.

**Folders.** One lecture per stem (the name up to the first dot); files
sharing it are tracks of one lecture. Not recursive, and unreadable files form
no group. A lecture in a folder can be named by its stem, e.g.
``6.006/week-03``.

Unrecognised parts are skipped, and the first recognised one wins, so
``lecture.en.raw.vtt`` is English. See `resolve.LANGUAGE_CODES`.
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
        """Turn a folder into the lectures in it, addressed by stem.

        One `iterdir`, no file opened. Anything that is not a folder comes back
        alone.
        """
        path = Path(source)
        if not path.is_dir():
            return Expansion(sources=(source,))

        groups = _by_stem(sorted(p for p in path.iterdir() if p.is_file()))
        if not groups:
            # Refused rather than expanded to nothing, which would exit 0.
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
        """Read one caption file (BOM-tolerant, size-capped)."""
        path = Path(ref)
        try:
            return read_capped(path)
        except OSError as error:
            # The file may have moved since `list`; say so, not "retry".
            raise InputUnreadable(
                source=path.name, detail=error.strerror or str(error)
            ) from error
        except UnicodeDecodeError as error:
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

    Unrecognised parts (dates, initials) are skipped; an unlabelled track lists
    as `und`, which matches any requested language.
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

    Only caption files count, so an unrelated ``week-03.txt`` cannot capture
    the name.
    """
    return any(_is_lecture_file(child) for child in _stem_siblings(path))


def _lecture_files(path: Path) -> list[Path] | None:
    """The files this source names, or None when it names nothing at all.

    None is `LectureUnavailable`; ``[]`` (a folder with nothing readable) is
    `NoCaptionsAvailable`.
    """
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.is_file())
    return _stem_siblings(path) or None


def _by_stem(files: Sequence[Path]) -> dict[str, list[Path]]:
    """Group caption files by stem, in sorted filename order.

    ``lec1.en.vtt`` and ``lec1.auto.en.json3`` are one lecture;
    ``week-03.en.vtt`` and ``week-04.en.vtt`` are two. Unreadable files form no
    group.
    """
    groups: dict[str, list[Path]] = {}
    for file in files:
        if _is_lecture_file(file):
            groups.setdefault(file.name.split(".")[0], []).append(file)
    return groups
