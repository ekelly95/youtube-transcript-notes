---
name: lecture-notes
description: Turn a captioned lecture video into readable, citable notes or a grounded summary with youtube-transcript-notes. Use for a YouTube URL or ID, a playlist, a caption file or folder, or requests for a lecture transcript, notes, summary, quotation, or citation.
---

# Lecture notes

Run commands from the repository root. Use `.venv/Scripts/python` on Windows
or `.venv/bin/python` on macOS and Linux. If `LOCAL.md` exists beside this
file, apply its machine-specific output folder and post-processing defaults.

## Workflow

1. Inspect available tracks without downloading captions:

   ```bash
   .venv/Scripts/python -m youtube_transcript_notes <source> --list
   ```

2. Choose the most trustworthy suitable track:

   | Tier | Treatment |
   |---|---|
   | `manual` | Quote directly |
   | `asr_platform` | Disclose automatic captions; verify technical terms before quoting |
   | `asr_local` | State that quality depends on the separate transcription tool |
   | `translated` | Use for gist only; do not quote |

3. Render with the standing glossary:

   ```bash
   .venv/Scripts/python -m youtube_transcript_notes <source> \
     --glossary names.txt --out notes/
   ```

   Use the user's destination or `LOCAL.md` instead of `notes/` when given.
   Omit `--out` when the user wants the text only in conversation. Never use
   `--force` without reading the conflicting file and asking the user whether
   to replace it — with one exception: step 4's rerun replaces the file this
   workflow itself just wrote, and that needs nobody's permission.

4. Unless the user asked for a quick result, inspect the rendered note for
   recognition errors that require context. Put only confident proposals in a
   scratch JSON file:

   ```json
   [{"wrong": "20 bucks", "right": "20 bugs",
     "evidence": "the speaker is counting defects"}]
   ```

   Rerun the step 3 command with `--corrections <file>` added, plus
   `--force`: the corrected note always differs from the file step 3 wrote,
   so the overwrite guard refuses without it. Before adding `--force`, check
   that the refusal names the file this run produced — a conflict anywhere
   else goes back to the user. Never rewrite transcript text directly.
   A digit correction requires explicit contextual or audio evidence. Add a
   recurring, non-numeric correction to `names.txt` as
   `Right Form: wrong form`.

## Deliverables

A **transcript** is the rendered, timestamped source. **Notes** are that document
saved to a folder. A **summary** is prose written from the rendered source; it
does not replace the evidence.

A summary must include:

- title, channel, date, and video link, when the source provides them — a
  local caption file has only its filename stem, and the summary says what
  is missing rather than filling it in from anywhere else;
- concise prose following the lecture's own sections;
- a timestamp for every substantive claim — linked when the source has a
  URL, a plain position when it does not;
- quotations only when wording matters, with ASR uncertainty disclosed;
- the result of `--format citation` at the end.

Aim for 150–300 words per hour unless the user asks for a gist or depth. Keep
timestamp links in quotations and substantive notes.

For a long lecture or narrow question, use
`--format context --budget N`. For an exact time range:

```python
from youtube_transcript_notes import TranscriptFetcher

lecture = TranscriptFetcher().fetch("<source>")
excerpt = lecture.between(720, 1200)
```

Never imply omitted text was reviewed.

The tool supports multiple videos, playlists, caption files, and folders with
per-item failure isolation. It refuses videos without captions, channels,
searches, and playlists over 500 items.
