# Changelog

Notable changes are listed newest first. Versions follow
[semantic versioning][semver]; releases before `1.0.0` may include breaking
changes.

[semver]: https://semver.org/spec/v2.0.0.html

## 0.5.0

Fixes from an outside audit. Trust tiers now say only what is known about
a track, the starter glossary no longer marks ordinary English as a
misheard name, and the `video-notes` skill keeps a summary to what the
transcript says.

**Breaking changes**

- A local caption file with no tier marker in its name is now `unmarked`, not
  `manual`, and its byline says "captions of unstated origin". yt-dlp's
  `--write-auto-subs` writes automatic captions under exactly such a name, so
  those notes were claiming human authorship. `--tiers manual` no longer
  selects an unmarked file; mark it `.manual.` or pass `--tiers unmarked`. The
  README now lists every filename marker.
- `names.txt` moves to `glossaries/agent-engineering.txt`, one glossary per
  field. Wrong forms that are ordinary English are gone: "Ada", "Colab",
  "cowork", "azalea", "Rubrik", "clockwork", "Clyde", "hot code" and "cold
  start problem" were being bracketed as names in unrelated sentences. The
  `video-notes` skill passes a glossary only for a video in its field.
- `Correction.confidence` is replaced by `distance`: the edit distance for a
  near-miss, or `null` for a wrong form named in a glossary or corrections
  file. The old number was one minus a tenth of the edit distance, shown as
  "Confidence 0.90" as if measured. JSONL lines carry `distance`, and older
  lines with `confidence` still read. The Markdown appendix column is now
  "Match" ("named", "1 edit").

**Fixes**

- Notes and cache entries are written with LF line endings on every platform,
  so a notes folder synced between Windows and other systems no longer changes
  on every re-render. A CRLF note from an older version still counts as
  unchanged.
- A capitalised word and a colon (`NASA: launched in 1958`) is read as a
  speaker label only in a track that labels speakers, where a label recurs or
  follows `>>`, or when it opens the track. Before, it named a speaker and
  attributed everything after it to them.
- Error messages and the context preamble say "video" rather than "lecture".
  Error codes and class names are unchanged.
- `--format context` keeps to its budget exactly by its own estimate.
  Rounding each part down let the parts add up to less than the whole, so the
  output could run a few tokens over.
- The context header leaves out the duration of a video under a minute instead
  of saying "0 min", and the citation note marks its retrieval date as UTC.
- A video with only automatic captions is fetched from its original track
  (`en-orig`) rather than the plain `en` beside it. Both are the same
  transcription, but current yt-dlp lists `en` first, and YouTube serves it
  through its translation endpoint, which answered HTTP 429 long before the
  original did.
- When a video has an `-orig` track, only automatic tracks in that language
  are `asr_platform`. The uploader's declared language decides only when there
  is no `-orig` track, so a wrong declaration can no longer file a machine
  translation as the transcription.
- A cached manifest or playlist is used only when YouTube could not be
  reached. A video it reports unavailable, age-restricted or region-blocked
  now fails as such, instead of being served from cache under a notice saying
  the source could not be reached.
- The pipx remedy for a missing yt-dlp now installs `"yt-dlp[default]"`, the
  extra that carries YouTube's challenge solver.
- A non-text `language` field from yt-dlp is ignored rather than failing the
  listing with advice to retry.

**Skill**

- `video-notes` binds summaries to the transcript: every statement traces to
  a timestamp, no background knowledge is added, a non-`manual` track is named
  up front, and an `## Omitted` block is reproduced verbatim. It also says the
  tool chooses the track, and that a `manual` track in another language is a
  translation.
- Tests check that every command in a skill parses with the real CLI parser
  and that any glossary it names exists.

**Tests**

- The 2011 automatic track's vtt and json3 encodings now have to agree on
  every paragraph stamp, not only on the words: each of the 61 vtt stamps is
  the json3 time of its opening word.
