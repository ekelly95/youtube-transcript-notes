# Test fixtures

Caption payloads captured once and replayed offline forever, plus one built by
hand. The suite never touches the network — `tests/conftest.py` fails any test
that opens a socket — so these files are the only contact this project has
with what YouTube publishes.

Captured with `yt-dlp --skip-download --write-subs / --write-auto-subs`.

## `captions/`

All five files come from one lecture:

> *Lecture 1: Algorithmic Thinking, Peak Finding* — MIT 6.006 Introduction to
> Algorithms, Fall 2011. MIT OpenCourseWare.
> <https://www.youtube.com/watch?v=HtSuA80QTyo> (video id `HtSuA80QTyo`,
> published 2013-01-14, 3201s, 9 chapters)

| File | Track | Why it is here |
|---|---|---|
| `mit6006-lec1.manual.en.json3` | human-written | The clean case: punctuated, cased, one seg per cue, no word timings |
| `mit6006-lec1.manual.en.vtt` | human-written | Same content, line-wrapped for display |
| `mit6006-lec1.manual.en.srt` | human-written | Opens with a zero-length **empty cue** — a real edge case |
| `mit6006-lec1.auto.en.json3` | auto-generated | Word-level `tOffsetMs` and ASR confidence; scrolling expressed as `aAppend` events |
| `mit6006-lec1.auto.en.vtt` | auto-generated | The messy one: rolling-window repetition and 10 ms flush cues |

The two automatic tracks are the same speech through the same recogniser, so
the difference between them is purely how the format encodes a scrolling
caption window. That contrast is the point: `json3` marks scroll padding
structurally, while `vtt` repeats the previous window's text. It is why
`json3` is the preferred format and why deduplication is only needed for
`vtt`.

**These are 2011 automatic captions, and that matters.** They carry **two full
stops in thirty-five thousand characters**: YouTube's recogniser did not
punctuate or case its output then, and does now. So every claim about
automatic captions this pair can confirm is a claim about a recogniser that no
longer exists — which is how `TrustTier.assume_punctuated` stayed wrong for
years with the suite green and fully covered. Anything about *modern*
automatic captions has to be tested against `synthetic/`, or live against
`pytest -m canary`.

## `synthetic/`

`synthetic-talk.auto.en.json3` is built by hand rather than captured: a short
talk in the shape json3 has now — one word per `seg` with `tOffsetMs`,
`aAppend` scroll events, punctuated and cased prose.

Synthetic for two reasons. A captured recent lecture would be another
licensing question in a repository that already keeps `tests/` out of the sdist
over the material below. And every awkward case can be *placed* rather than
hoped for: it holds a `[Music]` opening and an `[Applause]` close, a breath in
the middle of a sentence and a real pause after one, two `>>` speaker turns, a
run of `[INAUDIBLE]`, and one `[? ?]` the captioner was unsure of.

What it cannot do is notice YouTube changing again — a fixture agrees with
itself forever. `tests/test_canary.py` is the other half, and asks the real
thing.

It sits outside `captions/` because several tests point a provider at that
whole directory and assert on what comes back, so a file added beside the
captured ones would not be an extra fixture but a change to their input.

## Licensing

MIT OpenCourseWare material is published under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
These captions are included unmodified, for testing only.
