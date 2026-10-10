# youtube-transcript-notes

Turn captioned videos — lectures, talks, podcasts, interviews — into readable
notes and grounded summaries with clickable timestamps.

Give Claude Code or Codex a YouTube URL and ask for notes or a summary. The
bundled skill finds the best available captions, records how trustworthy they
are, and preserves a link back to the source for every substantive point. The
command-line tool can also be used on its own.

Videos without captions are refused. The project does not download audio or run
speech recognition.

## Quick start

Python 3.11 or newer. There are two ways in, depending on whether you want the
agent workflow or only the command-line tool.

### Install the command-line tool

```bash
pipx install "youtube-transcript-notes[youtube]"
```

The `[youtube]` part brings in [yt-dlp](https://github.com/yt-dlp/yt-dlp), which
is what fetches captions. Without it the tool installs and runs but cannot reach
YouTube. `pip install "youtube-transcript-notes[youtube]"` into a virtual
environment does the same job.

yt-dlp also wants a JavaScript runtime for full YouTube support. If you have
neither [Deno](https://docs.deno.com/runtime/getting_started/installation/) nor
Node.js 20+ installed, install one (on Windows, `winget install DenoLand.Deno`).
The tool uses whichever it finds.

```bash
youtube-transcript-notes \
  "https://www.youtube.com/watch?v=HtSuA80QTyo" --out notes/
```

### Or clone it, for Claude Code and Codex

The bundled skills and the `glossaries/` folder are repository files rather than
part of the installed package, so the agent workflow starts from a clone:

```bash
git clone https://github.com/ekelly95/youtube-transcript-notes.git
cd youtube-transcript-notes
python -m venv .venv
```

Windows:

```bash
.venv/Scripts/python -m pip install -e ".[youtube]"
```

macOS or Linux:

```bash
.venv/bin/python -m pip install -e ".[youtube]"
```

Open the folder in Claude Code or Codex and ask:

> Make me notes on https://www.youtube.com/watch?v=HtSuA80QTyo

Claude Code reads `.claude/skills/`; Codex reads the identical workflow from
`.agents/skills/`. To set a personal output folder or post-processing step,
create a gitignored `LOCAL.md` beside the relevant `SKILL.md`.

Either way, the result is ordinary Markdown:

```markdown
# Lecture 1: Algorithmic Thinking, Peak Finding

*MIT OpenCourseWare · 14 January 2013 · [watch](https://www.youtube.com/watch?v=HtSuA80QTyo) · human-written captions (en)*

## Intro

**[0:22](https://www.youtube.com/watch?v=HtSuA80QTyo&t=22)** **PROFESSOR:** Hi. I'm
Srini Devadas. I'm a professor of electrical engineering and computer science…
```

## What it adds

- Reassembles caption fragments into readable paragraphs that begin at sentence
  boundaries.
- Keeps paragraph timestamps, title, channel, publication date, chapters, and
  retrieval provenance. A long video without chapters gets a timestamp heading
  every ten minutes, so the note still has an outline.
- Prefers human-written captions and identifies automatic or translated tracks.
- Interprets common speaker and non-speech annotations without inventing
  speakers.
- Proposes visible corrections beside the original words; it never silently
  rewrites a transcript.
- Handles videos, playlists, caption files, and folders with per-item failure
  isolation.
- Caches captions and metadata so previously fetched videos remain available
  during a YouTube or yt-dlp outage.
- Refuses to replace a note it did not write unless `--force` is explicit; its
  own notes are updated in place.

## CLI

```text
youtube-transcript-notes <source>… [--out DIR] [--force]
  [--format markdown|plain|citation|jsonl|context] [--list]
  [--languages LANG…] [--tiers TIER…] [--budget N]
  [--glossary FILE] [--corrections FILE]
  [--delay SECONDS] [--json]
  [--cache DIR] [--refresh | --no-cache] [--version]
```

`<source>` may be a YouTube URL, video ID, playlist URL, caption file, or
folder. Multiple sources are allowed; one failure does not discard the rest.
A caption file has no video to link to, so its timestamps are plain positions
rather than links.

Useful examples. An installed copy is called as `youtube-transcript-notes`; from
a clone, run them with the venv's `python` (`.venv/Scripts/python` or
`.venv/bin/python`, as in Quick start). The two are the same program.

```bash
# See available tracks without downloading caption payloads
python -m youtube_transcript_notes HtSuA80QTyo --list

# Prefer German, then English
python -m youtube_transcript_notes HtSuA80QTyo --languages de en

# Process a playlist or a folder into one file per video
python -m youtube_transcript_notes <playlist-url> --out notes/course
python -m youtube_transcript_notes ~/Downloads/course-captions --out notes/course

# Produce a bibliography entry or bounded agent context
python -m youtube_transcript_notes HtSuA80QTyo --format citation
python -m youtube_transcript_notes HtSuA80QTyo --format context --budget 6000
```

| Format | Also accepts | Purpose |
|---|---|---|
| `markdown` | `md` | Timestamped study notes; default |
| `plain` | `text` | Reading text without markup |
| `citation` | `cite` | Bibliography entry and retrieval provenance |
| `jsonl` | | One record per passage for other tools |
| `context` | | Metadata and outline followed by budgeted transcript text |

Documents go to stdout and failures to stderr. Exit code `0` means all items
succeeded, `1` means some failed, and `2` means nothing was produced. With
`--json`, results and failures share one machine-readable document.

Four combinations are refused before any work starts, because each one asks
for two different things at once: `--out` with `--list` (a listing is not a
document to file), `--force` without `--out` (nothing is being overwritten),
`--budget` with a format that has no budget to spend — only `context` does —
and `--refresh` with `--no-cache` (one stores what it fetches, the other
stores nothing).

## Trust and corrections

The selected track is recorded in every result except plain text, which is
deliberately stripped of everything citable:

| Tier | Meaning | Quoting guidance |
|---|---|---|
| `manual` | Human-written captions | Quote directly |
| `unmarked` | A local caption file whose name does not say who wrote it | Find out its origin before quoting |
| `asr_platform` | YouTube automatic captions | Verify technical terms |
| `asr_local` | Captions made by a separate transcription tool | Depends on that tool |
| `translated` | Machine-translated transcription | Use for gist, not quotation |

A `manual` track in a language other than the one spoken is a translation,
however carefully a person wrote it. YouTube lists such tracks as `manual` —
the MIT lecture used in the tests has human-written Arabic and Chinese tracks
— so quote one only as a translation.

A note may also carry two marks of the captioner's own uncertainty:
`(inaudible)` where they could not make the words out, and `(?)` after a word
they guessed at — `a cure(?)` keeps the guess with the doubt attached.

The video title and chapter headings provide an initial spelling glossary.
Add recurring acoustic errors with `--glossary`:

```text
Anthropic
Claude Code: quad code, Squad code, Cloud Code, Quack Co
Andrej Karpathy: Andrew Carpet
```

```bash
python -m youtube_transcript_notes <source> --glossary glossaries/agent-engineering.txt
```

The repository keeps one glossary per field in
[`glossaries/`](https://github.com/ekelly95/youtube-transcript-notes/tree/main/glossaries).
So far there is
[`agent-engineering.txt`](https://github.com/ekelly95/youtube-transcript-notes/blob/main/glossaries/agent-engineering.txt),
for talks on AI coding agents. Use a glossary only for a video in its field.
Every `wrong` form is marked wherever it appears, so another field's list
brackets ordinary words. An installed copy does not include the folder.

`--corrections found.json` accepts a list of `wrong`, `right`, and optional
`evidence` fields. Corrections appear as `quad code [Claude Code]` and in an
appendix. The original caption text remains intact.

Local caption filenames may include tier and language metadata between the
stem and the extension, for example `week-01.auto.en.vtt`:

| Marker | Tier |
|---|---|
| `.manual.`, `.human.` | `manual` |
| `.auto.`, `.asr.` | `asr_platform` |
| `.whisper.`, `.transcribed.` | `asr_local` |
| `.translated.` | `translated` |
| none | `unmarked` |

The tier controls deduplication: only `asr_platform` vtt and srt tracks have
their rolling-window repetition removed. yt-dlp's `--write-auto-subs` saves
automatic captions as `NAME.en.vtt`, with no marker, so rename such a file
`NAME.auto.en.vtt` before rendering it; left unmarked, it keeps every repeated
line.

## Files and cache

Remote output names include the source ID, such as
`Peak Finding (HtSuA80QTyo).md`. This prevents a video title from claiming an
unrelated note. Local files keep their stem.

Markdown notes open with YAML frontmatter — title, source ID, URL, channel,
publication date, caption tier and language — which Obsidian shows as
properties and Dataview can query. Every value is quoted and escaped, so a
hostile title stays inert.

If the chosen name already contains identical output, the run reports
`unchanged`. If it holds this tool's own note for the same YouTube video (its
frontmatter says so), the note is re-rendered and reported as `updated` — this
is how a corrections pass or an upgrade refreshes a note. Anything else is
refused unless `--force` is given: a note you wrote, another video's note, or
any local-file note, because a filename stem is not a unique identity. Notes
written before 0.4.0 have no frontmatter, so refresh those once with `--force`.
A title changed upstream creates a new file and leaves the old one alone.

The default cache is:

- Windows: `%LOCALAPPDATA%\youtube-transcript-notes\cache`
- macOS: `~/Library/Caches/youtube-transcript-notes`
- Other Unix: `$XDG_CACHE_HOME/youtube-transcript-notes`, or
  `~/.cache/youtube-transcript-notes` when that variable is unset

Use `YOUTUBE_TRANSCRIPT_NOTES_CACHE`, `--cache`, or `--no-cache` to override it,
and `--refresh` to refetch captions that changed upstream and store the fresh
copies.
When yt-dlp cannot reach YouTube, cached videos remain usable and are clearly
reported as stale. Updating yt-dlp is the usual repair for upstream changes.

The cache is an optimisation and never a reason for a run to fail: if it cannot
be written — a full disk, a read-only directory — the note is still produced
and the next run simply fetches again.

## Python API

```python
from youtube_transcript_notes import TranscriptFetcher

manifest = TranscriptFetcher().list("HtSuA80QTyo")  # metadata only
lecture = manifest.find(["en"]).fetch()
excerpt = lecture.between(720, 1200)  # 12:00–20:00, still citable
```

## Limits

- Captioned videos only; no audio transcription fallback.
- Channel and search URLs are refused. Use individual video or playlist URLs.
- Playlists over 500 videos are refused rather than silently truncated.
- Speaker identity is kept only when the captions provide it.
- Paragraph sizing counts space-separated words, so it is less effective for
  languages such as Chinese, Japanese, and Thai.
- A `watch?v=…&list=…` share link means one video; use the playlist URL to
  process the playlist.

## Troubleshooting

Every failure carries a plain-language cause and something to try. With
`--json` the same failure also carries a stable `code`, so a script or an agent
can branch on `NO_CAPTIONS_AVAILABLE` rather than on English.

**"Sign in to confirm you're not a bot" (`BOT_CHECK`), or every video failing
at once.** YouTube blocks addresses it thinks are automated, and it treats
datacenter ranges as automated by default. This is the commonest first-run
failure and it is almost never about the video. Run from an ordinary home
connection rather than a VPN, a cloud box, or a CI runner. There is no cookie
or sign-in option: the tool has no account handling, deliberately.

**HTTP 429 (`RATE_LIMITED`).** Too many requests in too short a time. Wait
several minutes and run the same command again — captions already fetched are
cached, so the rerun picks up where the last one stopped. Videos in one run are
spaced out by `--delay` (one second by default); raise it for long playlists.

**Errors mentioning extraction, the player response, or a signature.**
YouTube changed something and yt-dlp has not caught up, or your copy is old.
These are reported as `TRANSPORT_CONTRACT_CHANGED` rather than as a problem
with the video, because the video is usually fine:

```bash
.venv/Scripts/python -m pip install -U "yt-dlp[default]"
```

For a pipx install, `pipx runpip youtube-transcript-notes install -U
"yt-dlp[default]"` reaches the tool's own environment. If yt-dlp is already
current, wait a day or two — this is the one dependency whose upstream breaks
on somebody else's schedule.

**"No JavaScript runtime was found".** Since late 2025 yt-dlp solves YouTube's
challenges with an external JavaScript runtime. Captions usually still arrive
without one, but yt-dlp has deprecated that path. Install
[Deno](https://docs.deno.com/runtime/getting_started/installation/) or Node.js
20+; the tool enables Deno, Node and Bun, whichever is on `PATH`.

**"has no caption tracks of any kind".** The tool reads captions; it does not
create them. There is no audio download and no speech recognition, on purpose.
If you can obtain a transcript as a caption file from anywhere else — a course
platform, or a separate transcription tool — pass that file's path as the
source instead.

**Age-restricted or region-blocked videos** cannot be retrieved. Both are
reported by name rather than as a generic failure, so you can tell them from a
video that was deleted.

**"refusing to replace".** The output file already exists and holds something
different. This is the guard that stops a video retitled upstream, or an
uploader who picked your filename, from overwriting a note. Read the file
first; if replacing it is what you want, add `--force`.

**A long playlist.** Videos are fetched one at a time, a second apart, with a
`[n/N]` progress line on stderr as each starts. With `--out`, each note is
written as soon as its video is done, so stopping with Ctrl-C keeps everything
finished; run the same command again to continue. Exit code `1` afterwards
means some items failed and the rest succeeded — the run is not discarded
because one video was.

## Development

Windows paths shown, as in Quick start; on macOS and Linux the interpreter is
`.venv/bin/python` and the linter `.venv/bin/ruff`.

```bash
.venv/Scripts/python -m pip install -e ".[dev,youtube]"
.venv/Scripts/python -m coverage run -m pytest
.venv/Scripts/python -m coverage report
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/ruff.exe check .
.venv/Scripts/python -m mypy
.venv/Scripts/python -m pytest -m canary  # live YouTube; opt in
```

The offline suite enforces 100% branch coverage and never touches the network —
`tests/conftest.py` fails any test that opens a connection, so every test runs
against payloads captured once and replayed. `mypy` runs strict over `src`,
which is what makes the shipped `py.typed` marker a promise rather than a
claim.

Every push runs the full matrix: Linux, Windows and macOS across Python
3.11–3.15. The live canary is not part of it, because GitHub's runners are
datacenter addresses and YouTube blocks them — run it yourself before a
release.

## License and credit

The code is MIT licensed. The captured lecture captions under
`tests/fixtures/captions/` are MIT OpenCourseWare's, retain their CC BY-NC-SA
4.0 license, and are excluded from distributions; the hand-built fixture under
`tests/fixtures/synthetic/` is covered by this project's own license.

Captions are retrieved through [yt-dlp](https://github.com/yt-dlp/yt-dlp),
which does the hardest and least thanked job here: keeping up with YouTube.
