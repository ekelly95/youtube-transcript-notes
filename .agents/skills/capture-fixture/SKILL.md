---
name: capture-fixture
description: Capture a real caption payload as an offline test fixture for youtube-transcript-notes. Use when a parser hits data it cannot handle, when adding support for a caption format or source, or when a bug needs a regression test built from real data.
---

# Capture a fixture

The project's test suite never touches the network — `tests/conftest.py` fails any
test that connects. Every test runs against payloads captured once and replayed
forever. When a parser meets data it mishandles, the fix starts here.

Commands are written for Windows (`.venv/Scripts/python`); on macOS and Linux
the interpreter is `.venv/bin/python`.

## Capture

Caption files:

```bash
.venv/Scripts/python -m yt_dlp --skip-download --write-subs \
  --sub-langs en --sub-format json3 -o 'NAME' '<url>'
```

Use `--write-auto-subs --sub-langs en-orig` for automatic captions. Capture
`json3` first — it carries word-level timings and marks caption scrolling
structurally. Capture `vtt` too when the bug involves automatic captions, since
that is where the rolling-window repetition lives.

For a provider bug, capture the metadata instead. Keep every field the provider
reads and every track, but replace the caption URLs with short placeholders —
they are signed and expire within hours, so the real ones are noise.

## Place and name it

`tests/fixtures/captions/<lecture>.<tier>.<lang>.<ext>`

The tier marker is load-bearing, not decoration: it decides whether
deduplication runs. An automatic track filed without `.auto.` will be treated as
human-written and keep its repeated text.

Then add a row to `tests/fixtures/README.md` saying where it came from and, more
importantly, **what it is there to catch**. A fixture nobody can explain is a
fixture nobody dares delete.

## Write the test

Assert exact counts. Vague assertions are the problem here: a transcript missing
four sentences still parses, still reads well, and is still wrong.

```python
def test_the_thing_that_broke(self) -> None:
    cues = parse_json3(load_caption("<name>"))
    assert len(cues) == 978  # the real number, checked by hand
    assert cues[0].text == "..."
```

Where two encodings of the same lecture exist, assert they agree — that is the
strongest test available, because it does not depend on anyone having worked
out the right answer in advance. See `TestCrossFormatAgreement` in
`tests/test_parse.py`.

## Verify

```bash
.venv/Scripts/python -m coverage run -m pytest && .venv/Scripts/python -m coverage report
```

The test must fail before the fix and pass after. Coverage is gated at 100% with
branches. If the fix adds a line no test reaches, either test it or delete it —
do not exempt it.