- Stamp checks match the instance of the opening word, not any time the word
  was said. The context budget test checks the output fits and that the next
  passage would not, instead of a loose upper bound. Paragraph counts are pinned
  beside the p95 length band.

## 0.4.0

Brought up to date with yt-dlp and Python as they stand in October 2026, and
reshaped around how the tool is actually used: long playlists, podcasts and
interviews as well as lectures, and notes kept in a notes app.

**Breaking changes**

- Python 3.11 or newer. 3.10 reaches end of life this month; 3.15 is now
  supported and tested.
- Markdown notes open with YAML frontmatter (title, source ID, URL, channel,
  publication date, caption tier, language, generator). Anything that parsed
  the first line as the `#` heading needs to skip the block.
- The bundled skill is renamed `lecture-notes` → `video-notes`, in both the
  Claude Code and Codex copies.
- `str(TrustTier.MANUAL)` is now `"manual"`: the enum is a `StrEnum`.

**YouTube**

- The `youtube` extra installs `yt-dlp[default]>=2025.11.12`. That release
  made a JavaScript runtime part of YouTube extraction and moved the solver
  into `yt-dlp-ejs`, which only the `default` extra brings. The tool lets
  yt-dlp use Deno, Node or Bun, whichever is installed, and when none is, the
  advice on a transport failure says so.
- YouTube's bot check and HTTP 429 are named failures, `BOT_CHECK` and
  `RATE_LIMITED`, with advice about the connection and about waiting rather
  than a suggestion to retry at once.
- `--delay SECONDS` spaces out videos in one run (1 second by default, 0 to
  turn it off), so a playlist does not reach YouTube as a burst.
- `--refresh` refetches cached captions and stores the fresh copies.

**Notes**

- With `--out`, each note is written as soon as its video is done, and a
  `[n/N]` progress line goes to stderr as each source starts. Ctrl-C keeps
  every finished note and exits 130.
- A note the tool wrote for the same YouTube video is re-rendered and
  reported as `updated` — a corrections pass no longer needs `--force`.
  Hand-written notes, another video's notes and notes from local caption
  files are still refused. Notes written by 0.3.0 have no frontmatter, so
  refresh them once with `--force`.
- A video longer than twenty minutes with no published chapters gets a
  timestamp heading every ten minutes.
- User-facing wording covers talks, podcasts and interviews; the skill tells
  summaries to attribute each claim to the speaker who made it. The Python
  API keeps its names.

**Maintenance**

- Comments and docstrings trimmed to the reasons and contracts, from 0.63 to
  0.30 lines of prose per line of code, verified to change no code.

## 0.3.0

First release on PyPI: `pipx install "youtube-transcript-notes[youtube]"`.

- Names the missing-transport failure `TRANSPORT_NOT_INSTALLED` rather than
  reporting it as an unclassified acquisition failure, whose advice — retry, and
  check the transport is up to date — was advice about a package that is not
  installed. The remedy now also covers pipx installations, where a plain
  `pip install` reaches a different environment and changes nothing.
- Documents both ways in: the packaged command-line tool, and the clone that
  carries the Claude Code and Codex skills and the `names.txt` glossary. Neither
  of those is part of the distribution, so `pipx install` gives the tool without
  the agent workflow.

Nothing changes in the notes, citations, or transcripts the tool produces.

## 0.2.0

Initial public release, developed privately under the name Lectern.

- Converts captioned videos, playlists, caption files, and folders into
  timestamped Markdown, plain text, citations, JSONL, or bounded agent context.
- Records caption provenance and distinguishes human-written, automatic,
  locally transcribed, and translated tracks.
- Preserves source wording while showing proposed corrections separately.
- Caches remote captions and metadata for use during transport failures.
- Processes sources independently and reports partial failures.
- Refuses to replace different existing output without `--force`.
- Supports Python 3.10–3.14 on Linux, Windows, and macOS.

Audio transcription, account handling, channel URLs, search URLs, and
playlists over 500 videos are not supported.
