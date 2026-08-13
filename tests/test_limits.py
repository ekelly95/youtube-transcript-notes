"""Payload ceilings, at every boundary that has one.

The remote boundary is tested in `test_youtube.py`, where the response fake
lives. Everything else is here: local files, the structured formats, and the
library's own front door.

What these are really protecting is `cli.run`'s per-source catch. One failed
lecture is supposed to cost one item; an allocation big enough for the OS to
kill the process costs the whole batch, and no `except` clause runs to say so.
That is why every check below has to happen *before* the thing it refuses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from youtube_transcript_notes.errors import PayloadTooLarge
from youtube_transcript_notes.limits import (
    MAX_CUES,
    MAX_EVENTS,
    MAX_PAYLOAD_BYTES,
    describe_size,
)
from youtube_transcript_notes.parse import parse_captions
from youtube_transcript_notes.parse.base import check_count
from youtube_transcript_notes.sources import LocalProvider


class TestCheckCount:
    """The shared counter, at its edges."""

    def test_one_under_the_limit_passes(self) -> None:
        check_count([0] * 9, 10, "s", "json3", "events")

    def test_exactly_the_limit_passes(self) -> None:
        check_count([0] * 10, 10, "s", "json3", "events")

    def test_one_over_the_limit_is_refused(self) -> None:
        with pytest.raises(PayloadTooLarge) as caught:
            check_count([0] * 11, 10, "s", "json3", "events")

        assert "11" in str(caught.value)
        assert "10" in str(caught.value)


class TestLocalFileLimit:
    def test_an_ordinary_caption_file_is_read(self, tmp_path: Path) -> None:
        path = tmp_path / "lecture.en.vtt"
        path.write_text("WEBVTT\n", encoding="utf-8")

        assert LocalProvider().load(str(path)) == "WEBVTT\n"

    def test_a_byte_order_mark_is_still_stripped(self, tmp_path: Path) -> None:
        """The capped read replaced `read_text(encoding='utf-8-sig')`, and a
        surviving BOM turns WEBVTT into a word the parser does not know."""
        path = tmp_path / "lecture.en.vtt"
        path.write_bytes(b"\xef\xbb\xbfWEBVTT\n")

        assert LocalProvider().load(str(path)) == "WEBVTT\n"

    def test_a_file_past_the_limit_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "huge.en.vtt"
        path.write_bytes(b"a" * (MAX_PAYLOAD_BYTES + 1))

        with pytest.raises(PayloadTooLarge):
            LocalProvider().load(str(path))

    def test_a_file_that_grew_since_it_was_measured_is_still_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`stat` is a claim about a moment already past.

        A recording still being written, a synced folder, or anything that is
        not a plain file can all deliver more than `stat` promised — so the
        streaming cap, not the cheap check, has to be the real defence.
        """
        path = tmp_path / "growing.en.vtt"
        path.write_bytes(b"a" * (MAX_PAYLOAD_BYTES + 1))

        real_stat = Path.stat

        def understated(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            result = real_stat(self, *args, **kwargs)
            if self.name == "growing.en.vtt":
                return type("Lie", (), {"st_size": 10})()
            return result

        monkeypatch.setattr(Path, "stat", understated)

        with pytest.raises(PayloadTooLarge):
            LocalProvider().load(str(path))

    def test_the_refusal_names_the_file_and_both_numbers(self, tmp_path: Path) -> None:
        path = tmp_path / "huge.en.vtt"
        path.write_bytes(b"a" * (MAX_PAYLOAD_BYTES + 1))

        with pytest.raises(PayloadTooLarge) as caught:
            LocalProvider().load(str(path))

        message = str(caught.value)
        assert "huge.en.vtt" in message
        assert describe_size(MAX_PAYLOAD_BYTES) in message


class TestStructuredPayloadLimits:
    """Small on the wire, enormous once parsed.

    Every payload here is well inside the byte ceiling. Bytes are not what
    bounds this shape — about forty bytes of JSON buys one event, and one event
    becomes several objects — which is why the counts exist at all.
    """

    def test_too_many_json3_events_is_refused(self) -> None:
        # Scalars, not objects: `check_count` runs before any per-event work,
        # so this proves the refusal happens before the expensive part.
        payload = json.dumps({"events": [0] * (MAX_EVENTS + 1)})

        assert len(payload) < MAX_PAYLOAD_BYTES  # small on the wire
        with pytest.raises(PayloadTooLarge):
            parse_captions(payload, "json3")

    def test_json3_events_at_the_limit_are_not_refused_for_size(self) -> None:
        """At the ceiling the parser proceeds — and then rejects these for
        being the wrong shape, which is a different error and the right one."""
        from youtube_transcript_notes.errors import MalformedCaptions

        payload = json.dumps({"events": [0] * MAX_EVENTS})

        with pytest.raises(MalformedCaptions):
            parse_captions(payload, "json3")

    #: A real timing line, because a bare ``-->`` is no longer a cue: the
    #: scanner parses the timestamps rather than looking for the arrow. Left
    #: deliberately textless — the ceiling counts blocks on the way past, so
    #: the refusal still fires without building 250,000 `Cue` objects.
    TIMING = "00:00:00.000 --> 00:00:01.000\n"

    @pytest.mark.parametrize("fmt", ["vtt", "srt"])
    def test_too_many_cues_is_refused(self, fmt: str) -> None:
        """The line formats have no container to measure up front, so the
        count happens on the way past instead."""
        payload = self.TIMING * (MAX_CUES + 1)

        assert len(payload) < MAX_PAYLOAD_BYTES
        with pytest.raises(PayloadTooLarge):
            parse_captions(payload, fmt)

    @pytest.mark.parametrize("fmt", ["vtt", "srt"])
    def test_cues_at_the_limit_are_accepted(self, fmt: str) -> None:
        payload = self.TIMING * MAX_CUES

        # Every cue is empty, so none survives — but no refusal either.
        assert parse_captions(payload, fmt) == []


class TestLibraryFrontDoor:
    def test_parse_captions_caps_what_a_caller_hands_it(self) -> None:
        """Both providers cap what they read, but `parse_captions` is exported
        and a caller who built a payload some other way meets the same
        ceiling."""
        with pytest.raises(PayloadTooLarge):
            parse_captions("a" * (MAX_PAYLOAD_BYTES + 1), "vtt")

    def test_a_payload_exactly_at_the_limit_is_allowed_through(self) -> None:
        assert parse_captions("a" * MAX_PAYLOAD_BYTES, "vtt") == []


class TestDescribeSize:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [(0, "0 bytes"), (512, "512 bytes"), (2048, "2.0 KiB"), (5 << 20, "5.0 MiB")],
    )
    def test_sizes_read_as_something_comparable(self, size: int, expected: str) -> None:
        assert describe_size(size) == expected


