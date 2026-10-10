---
name: video-notes
description: Turn a captioned video — a lecture, talk, podcast or interview — into readable, citable notes or a grounded summary with youtube-transcript-notes. Use for a YouTube URL or ID, a playlist, a caption file or folder, or requests for a transcript, notes, summary, quotation, or citation of a video.
---

# Video notes

Run commands from the repository root. Use `.venv/Scripts/python` on Windows
or `.venv/bin/python` on macOS and Linux. If `LOCAL.md` exists beside this
file, apply its machine-specific output folder and post-processing defaults.

## Workflow

A caption file downloaded with yt-dlp's `--write-auto-subs` is named
`NAME.en.vtt` but holds automatic captions. Rename it `NAME.auto.en.vtt`
before rendering, so its rolling repetition is removed and it is not reported
as `unmarked`.

1. Inspect available tracks without downloading captions:

   ```bash
   .venv/Scripts/python -m youtube_transcript_notes <source> --list
   ```

2. Choose the most trustworthy suitable track:

   | Tier | Treatment |
   |---|---|
   | `manual` | Quote directly |
   | `unmarked` | A local file that does not say who wrote it; ask or check before quoting |
   | `asr_platform` | Disclose automatic captions; verify technical terms before quoting |
   | `asr_local` | State that quality depends on the separate transcription tool |
   | `translated` | Use for gist only; do not quote |

3. Render with the standing glossary:

   ```bash
   .venv/Scripts/python -m youtube_transcript_notes <source> \
     --glossary names.txt --out notes/
   ```

   Use the user's destination or `LOCAL.md` instead of `notes/` when given.
   Omit `--out` when the user wants the text only in conversation. For a
   playlist over about fifty videos, add `--delay 3` to stay clear of
   YouTube's bot check. Never use `--force` without reading the conflicting
   file and asking the user whether to replace it, except as step 4 allows.

4. Unless the user asked for a quick result, inspect the rendered note for
   recognition errors that require context. Put only confident proposals in a
   scratch JSON file:

   ```json
   [{"wrong": "20 bucks", "right": "20 bugs",
     "evidence": "the speaker is counting defects"}]
   ```

   Rerun the step 3 command with `--corrections <file>` added. For a YouTube
   source the tool recognises its own note from step 3 and reports `updated`.
   For a local caption file it cannot, and refuses: check that the refusal
   names the file step 3 wrote, then add `--force` — a conflict anywhere else
   goes back to the user. Never rewrite transcript text directly.
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
- concise prose following the video's own sections — its chapters, or the
  ten-minute timestamp headings when it has none;
- a timestamp for every substantive claim — linked when the source has a
  URL, a plain position when it does not;
- for an interview or panel, each claim attributed to the person who made
  it. Captions mark a new speaker with a dash and name one only when they
  say who it is; never guess a name the transcript does not give;
- quotations only when wording matters, with ASR uncertainty disclosed;
- the result of `--format citation` at the end.

Aim for 150–300 words per hour unless the user asks for a gist or depth. Keep
timestamp links in quotations and substantive notes.

For a long video or narrow question, use
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
