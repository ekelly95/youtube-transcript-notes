# Changelog

Notable changes are listed newest first. Versions follow
[semantic versioning][semver]; releases before `1.0.0` may include breaking
changes.

[semver]: https://semver.org/spec/v2.0.0.html

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