class TestNewlineHandlingSurvivedTheCappedRead:
    """A regression guard for the way the size bound was implemented.

    `read_text` opened in text mode and translated line endings on the way in.
    Bounding the read meant reading bytes instead, which does not — so caption
    files written on Windows arrived with their CRLFs intact and every payload
    quietly changed shape. Parsing survived it; the cache and any byte-exact
    comparison would not have.
    """

    @pytest.mark.parametrize(
        "written",
        [
            b"WEBVTT\r\n\r\n00:00.000 --> 00:01.000\r\nhi\r\n",
            b"WEBVTT\rold-mac\r",
        ],
    )
    def test_no_carriage_return_survives(self, tmp_path: Path, written: bytes) -> None:
        path = tmp_path / "crlf.en.vtt"
        path.write_bytes(written)

        assert "\r" not in LocalProvider().load(str(path))

    def test_crlf_text_matches_what_text_mode_would_have_given(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "crlf.en.vtt"
        path.write_bytes(b"WEBVTT\r\nsecond\r\n")

        assert LocalProvider().load(str(path)) == "WEBVTT\nsecond\n"


class TestCacheReadIsBoundedToo:
    """A cache hit is still a read of a file.

    Nothing this version writes can exceed the ceiling, so an entry that trips
    this was written by an older version or put there by hand. "It came from us
    last week" is not a reason to read it unbounded now.
    """

    def test_an_oversized_cache_entry_is_refused(self, tmp_path: Path) -> None:
        from youtube_transcript_notes.cache import Cache

        cache = Cache(tmp_path / "cache")
        path = cache.path_for("k")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"a" * (MAX_PAYLOAD_BYTES + 1))

        with pytest.raises(PayloadTooLarge):
            cache.read("k")

    def test_an_ordinary_cache_entry_still_round_trips(self, tmp_path: Path) -> None:
        from youtube_transcript_notes.cache import Cache

        cache = Cache(tmp_path / "cache")
        cache.write("k", "WEBVTT\n")

        assert cache.read("k") == "WEBVTT\n"

    def test_a_missing_entry_is_still_a_miss(self, tmp_path: Path) -> None:
        from youtube_transcript_notes.cache import Cache

        assert Cache(tmp_path / "cache").read("nope") is None
