"""The command line.

`_outcomes` works each source out, one at a time, without touching anything.
`run` collects them all and reports through `_present`, so every test is an
assertion about a returned value. `main` consumes the same outcomes but writes
each file as soon as its source is done, then reports once, after every
write, so the report never claims a file that failed.

A batch survives its own failures: each source is processed independently,
and the report covers both what worked and what did not. Documents go to
stdout and failures to stderr, so redirecting output cannot bury an error in
someone's notes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from . import __version__
from .api import TranscriptFetcher
from .atomic import atomic_write
from .cache import Cache, NullCache, RefreshCache
from .errors import (
    AcquisitionFailed,
    InputUnreadable,
    MalformedCorrections,
    OutputExists,
    OutputUnwritable,
    TranscriptError,
    UnknownProvider,
)
from .limits import MAX_CORRECTIONS, MAX_GLOSSARY_BYTES, read_capped
from .models import Lecture, TrustTier
from .naming import filename_for
from .redact import redact
from .refine import Glossary, read_corrections, read_glossary
from .render import Renderer, get_renderer, renderers
from .render.markdown import GENERATOR, read_frontmatter
from .resolve import TrackManifest

__all__ = ["CliResult", "OutputFile", "main", "run"]

#: Everything succeeded.
EXIT_OK = 0
#: At least one source failed, but the run continued and reported the rest.
EXIT_PARTIAL = 1
#: Nothing could be produced at all.
EXIT_FAILED = 2

#: Seconds between remote sources unless `--delay` says otherwise. A playlist
#: fetched back to back is what YouTube's bot check is for.
DEFAULT_DELAY = 1.0

#: How the run waits between remote sources; the test suite replaces it.
_pause = time.sleep

#: What a write did: created or replaced (with --force), left identical, or
#: replaced this tool's own earlier note for the same video.
WROTE, UNCHANGED, UPDATED = "wrote", "unchanged", "updated"

#: How much of a file in the way is read to find its frontmatter.
_FRONTMATTER_PROBE = 4096


@dataclass(frozen=True)
class OutputFile:
    """A document `run` decided to write and `main` actually writes."""

    path: Path
    text: str

    overwrite: bool = False
    """Whether this may replace any file already at `path`. False unless
    ``--force``, so a hand-built `OutputFile` is the safe one."""

    source_id: str | None = None
    """The video this note is for, when that identity is global.

    A note this tool wrote earlier for the same id may be replaced without
    ``--force``. Set only for sources with a URL: a YouTube id names one video
    everywhere, while a local file's stem can recur in another folder.
    """


@dataclass(frozen=True)
class CliResult:
    text: str
    """What belongs on stdout — documents, a listing, or what was written."""

    exit_code: int

    files: tuple[OutputFile, ...] = ()
    """Documents for `main` to write. Empty unless ``--out`` was given."""

    report: str = ""
    """What belongs on stderr: failures and notices."""


@dataclass(frozen=True)
class _Document:
    """One lecture and the text it rendered to.

    Rendered inside `_outcomes`' per-source loop, so a renderer that raises
    costs that one source.
    """

    lecture: Lecture
    text: str


@dataclass(frozen=True)
class _Decision:
    """Everything one run worked out, held as data for `main` and `_present`."""

    renderer: Renderer
    as_json: bool
    listings: tuple[str, ...] = ()
    documents: tuple[_Document, ...] = ()
    files: tuple[OutputFile, ...] = ()
    failures: tuple[tuple[str, TranscriptError], ...] = ()
    notices: tuple[tuple[str, TranscriptError], ...] = ()


@dataclass(frozen=True)
class _Outcome:
    """What became of one source. Exactly one field besides `source` is set."""

    source: str
    document: _Document | None = None
    listing: str | None = None
    failure: TranscriptError | None = None
    notice: TranscriptError | None = None


#: Told the position, the total and the source, before each source is worked on.
_Announce = Callable[[int, int, str], None]


def run(argv: Sequence[str] | None = None) -> CliResult:
    """Decide the whole run and report it, writing nothing."""
    return _present(_decide(argv))


def _decide(argv: Sequence[str] | None) -> _Decision:
    """Work out the whole run without touching anything."""
    args = _parse(argv)
    renderer = get_renderer(args.format, **_options(args))
    planner = _Planner(args.out, renderer.extension, args.force)
    tally = _Tally()
    for outcome in _outcomes(args, renderer):
        tally.add(outcome, planner.plan(outcome.document))
    return tally.decision(renderer, args.json)


def _outcomes(
    args: argparse.Namespace,
    renderer: Renderer,
    announce: _Announce | None = None,
) -> Iterator[_Outcome]:
    """Every source's outcome, one at a time, so `main` can act on each at once."""
    fetcher = TranscriptFetcher(cache=_cache(args))

    try:
        glossary = _glossary(args)
    except TranscriptError as error:
        # Wrong for the whole run, so reported once and not charged to a source.
        yield _Outcome("", failure=error)
        return

    # Playlists and folders become their items first, so each item is isolated
    # exactly like a source typed by hand.
    sources: list[str] = []
    for source in args.sources:
        try:
            expansion = fetcher.expand(source)
        except TranscriptError as error:
            yield _Outcome(source, failure=error)
            continue
        except Exception as error:
            yield _Outcome(source, failure=_wrap(source, error))
            continue
        if expansion.stale_reason is not None:
            yield _Outcome(source, notice=expansion.stale_reason)
        sources.extend(expansion.sources)

    paced = False
    for position, source in enumerate(sources, start=1):
        if announce is not None:
            announce(position, len(sources), source)
        try:
            if _is_remote(fetcher, source):
                # Between remote sources, never before the first.
                if paced and args.delay:
                    _pause(args.delay)
                paced = True
            # Step by step rather than `TranscriptFetcher.fetch`, because the
            # manifest says whether this run was served from cache.
            manifest = fetcher.list(source)
            if manifest.stale_reason is not None:
                yield _Outcome(source, notice=manifest.stale_reason)

            if args.list:
                yield _Outcome(source, listing=_describe(manifest))
            else:
                lecture = manifest.find(args.languages, _tiers(args)).fetch(
                    glossary=glossary
                )
                # Rendered here, so a renderer that raises costs this source.
                yield _Outcome(
                    source, document=_Document(lecture, renderer.render(lecture))
                )
        except TranscriptError as error:
            yield _Outcome(source, failure=error)
        except Exception as error:
            # Anything unclassified still costs one source, not the batch.
            yield _Outcome(source, failure=_wrap(source, error))


