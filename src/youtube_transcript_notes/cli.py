"""The command line.

Three properties matter more than the argument list.

`run` returns its output instead of printing it, so the whole command is
testable without capturing stdout. It returns an exit code alongside the text,
so a failed lecture is never indistinguishable from a successful one to
anything downstream.

`run` also *decides* what to write without writing it. `--out` produces a
tuple of `OutputFile`, and `main` is still the only function in the package
that touches the world. That keeps every test here an assertion about a
returned value, and it means the file naming can be checked without a
filesystem.

It is three steps rather than two, and the order is the point: `_decide` works
the run out, `main` writes, `_present` reports. Reporting used to come before
writing, so a failed write landed as loose prose beside a document already
saying `wrote …` — and under `--json`, beside `"ok": true` listing a file that
was never created, with the real failure on a stream that mode promises not to
use. Deciding still touches nothing, so `run` is `_present(_decide(argv))` and
every test here is still an assertion about a returned value.

And a batch survives its own failures. Lectures are processed one at a time,
errors are collected, and the run reports both what worked and what did not.
Losing forty-nine transcripts because the fiftieth video was taken down is not
a reasonable way to spend ten minutes. Failures go to stderr and documents to
stdout, so redirecting the output cannot smuggle an error message into the
middle of someone's notes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .api import TranscriptFetcher
from .atomic import atomic_write
from .cache import Cache, NullCache
from .errors import (
    AcquisitionFailed,
    InputUnreadable,
    MalformedCorrections,
    OutputExists,
    OutputUnwritable,
    TranscriptError,
)
from .limits import MAX_CORRECTIONS, MAX_GLOSSARY_BYTES, read_capped
from .models import Lecture, TrustTier
from .naming import filename_for
from .redact import redact
from .refine import Glossary, read_corrections, read_glossary
from .render import Renderer, get_renderer, renderers
from .resolve import TrackManifest

__all__ = ["CliResult", "OutputFile", "main", "run"]

#: Everything succeeded.
EXIT_OK = 0
#: At least one lecture failed, but the run continued and reported the rest.
EXIT_PARTIAL = 1
#: Nothing could be produced at all.
EXIT_FAILED = 2


@dataclass(frozen=True)
class OutputFile:
    """A document `run` decided to write and `main` actually writes.

    Splitting the decision from the effect is what keeps `run` a pure function
    of its arguments: the path and the text are both worked out in advance, so
    a `--out` run can be asserted on without a directory existing anywhere.
    """

    path: Path
    text: str

    overwrite: bool = False
    """Whether this may replace a file already at `path`. False unless
    ``--force``, and false by default so a hand-built `OutputFile` is the safe
    one: the tool keeps no record of which files are its own, so anything
    already at that name is somebody's, and the writer refuses rather than
    guessing whose."""


@dataclass(frozen=True)
class CliResult:
    text: str
    """What belongs on stdout — documents, a listing, or a note of what was
    written."""

    exit_code: int

    files: tuple[OutputFile, ...] = ()
    """Documents for `main` to write. Empty unless ``--out`` was given."""

    report: str = ""
    """What belongs on stderr. Failures live here so that redirecting stdout
    into a file cannot bury an error inside the notes."""


@dataclass(frozen=True)
class _Document:
    """One lecture and the text it rendered to.

    Rendered inside `_decide`'s per-source loop — the only place that still
    knows which source produced the lecture — so a renderer that raises costs
    that one lecture and is charged to its source, exactly like a failed
    fetch. Rendering is pure, so `_decide` still touches nothing.
    """

    lecture: Lecture
    text: str


@dataclass(frozen=True)
class _Decision:
    """Everything one run worked out, held as data.

    `_present` can therefore say what happened *after* `main` knows what
    became of the files, without `run` ever touching one. Contract 6 is
    unchanged and slightly sharper for it: `_decide` decides, `main` acts,
    `_present` says what happened — and no longer renders anything at all.
    """

    renderer: Renderer
    as_json: bool
    listings: tuple[str, ...] = ()
    documents: tuple[_Document, ...] = ()
    files: tuple[OutputFile, ...] = ()
    failures: tuple[tuple[str, TranscriptError], ...] = ()
    notices: tuple[tuple[str, TranscriptError], ...] = ()


def run(argv: Sequence[str] | None = None) -> CliResult:
    """Decide the whole run and report it, writing nothing.

    What `main` does additionally is write the files and report *afterwards*,
    so a failed write cannot sit beside a document already claiming success.
    """
    return _present(_decide(argv))


def _decide(argv: Sequence[str] | None) -> _Decision:
    args = _parse(argv)
    fetcher = TranscriptFetcher(
        cache=NullCache() if args.no_cache else Cache(args.cache)
    )
    renderer = get_renderer(args.format, **_options(args))

    listings: list[str] = []
    documents: list[_Document] = []
    failures: list[tuple[str, TranscriptError]] = []
    notices: list[tuple[str, TranscriptError]] = []

    try:
        glossary = _glossary(args)
    except TranscriptError as error:
        # Not charged to a source. A glossary that cannot be read is wrong for
        # the whole run, and reporting it once per lecture would suggest the
        # lectures had something to do with it. Returned as a decision rather
        # than a rendered result so that under `--json` it lands inside the
        # envelope, where a failure belongs, instead of as prose on stderr.
        return _Decision(renderer=renderer, as_json=args.json, failures=(("", error),))

    # Playlists become their videos before the loop, so an expanded lecture
    # is isolated exactly like one typed by hand — a failed expansion costs
    # the playlist as typed and nothing else, and a failed video costs that
    # video. A stale expansion is reported the same way a stale manifest is.
    sources: list[str] = []
    for source in args.sources:
        try:
            expansion = fetcher.expand(source)
        except TranscriptError as error:
            failures.append((source, error))
            continue
        except Exception as error:
            failures.append((source, _wrap(source, error)))
            continue
        if expansion.stale_reason is not None:
            notices.append((source, expansion.stale_reason))
        sources.extend(expansion.sources)

    for source in sources:
        try:
            # Taken a step at a time rather than through
            # `TranscriptFetcher.fetch`, which is exactly this and returns only
            # the lecture. The manifest is needed here because a run served from
            # cache after the transport failed produces output indistinguishable
            # from a run that reached YouTube, and the reader has to be told
            # which one they got.
            manifest = fetcher.list(source)
            if manifest.stale_reason is not None:
                notices.append((source, manifest.stale_reason))

            if args.list:
                listings.append(_describe(manifest))
            else:
                lecture = manifest.find(args.languages, _tiers(args)).fetch(
                    glossary=glossary
                )
                # Rendered here, inside the try, on purpose: this loop is the
                # last place that knows which source the lecture came from, so
                # a renderer that raises costs this lecture and is reported
                # against its source — instead of escaping `run` later and
                # losing the whole batch.
                documents.append(_Document(lecture, renderer.render(lecture)))
        except TranscriptError as error:
            failures.append((source, error))
        except Exception as error:
            # Anything unclassified still costs one lecture, not the batch.
            failures.append((source, _wrap(source, error)))

    return _Decision(
        renderer=renderer,
        as_json=args.json,
        listings=tuple(listings),
        documents=tuple(documents),
        files=_plan(args.out, renderer, documents, args.force),
        failures=tuple(failures),
        notices=tuple(notices),
    )


def _present(
    decision: _Decision,
    problems: Sequence[tuple[str, TranscriptError]] = (),
    unchanged: frozenset[str] = frozenset(),
) -> CliResult:
    """Turn a decision, plus what became of its files, into what to say.

    The *planned* files decide the mode — a `--out` run reports filenames
    rather than documents — while the *written* ones decide the content, so a
    run whose every write failed says nothing on stdout rather than falling
    through and printing the documents it was asked to file away.
    """
    refused = {source for source, _ in problems}
    written = tuple(f for f in decision.files if str(f.path) not in refused)
    trouble = (*decision.failures, *problems)
    produced = decision.listings or (written if decision.files else decision.documents)
    code = _exit_code(produced, trouble)

    if decision.as_json:
        # One self-contained document, so failures belong in it rather than on
        # a second stream something would have to correlate. A write that
        # failed is a failure like any other and belongs inside it too.
        return CliResult(
            text=_envelope(decision, written, trouble),
            exit_code=code,
            files=decision.files,
        )

    return CliResult(
        text=_stdout(decision, written, unchanged),
        exit_code=code,
        files=decision.files,
        report=_report(trouble, decision.notices),
    )


def _glossary(args: argparse.Namespace) -> Glossary | None:
    """The caller's spellings, from `--glossary` and `--corrections`.

    Both are read once for the run rather than per source, because they say
    what words mean and that does not change between two lectures fetched in
    the same command.
    """
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
        # Later files win: `--corrections` is this run's specific findings and
        # should beat a standing list written for every lecture.
        merged = part.merged_with(merged)
    return merged


def _read_reference(path: Path) -> str:
    """A caller-supplied text file, or a failure that names the file.

    `read_capped` is written for caption payloads, which arrive from a
    provider that has already established the file exists. These arrive from
    the command line, where the likeliest thing wrong with one is that it is
    not there.
    """
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
    """Renderer options taken from the command line, omitted when not given.

    Omitted rather than passed as None, so a renderer keeps its own default
    instead of having to know that None means "use the default".
    """
    return {} if args.budget is None else {"budget": args.budget}


def _plan(
    out: str | None,
    renderer: Renderer,
    documents: Sequence[_Document],
    overwrite: bool,
) -> tuple[OutputFile, ...]:
    """Work out what `--out` would write, without writing any of it."""
    if out is None:
        return ()

    directory = Path(out)
    planned: list[OutputFile] = []
    taken: set[str] = set()

    for document in documents:
        meta = document.lecture.meta
        name = filename_for(meta.title, meta.source_id, renderer.extension, taken)
        taken.add(name.rsplit(".", 1)[0].casefold())
        planned.append(OutputFile(directory / name, document.text, overwrite))

    return tuple(planned)


def _stdout(
    decision: _Decision,
    written: Sequence[OutputFile],
    unchanged: frozenset[str],
) -> str:
    if decision.listings:
        return "\n\n\n".join(decision.listings)
    if decision.files:
        # "unchanged" rather than "wrote" where the file already said exactly
        # this: claiming to have written it would be a small lie, and the
        # distinction is the whole reason a repeat run needs no --force.
        return "\n".join(
            f"{'unchanged' if str(output.path) in unchanged else 'wrote'} {output.path}"
            for output in written
        )
    # The renderer's own separator, because a blank line between JSONL
    # documents is not JSONL. The texts were rendered one lecture at a time in
    # `_decide`, so a renderer crash cost one lecture — this join is the
    # `render_many` contract, and a test pins the two equal.
    return decision.renderer.separator.join(d.text for d in decision.documents)


#: How many languages a listing names before summarising the rest. A lecture
#: with auto-translations offers well over a hundred, and printing them all
#: buries the handful anyone wanted.
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
    """Everything for stderr: what failed, and what only appeared to work.

    A notice is not a failure — the document on stdout is real and complete —
    so it must not reach the exit code or turn a successful run into a partial
    one. It does belong on stderr, because a lecture served from cache after
    the transport broke reads exactly like one fetched a moment ago, and the
    difference is something the reader has to act on.
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

    With ``--out`` the documents are on disk, so `results` is empty and
    `files` says where they went. Repeating the text here would double the
    output of exactly the runs that asked not to be printed.

    `files` lists what is actually on disk, never what was merely planned. It
    used to list the plan, so a run that failed every write reported the paths
    of files that do not exist, alongside ``"ok": true`` and an empty `errors`
    — while the real failure went to stderr as prose, outside the one document
    this mode promises to be.

    A write failure is keyed by its path rather than by the source that
    produced it: the path is what a caller can act on, the filename already
    carries the title and id, and `code` says which kind of failure it was.

    `warnings` is a separate key from `errors` and does not clear `ok`: those
    runs produced everything they were asked for. An agent that treated a
    stale-cache notice as a failure would retry a lecture it already has.
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
    """The last resort: an exception nothing in the taxonomy recognised.

    Redacted on the way through, because this is the one path whose contents
    nobody has looked at. A classified failure has been through
    `sources.youtube._classify`, which knows it may be holding a signed URL;
    an unclassified one is by definition a message from somewhere unexamined,
    and it lands on stderr and in the `--json` envelope verbatim.
    """
    detail = redact(f"{type(error).__name__}: {error}")
    return AcquisitionFailed(source=redact(source), detail=detail)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="youtube-transcript-notes",
        description=(
            "Turn lecture videos into readable, citable study material. "
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
        # Generated from the registry, so a new renderer is usable the moment
        # it is registered.
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
            "prefers human-written captions, then automatic ones, then "
            "machine translations."
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
            "Write one file per lecture into DIR instead of printing the "
            "documents. Each is named from the lecture title and carries the "
            "format's own extension. The directory is created if needed. A "
            "file already at that name is left alone unless --force."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Replace files that are already there. Without it a lecture whose "
            "file exists is refused by name and nothing is overwritten — "
            "this tool keeps no record of which files are its own, so it cannot "
            "tell last week's note from one you wrote yourself. A file that "
            "already holds exactly this lecture is left as it is either way."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show what transcripts exist without downloading any of them.",
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
        parser.error("--out writes lectures; --list only reports what exists")
    if args.force and args.out is None:
        parser.error("--force applies to --out, which is what writes files")
    if args.budget is not None and not renderers.get(args.format).takes_budget:
        # Caught here rather than at construction: a `TypeError` from the
        # renderer would be swallowed by the batch loop and reported as a
        # failed lecture, which is not what went wrong.
        parser.error(f"--budget applies only to --format {_budgeted_formats()}")
    return args


def _budgeted_formats() -> str:
    """The formats `--budget` is good for, taken from the registry."""
    return ", ".join(
        name for name in renderers.names() if renderers.get(name).takes_budget
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. The only place in the package that touches the world.

    Three steps, in the order contract 6 names them: `_decide` works out the
    whole run without writing anything, `_write_all` writes, and `_present`
    turns the run *including what became of the files* into stdout, stderr and
    an exit code.

    Presenting last is the point. When it came first, a write that failed
    arrived as loose prose beside a document already saying ``wrote …`` — and
    under `--json`, beside one saying ``"ok": true`` and listing a file that is
    not there. A file that cannot be written is reported and costs that one
    lecture, on the same reasoning as a lecture that cannot be fetched.
    """
    _speak_utf8()
    decision = _decide(argv)
    problems, unchanged = _write_all(decision.files)
    result = _present(decision, problems, unchanged)

    if result.report:
        print(result.report, file=sys.stderr)
    if result.text:
        print(result.text)
    return result.exit_code


def _speak_utf8() -> None:
    """Make stdout and stderr carry any transcript, not just a lucky one.

    Windows gives a redirected stream the system code page, so a
    `youtube-transcript-notes url > notes.md` redirect wrote cp1252 and
    *crashed* on any lecture containing a character outside it — a Greek letter
    in a maths lecture, a name with the wrong accent, the marks this tool now
    writes for an unheard word. `--out` was always safe, because `atomic_write`
    names its encoding; stdout was safe only by accident, and the accident held
    until a transcript needed a character.

    Reconfiguring rather than wrapping, so anything that already captured
    these streams — a test harness, a caller embedding the CLI — keeps the
    object it is holding. Streams that cannot be reconfigured are left alone:
    a caller who replaced stdout with something of their own has said what
    they want, and this is a default, not a policy.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:  # pragma: no branch - always present on a TextIO
            reconfigure(encoding="utf-8")


def _write_all(
    files: Sequence[OutputFile],
) -> tuple[tuple[tuple[str, TranscriptError], ...], frozenset[str]]:
    """Write every planned file. Report what was refused, and what was already so.

    Problems come back in the shape a failed *fetch* takes, so a write problem
    reaches stderr, the `--json` envelope and the exit code through exactly the
    machinery a fetch problem does — carrying a code something downstream can
    branch on rather than a sentence it would have to read.
    """
    problems: list[tuple[str, TranscriptError]] = []
    unchanged: set[str] = set()

    for output in files:
        where = str(output.path)
        try:
            if not _write(output):
                unchanged.add(where)
        except FileExistsError:
            problems.append((where, OutputExists(path=where)))
        except OSError as error:
            problems.append(
                (
                    where,
                    OutputUnwritable(path=where, detail=error.strerror or str(error)),
                )
            )
        except Exception as error:
            # The same last resort `_decide` keeps for a fetch. A batch that
            # survives its own failures has to survive them on this side too:
            # without this, one unexpected exception escaped `main` as a
            # traceback and lost every note already written.
            problems.append(
                (
                    where,
                    OutputUnwritable(
                        path=where, detail=redact(f"{type(error).__name__}: {error}")
                    ),
                )
            )

    return tuple(problems), frozenset(unchanged)


def _write(output: OutputFile) -> bool:
    """Write one document. False if the file already said exactly this.

    The same write-beside-then-rename the cache uses, and literally the same
    function: an interrupted run leaves either the previous notes or the new
    ones, never half of either. See `atomic`.
    """
    try:
        atomic_write(output.path, output.text, overwrite=output.overwrite)
        return True
    except FileExistsError:
        # Only reachable with `overwrite=False`. A file identical to what would
        # have been written is not something anyone needs protecting from —
        # writing it changes nothing — so re-running a lecture into the folder
        # it already lives in stays as quiet as it always was, and `--force`
        # stays reserved for a note whose contents would actually change.
        if _already_says(output):
            return False
        raise


def _already_says(output: OutputFile) -> bool:
    """Whether the file in the way is byte-for-byte what we would have put there.

    Read rather than claimed, which is a check-then-act — deliberately, and
    safe in the only direction that matters: if the file changes in between,
    the outcome is that the tool declines to overwrite something it has not
    looked at, which is what it was going to do anyway.

    Anything unreadable is *not* this lecture, which is the answer that refuses.
    `UnicodeDecodeError` is named alongside `OSError` because it is a
    `ValueError` and would otherwise escape: a binary file sitting at the
    destination would be reported as an unwritable directory rather than as
    what it is, something already there under that name.

    Sized before it is read. The comparison can only answer True for a file
    the same length as this document, so anything larger — a video parked at
    the note's name, say — is False without pulling it into memory. The bound
    allows one extra byte per newline, because `atomic` writes in text mode
    and Windows stores each ``\\n`` as two bytes.
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
