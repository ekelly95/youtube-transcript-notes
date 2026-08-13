# Changelog

Notable changes are listed newest first. Versions follow
[semantic versioning][semver]; releases before `1.0.0` may include breaking
changes.

[semver]: https://semver.org/spec/v2.0.0.html

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