class _Planner:
    """Names `--out` files one at a time, so no two in a run collide."""

    def __init__(self, out: str | None, extension: str, overwrite: bool) -> None:
        self._directory = Path(out) if out is not None else None
        self._extension = extension
        self._overwrite = overwrite
        self._taken: set[str] = set()

    def plan(self, document: _Document | None) -> OutputFile | None:
        """The file this document goes to, or None when nothing is filed."""
        if self._directory is None or document is None:
            return None
        meta = document.lecture.meta
        name = filename_for(meta.title, meta.source_id, self._extension, self._taken)
        self._taken.add(name.rsplit(".", 1)[0].casefold())
        return OutputFile(
            self._directory / name,
            document.text,
            self._overwrite,
            source_id=meta.source_id if meta.url else None,
        )


class _Tally:
    """Collects outcomes into a `_Decision`."""

    def __init__(self) -> None:
        self.listings: list[str] = []
        self.documents: list[_Document] = []
        self.files: list[OutputFile] = []
        self.failures: list[tuple[str, TranscriptError]] = []
        self.notices: list[tuple[str, TranscriptError]] = []

    def add(self, outcome: _Outcome, output: OutputFile | None = None) -> None:
        """Record one outcome, and the file planned for it if there is one.

        A filed document is kept only as its file: its text is on its way to
        disk, and a long playlist should not hold every lecture in memory.
        """
        if outcome.failure is not None:
            self.failures.append((outcome.source, outcome.failure))
        elif outcome.notice is not None:
            self.notices.append((outcome.source, outcome.notice))
        elif outcome.listing is not None:
            self.listings.append(outcome.listing)
        elif output is not None:
            self.files.append(output)
        else:
            assert outcome.document is not None  # one field is always set
            self.documents.append(outcome.document)

    def decision(self, renderer: Renderer, as_json: bool) -> _Decision:
        return _Decision(
            renderer=renderer,
            as_json=as_json,
            listings=tuple(self.listings),
            documents=tuple(self.documents),
            files=tuple(self.files),
            failures=tuple(self.failures),
            notices=tuple(self.notices),
        )


