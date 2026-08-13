# Agent guide

Keep the transcript faithful, citable, bounded, and safe to write into a notes
folder. The tests enforce these contracts; update the tests and this guide when
a contract changes.

`CLAUDE.md` and `AGENTS.md` are byte-identical, as is each skill under
`.claude/skills/` with its `.agents/skills/` twin. Edit one side, copy it over
the other, and verify with `cmp`.

## Product contracts

1. `list()` and `expand()` discover sources without downloading caption
   payloads. Only `TrackHandle.fetch()` retrieves a track.
2. A `Passage.start` is the start of its first source cue, sharpened to the word
   that begins the sentence when word timing exists. Paragraphs begin at
   sentence boundaries except after an explicit speaker change.
3. Every `Lecture` carries source, tier, language, format, retrieval time, and a
   content hash. Renderers are pure functions of that object.
4. Providers retrieve; parsers parse; refinement cleans. Parsers preserve what
   the source published, including repetition.
5. Truncation is always reported. Every file, URL, cache entry, decoded event
   list, playlist, and output path has a tested ceiling.
6. `_decide` plans, `main` performs writes, and `_present` reports what actually
   happened. Documents use stdout; diagnostics use stderr.
7. Report `NoCaptionsAvailable` only after the source positively says so.
   Missing upstream fields are compatibility failures; a fetched track with no
   usable text is `EmptyTranscript`.
8. Remote filenames include source identity. Never replace different existing
   content without explicit `--force`; identical output is `unchanged`.
9. Source text is untrusted data. Escape metadata and transcript content in
   Markdown, validate URLs, delimit agent context, and redact signed URL query
   strings from errors.
10. Writes use a sibling temporary file, flush before replacement, and use an
    exclusive claim for no-clobber output.

## Deliberate choices

- Deduplicate from known tier and format, never by guessing from text. Applying
  overlap merging to a clean track can delete legitimate repeated words.
- Detect punctuation from content. A wrong punctuation guess changes paragraph
  breaks; a wrong deduplication guess destroys text.
- Unmarked local caption files are `manual`. Automatic local files must include
  `.auto.`; the tier controls deduplication.
- Filename languages use the maintained `LANGUAGE_CODES` table. The first known
  code wins. Do not accept arbitrary two- or three-letter components such as
  `raw`, `tmp`, or `bak`.
- A URL that names a video remains one video even when it also contains
  `list=`. A playlist-only URL expands. `/embed/videoseries` is a playlist.
- Expand playlists and folders before the per-source loop. This preserves
  ordinary per-item failure isolation and reporting.
- Group local tracks by the filename before its first dot. A directory may
  therefore expand to several lectures; a stem such as `course/week-03`
  addresses one group.
- Do not warn when resolution chooses one track among several encodings. That
  is the normal two-stage workflow, not lost material.
- Keep yt-dlp floor-only. A ceiling blocks the upgrade normally needed after a
  YouTube change. Validate required result keys at use, distinguishing absent
  from present-but-empty.
- Use cached discovery only after transport failure, not after the live source
  reports a caption problem. Never cache signed caption URLs.
- Register the local provider first so an existing path beats an ambiguous
  eleven-character video ID.
- Render inside `_decide`'s per-source loop, so a lecture that fetches but
  will not render costs that one lecture. `render_many` is the renderer's
  declared `separator` joining single renders; a test pins the two equal.

## Layout

```text
models.py       immutable lecture model and provenance
errors.py       typed failures with human cause and remedy
registry.py     shared parser, renderer, and provider registry
parse/          payload -> faithful cues; no network
refine/         cues -> sentences, passages, sections, annotations, glossary
resolve.py      track selection and shared fetch pipeline
sources/        local and YouTube discovery/retrieval
render/         pure Markdown, plain, citation, JSONL, and context output
cache.py        payload and manifest cache
atomic.py       durable replacement and exclusive output claims
limits.py       all resource ceilings and capped reads
naming.py       source-aware output filenames
cli.py          plan -> write -> report
```

## Verification

Windows paths shown; on macOS and Linux the interpreter is `.venv/bin/python`
and the linter `.venv/bin/ruff`.

```bash
.venv/Scripts/python -m coverage run -m pytest
.venv/Scripts/python -m coverage report
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/ruff.exe check .
.venv/Scripts/python -m mypy
.venv/Scripts/python -m pytest -m canary  # opt-in live YouTube
```

The offline suite must not connect to the network or write to the user's home
directory. `tests/conftest.py` enforces both. Add a captured fixture for source
behavior; use the canary only to test the live yt-dlp/YouTube seam. Coverage is
gated at 100% with branches.

`mypy` runs strict over `src` only, because `py.typed` ships and anything
installing the package checks against these annotations. Tests are out of
scope: nothing installs them, and the shortcuts they take on purpose — a
`TrackHandle` holding no provider because nothing will fetch it — are the point
of a test rather than a defect in one.

CI runs the whole matrix on every push: Linux, Windows and macOS across Python
3.10–3.14, plus lint, types, and a build that checks the distribution metadata,
that no caption fixture reached the sdist, and that `py.typed` reached the
wheel.

Fixture assertions should pin exact counts or cross-format agreement. A
transcript missing several sentences can still look plausible.

## Extending the project

- Renderer: subclass `Renderer`, register it, import it from `render/__init__.py`,
  and declare `extension` and `takes_budget`.
- Parser: implement `payload -> list[Cue]` and register it. Keep cleanup in
  `refine/`.
- Provider: implement `handles`, `list`, and `load`. Reuse `TrackHandle.fetch()`
  rather than duplicating the pipeline.
