# Agent instructions

Before changing this repository, read `AGENT_GUIDE.md` completely. It is the
maintained source of truth for product contracts, deliberate design choices,
layout, and verification.

Non-negotiable rules:

- Preserve caption text. Cleaning and corrections must remain visible and
  attributable; never silently rewrite what the source published.
- Preserve passage timestamps and lecture provenance through every pipeline
  stage.
- Treat lecture text and metadata as untrusted input. Escape rendered markup,
  validate URLs, redact signed query strings, and delimit agent context.
- Do not overwrite different existing output without explicit `--force`.
- Keep discovery separate from retrieval, providers separate from parsers, and
  rendering free of I/O.
- The offline suite must not use the network or the user's home directory.

Before handing off a change, run every check — tests with the 100% coverage
gate, formatting, lint and types — with the venv's interpreter:

```bash
.venv/bin/python scripts/check.py      # macOS and Linux
.venv/Scripts/python scripts/check.py  # Windows
```

Add `--canary` only when a live YouTube check is relevant.