def _cache(args: argparse.Namespace) -> Cache:
    """The cache this run reads and writes, as the flags ask."""
    if args.no_cache:
        return NullCache()
    if args.refresh:
        return RefreshCache(args.cache)
    return Cache(args.cache)


def _is_remote(fetcher: TranscriptFetcher, source: str) -> bool:
    """Whether fetching `source` reaches the network, and so should be paced."""
    try:
        return fetcher.provider_for(source).remote
    except UnknownProvider:
        # `list` reports it, once, like every other per-source failure.
        return False


def _present(
    decision: _Decision,
    problems: Sequence[tuple[str, TranscriptError]] = (),
    states: Mapping[str, str] | None = None,
) -> CliResult:
    """Turn a decision, plus what became of its files, into what to say.

    Planned files decide the mode (a `--out` run reports filenames); written
    files decide the content, so a run whose writes all failed prints nothing.
    `states` maps a path to what its write did, when that was not ``wrote``.
    """
    refused = {source for source, _ in problems}
    written = tuple(f for f in decision.files if str(f.path) not in refused)
    trouble = (*decision.failures, *problems)
    produced = decision.listings or (written if decision.files else decision.documents)
    code = _exit_code(produced, trouble)

    if decision.as_json:
        # One self-contained document, write failures included.
        return CliResult(
            text=_envelope(decision, written, trouble),
            exit_code=code,
            files=decision.files,
        )

    return CliResult(
        text=_stdout(decision, written, states or {}),
        exit_code=code,
        files=decision.files,
        report=_report(trouble, decision.notices),
    )


def _glossary(args: argparse.Namespace) -> Glossary | None:
    """The caller's spellings, from `--glossary` and `--corrections`, read once."""
    parts = []
    if args.glossary is not None:
        path = Path(args.glossary)
        parts.append(read_glossary(_read_reference(path), path.name))
    if args.corrections is not None:
        parts.append(_corrections_file(Path(args.corrections)))

    if not parts:
        return None
    merged = parts[0]
    for part in parts[1:]:
        # Later wins: this run's corrections beat the standing glossary.
        merged = part.merged_with(merged)
    return merged


def _read_reference(path: Path) -> str:
    """A caller-supplied text file, or a failure that names the file."""
    try:
        return read_capped(path, MAX_GLOSSARY_BYTES)
    except OSError as error:
        raise InputUnreadable(
            source=path.name, detail=error.strerror or str(error)
        ) from error
    except UnicodeDecodeError as error:
        raise InputUnreadable(
            source=path.name, detail=f"not UTF-8 text ({error.reason})"
        ) from error


def _corrections_file(path: Path) -> Glossary:
    text = _read_reference(path)
    try:
        records = json.loads(text)
    except json.JSONDecodeError as error:
        raise MalformedCorrections(source=path.name, detail=str(error)) from error

    if not isinstance(records, list):
        raise MalformedCorrections(
            source=path.name, detail=f"expected a list, found {type(records).__name__}"
        )
    if len(records) > MAX_CORRECTIONS:
        raise MalformedCorrections(
            source=path.name,
            detail=f"{len(records)} corrections, more than the {MAX_CORRECTIONS} limit",
        )
    return read_corrections(records, path.name)


def _options(args: argparse.Namespace) -> dict[str, object]:
    """Renderer options from the command line, omitted when not given."""
    return {} if args.budget is None else {"budget": args.budget}


def _stdout(
    decision: _Decision,
    written: Sequence[OutputFile],
    states: Mapping[str, str],
) -> str:
    if decision.listings:
        return "\n\n\n".join(decision.listings)
    if decision.files:
        return "\n".join(
            f"{states.get(str(output.path), WROTE)} {output.path}" for output in written
        )
    # The renderer's own separator: a blank line between JSONL documents is
    # not JSONL.
    return decision.renderer.separator.join(d.text for d in decision.documents)


