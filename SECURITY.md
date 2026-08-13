# Security

## Reporting a vulnerability

Open a [private security advisory][advisory]. Do not open a public issue for a
suspected vulnerability.

[advisory]: https://github.com/ekelly95/youtube-transcript-notes/security/advisories/new

This project has no bug bounty. The maintainer aims to reply within two weeks.

## Security model

This is a local command-line tool and library. It connects to YouTube through
yt-dlp and reads files supplied by the user. It has no server, account,
database, hosted API, or application secret.

Lecture metadata and captions are untrusted. The tool therefore:

- escapes source text before rendering Markdown;
- validates URLs before creating links;
- marks agent context as untrusted data;
- includes source identity in remote filenames and requires `--force` to
  replace different existing output;
- redacts signed caption URLs from errors and never caches them; and
- enforces limits on files, URLs, cache entries, decoded data, playlists, and
  output paths.

Please report any bypass of these controls, or any way to write outside the
chosen directory, execute code, or expose a secret.

## Out of scope

- yt-dlp vulnerabilities, which should be reported to
  [yt-dlp](https://github.com/yt-dlp/yt-dlp/security)
- YouTube bot blocking, rate limits, or extraction changes
- inaccurate captions
- attacks that require prior control of the user's machine or input files

## Supported versions

Only the latest release is supported.