#: Languages a listing names before summarising the rest.
MAX_LISTED_LANGUAGES = 20


def _describe(manifest: TrackManifest) -> str:
    lines = [f"{manifest.meta.title}  [{manifest.meta.source_id}]"]
    if manifest.meta.channel:
        lines.append(f"  {manifest.meta.channel}")
    lines.append(f"  {len(manifest)} track(s), languages: {_languages(manifest)}")
    lines.append("")
    lines.extend(f"  - {line}" for line in manifest.describe_tracks())
    return "\n".join(lines)


def _languages(manifest: TrackManifest) -> str:
    """Languages on offer, capped — but never capped silently."""
    languages = manifest.languages()
    shown = ", ".join(languages[:MAX_LISTED_LANGUAGES])
    dropped = len(languages) - MAX_LISTED_LANGUAGES
    return shown if dropped <= 0 else f"{shown}, ... and {dropped} more"


def _tiers(args: argparse.Namespace) -> list[TrustTier] | None:
    if not args.tiers:
        return None
    return [TrustTier(name) for name in args.tiers]


def _failures(failures: Sequence[tuple[str, TranscriptError]]) -> str:
    return "\n\n\n".join(f"{source}:\n{error}" for source, error in failures)


def _report(
    failures: Sequence[tuple[str, TranscriptError]],
    notices: Sequence[tuple[str, TranscriptError]],
) -> str:
    """Everything for stderr: what failed, and what was served from cache.

    A notice does not affect the exit code — the document is real — but the
    reader needs to know the transport is broken.
    """
    parts = []
    if notices:
        parts.append(
            "\n\n\n".join(
                f"{source}: served from cache — the source could not be "
                f"reached.\n{error}"
                for source, error in notices
            )
        )
    if failures:
        parts.append(_failures(failures))
    return "\n\n\n".join(parts)


def _envelope(
    decision: _Decision,
    written: Sequence[OutputFile],
    trouble: Sequence[tuple[str, TranscriptError]],
) -> str:
    """The machine-readable form: one JSON object covering the whole run.

    With ``--out``, `results` is empty and `files` lists what is actually on
    disk. A write failure is keyed by its path. `warnings` (stale-cache
    notices) do not clear `ok`, so an agent will not retry what it already has.
    """
    payload = {
        "ok": not trouble,
        "results": list(decision.listings)
        or ([] if decision.files else [d.text for d in decision.documents]),
        "files": [str(output.path) for output in written],
        "errors": [
            {"source": source, "message": error.cause, **error.remedy}
            for source, error in trouble
        ],
        "warnings": [
            {"source": source, "message": error.cause, **error.remedy}
            for source, error in decision.notices
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _exit_code(produced: Sequence[object], failures: Sequence[object]) -> int:
    if not failures:
        return EXIT_OK
    return EXIT_PARTIAL if produced else EXIT_FAILED


def _wrap(source: str, error: Exception) -> TranscriptError:
    """The last resort for an exception nothing recognised — redacted, because
    nobody has examined what it might carry."""
    detail = redact(f"{type(error).__name__}: {error}")
    return AcquisitionFailed(source=redact(source), detail=detail)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="youtube-transcript-notes",
        description=(
            "Turn captioned videos into readable, citable notes. "
            "Accepts YouTube URLs, video IDs and playlist URLs, and paths to "
            "caption files."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help=(
            "YouTube URLs or video IDs, or paths to caption files or folders. "
            "A playlist URL is expanded into its videos, each processed as if "
            "it had been passed by hand."
        ),
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        default=["en"],
        metavar="LANG",
        help=(
            "Language codes in descending preference. The first with any "
            "usable track wins, so --languages de en never returns English "
            "when a German transcript exists. Defaults to en."
        ),
    )
    parser.add_argument(
        "--format",
        default="markdown",
        # From the registry, so a new renderer is usable once registered.
        choices=sorted(renderers.keys()),
        help="Output format. Defaults to markdown.",
    )
    parser.add_argument(
        "--tiers",
        nargs="+",
        default=None,
        choices=[tier.value for tier in TrustTier],
        help=(
            "Restrict and reorder acceptable transcript sources. The default "
            "order is manual (human-written), unmarked (a local file that "
            "does not say), asr_platform (automatic), asr_local (transcribed "
            "by a separate tool), then translated."
        ),
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=None,
        metavar="TOKENS",
        help=(
            "Approximate token budget, for formats that ration their output. "
            "Metadata and the outline are spent first, then as much "
            "transcript as fits; whatever does not fit is named rather than "
            "quietly dropped."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help=(
            "Write one file per video into DIR instead of printing the "
            "documents. Each is named from the video title and id, with the "
            "format's own extension. The directory is created if needed. A "
            "note this tool wrote for the same video is updated; anything "
            "else at that name is left alone unless --force."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Replace any file that is already there. Without it, a note this "
            "tool wrote for the same YouTube video is updated and an identical "
            "file is left alone, but anything else at that name — a note you "
            "wrote, another video's — is refused and nothing is overwritten."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show what transcripts exist without downloading any of them.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        metavar="SECONDS",
        help=(
            "Pause between YouTube videos in one run, so a playlist does not "
            f"trip YouTube's bot check. Defaults to {DEFAULT_DELAY:g}; 0 turns "
            "it off. Local files are never delayed."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON envelope, with machine-readable remedies for errors.",
    )
    parser.add_argument(
        "--cache",
        default=None,
        metavar="DIR",
        help=(
            "Where to cache fetched captions. Defaults to a per-user cache "
            "directory outside the working directory; set "
            "YOUTUBE_TRANSCRIPT_NOTES_CACHE to move it without passing this "
            "every time."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Fetch everything fresh and store nothing.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Ignore cached captions and refetch them, storing the fresh copies. "
            "For captions corrected upstream since they were cached."
        ),
    )
    parser.add_argument(
        "--glossary",
        default=None,
        metavar="FILE",
        help=(
            "Names to watch for, one per line. A bare term catches near "
            "misspellings of it; 'Claude Code: quad code, Squad code' also "
            "corrects those exact forms, which is how errors too far from the "
            "spelling to guess at get caught. Grows with use."
        ),
    )
    parser.add_argument(
        "--corrections",
        default=None,
        metavar="FILE",
        help=(
            "A JSON list of {wrong, right} objects, as a model reading the "
            "transcript against its title and chapters would produce. Applied "
            "to every source in the run, and shown beside the original words "
            "rather than replacing them."
        ),
    )
    args = parser.parse_args(argv)
    if args.out is not None and args.list:
        parser.error("--out writes notes; --list only reports what exists")
    if args.force and args.out is None:
        parser.error("--force applies to --out, which is what writes files")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    if args.refresh and args.no_cache:
        parser.error("--refresh stores what it fetches; --no-cache stores nothing")
    if args.budget is not None and not renderers.get(args.format).takes_budget:
        # Here, not at construction, where a `TypeError` would be misreported
        # as a failed lecture.
        parser.error(f"--budget applies only to --format {_budgeted_formats()}")
    return args


def _budgeted_formats() -> str:
    """The formats `--budget` is good for, taken from the registry."""
    return ", ".join(
        name for name in renderers.names() if renderers.get(name).takes_budget
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point, and the only place in the package that touches the world.

    Each file is written as soon as its source is done, so an interrupted
    playlist keeps everything it finished. The report comes after every write,
    so it never claims a file that failed.
    """
    _speak_utf8()
    args = _parse(argv)
    renderer = get_renderer(args.format, **_options(args))
    planner = _Planner(args.out, renderer.extension, args.force)
    tally = _Tally()
    problems: list[tuple[str, TranscriptError]] = []
    states: dict[str, str] = {}
    interrupted = False

    try:
        for outcome in _outcomes(args, renderer, announce=_announcer(args)):
            output = planner.plan(outcome.document)
            if output is not None:
                problem, state = _write_one(output)
                if problem is not None:
                    problems.append((str(output.path), problem))
                else:
                    states[str(output.path)] = state
                # On disk now; keep where it went, not the whole text.
                output = replace(output, text="")
            tally.add(outcome, output)
    except KeyboardInterrupt:
        interrupted = True

    decision = tally.decision(renderer, args.json)
    result = _present(decision, problems, states)

    if result.report:
        print(result.report, file=sys.stderr)
    if result.text:
        print(result.text)
    if interrupted:
        print(_INTERRUPTED, file=sys.stderr)
        return EXIT_INTERRUPTED
    return result.exit_code


#: Exit code after Ctrl-C, by shell convention (128 + SIGINT).
EXIT_INTERRUPTED = 130

_INTERRUPTED = (
    "Interrupted. Every file reported above is complete. Run the same command "
    "again to finish: fetched captions are cached, and finished notes come "
    "back unchanged."
)


def _announcer(args: argparse.Namespace) -> _Announce | None:
    """Progress on stderr, one line as each source starts.

    Silent for a single source, where the result follows at once, and under
    `--json`, which promises one document and nothing else.
    """
    if args.json:
        return None

    def announce(position: int, total: int, source: str) -> None:
        if total > 1:
            print(f"[{position}/{total}] {source}", file=sys.stderr, flush=True)

    return announce


def _speak_utf8() -> None:
    """Make stdout and stderr carry any transcript.

    Windows gives a redirected stream the system code page, which crashes on
    the first character outside it. Reconfigured in place, so anything already
    holding these streams keeps the same object.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:  # pragma: no branch - always present on a TextIO
            reconfigure(encoding="utf-8")


def _write_one(output: OutputFile) -> tuple[TranscriptError | None, str]:
    """Write one planned file: the problem if it was refused, else what it did.

    A problem takes the shape of a failed fetch, so it reaches stderr, the JSON
    envelope and the exit code through the same machinery.
    """
    where = str(output.path)
    try:
        return None, _write(output)
    except FileExistsError:
        return OutputExists(path=where), ""
    except OSError as error:
        return OutputUnwritable(path=where, detail=error.strerror or str(error)), ""
    except Exception as error:
        # The last resort, as in `_outcomes`: one bad write costs one file.
        detail = redact(f"{type(error).__name__}: {error}")
        return OutputUnwritable(path=where, detail=detail), ""


def _write(output: OutputFile) -> str:
    """Write one document atomically, and say whether it was written, left
    unchanged, or updated in place of this tool's own earlier note."""
    try:
        atomic_write(output.path, output.text, overwrite=output.overwrite)
        return WROTE
    except FileExistsError:
        # Identical content needs no `--force`: writing it changes nothing.
        if _already_says(output):
            return UNCHANGED
        # Nor does this tool's own note for the same video: re-rendering it —
        # with corrections, or after an upgrade — is the normal case.
        if _is_own_note(output):
            atomic_write(output.path, output.text, overwrite=True)
            return UPDATED
        raise


def _is_own_note(output: OutputFile) -> bool:
    """Whether the file in the way is this tool's note for the same video.

    Read from its frontmatter, which names the generator and the source id.
    Check-then-replace, like `_already_says`: a file swapped in between is a
    narrow race, and the worst case replaces a note claiming to be this one.
    """
    if output.source_id is None:
        return False
    try:
        with output.path.open(encoding="utf-8") as handle:
            head = handle.read(_FRONTMATTER_PROBE)
    except (OSError, UnicodeDecodeError):
        return False

    fields = read_frontmatter(head)
    return (
        fields.get("generator", "").startswith(f"{GENERATOR} ")
        and fields.get("source_id") == output.source_id
    )


def _already_says(output: OutputFile) -> bool:
    """Whether the file in the way is exactly what we would have put there.

    Anything unreadable is *not* this document, which is the answer that
    refuses. Sized first, so a large file is never read; the bound allows a
    byte per newline for Windows line endings.
    """
    try:
        limit = len(output.text.encode("utf-8")) + output.text.count("\n")
        if output.path.stat().st_size > limit:
            return False
        return output.path.read_text(encoding="utf-8") == output.text
    except (OSError, UnicodeDecodeError):
        return False


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
