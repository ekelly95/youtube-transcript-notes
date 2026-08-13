"""A live check that the yt-dlp seam still fits YouTube.

Deselected by default — see ``addopts`` in pyproject.toml. Run it deliberately:

    .venv/Scripts/python -m pytest -m canary

Everything else in this suite runs from captured fixtures, and a fixture cannot
answer the one question this file exists to ask: whether the shape the tool
reads is still the shape yt-dlp and YouTube produce *today*. The captured
``HtSuA80QTyo.info.json`` was true the day it was captured and will go on
passing long after the real contract has moved. That gap is what the ``<2027``
version ceiling used to stand in for, badly.

**Run it from your own machine, not from CI.** GitHub-hosted runners are
datacenter addresses and YouTube bot-blocks them hard, so a scheduled job would
fail for reasons that have nothing to do with this project. A canary that cries
wolf every week is worse than no canary, because you stop reading it.

Assertions are about *shape and presence*, never counts. The pinned counts
elsewhere in the suite — 978 cues, 6841 words — exist because a transcript
missing four sentences still looks perfectly fine. Pinning them against a live
video instead would fail whenever MIT re-uploads, which is the same crying-wolf
failure in a different costume.

Two ways to read a failure here:

* `TransportContractChanged` — the seam moved. Try ``pip install -U yt-dlp``.
  If that does not fix it, `_require_shape` in ``sources/youtube.py`` names the
  key that went missing and that is where the repair goes.
* Anything else — read the message. Age gates, region blocks and the lecture
  simply having been taken down all report themselves by name, and none of them
  is a problem with the tool.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from youtube_transcript_notes.models import TrustTier
from youtube_transcript_notes.refine import ends_sentence
from youtube_transcript_notes.sources import youtube

pytestmark = [
    # Lets conftest's `block_network` fixture stand aside for this module.
    pytest.mark.network,
    # Keeps it out of the default run. A separate marker from `network` on
    # purpose: see the addopts comment in pyproject.toml.
    pytest.mark.canary,
]

#: MIT 6.006 Lecture 1 — the lecture every fixture in this suite was captured
#: from. Chosen because it is institutional, long-published, openly licensed
#: and carries both human-written and automatic tracks, so one video exercises
#: every tier the resolver ranks.
LECTURE = "HtSuA80QTyo"


def test_the_extractor_still_returns_the_shape_the_tool_reads() -> None:
    """The keys `_meta_from` and `_tracks_from` index into.

    Checked before anything is built on top of them, so a failure names the
    contract rather than surfacing three frames later as an empty manifest.
    """
    info = youtube._extract_info(youtube._watch_url(LECTURE))

    assert isinstance(info, dict)
    assert info.get("id") == LECTURE
    assert info.get("title")

    # The two that matter most. Their *absence* is what `_require_shape`
    # exists to catch, and it is the difference between "this lecture has no
    # captions" and "the tool can no longer see any".
    for key in youtube._CAPTION_KEYS:
        assert key in info, f"yt-dlp no longer returns {key!r}"
        assert isinstance(info[key], dict)

    entries = [
        entry for group in info["automatic_captions"].values() for entry in group
    ]
    assert entries, "no automatic caption tracks at all"
    assert any("ext" in entry and "url" in entry for entry in entries), (
        "caption entries no longer carry 'ext' and 'url'"
    )


def test_a_lecture_still_lists_and_fetches() -> None:
    """The whole path, end to end, against the real thing.

    `list` must cost one request and download nothing; `fetch` must come back
    with prose that still carries an anchor. Between them that is every
    invariant this project claims about acquisition.
    """
    manifest = youtube.YouTubeProvider().list(LECTURE)

    assert len(manifest) > 0
    assert "en" in manifest.languages()

    handle = manifest.find(["en"])
    assert handle.track.caption_format in {"json3", "vtt", "srt"}

    lecture = handle.fetch()

    assert lecture.sections
    passages = [passage for section in lecture.sections for passage in section.passages]
    assert passages, "a lecture with no passages"
    assert passages[0].text.strip()
    assert passages[0].start >= 0.0

    # Contract 3: a transcript with no provenance is a scrape, not a source.
    assert isinstance(lecture.provenance.tier, TrustTier)
    assert len(lecture.provenance.content_hash) == 64
    assert lecture.provenance.provider == "youtube"


def test_a_manual_track_is_still_offered() -> None:
    """The tier split still has both sides on a real lecture.

    Worth its own test because `_tier_for` classifies by comparing a track's
    language against the video's declared one. If yt-dlp stopped reporting
    `language`, every automatic track would silently be filed `TRANSLATED` —
    the least trustworthy tier there is — and nothing else here would notice.
    """
    tiers = {handle.track.tier for handle in youtube.YouTubeProvider().list(LECTURE)}

    assert TrustTier.MANUAL in tiers, "no human-written track on a lecture that has one"
    assert TrustTier.ASR_PLATFORM in tiers, (
        "no automatic track classified as the original transcription — "
        "check that yt-dlp still reports 'language' and the '-orig' suffix"
    )


#: The playlist the fixture lecture belongs to — MIT 6.006, Fall 2011.
PLAYLIST_ID = "PLUl4u3cNGP61Oq3tWYp6V_F-5jb5L2iHb"
PLAYLIST = f"https://www.youtube.com/playlist?list={PLAYLIST_ID}"


def test_flat_extraction_still_returns_entries_carrying_video_ids() -> None:
    """The keys `_require_playlist_ids` indexes into.

    The playlist tests run from a built fixture, and a built fixture answers
    every question except whether yt-dlp still produces that shape. Shape and
    presence only, as everywhere in this file: the lecture count would fail
    whenever MIT reorganises the course.
    """
    info = youtube._extract_flat_info(PLAYLIST)

    assert isinstance(info, dict)
    assert "entries" in info, "flat extraction no longer returns 'entries'"

    entries = list(info["entries"])
    assert entries, "the 6.006 playlist came back with no videos at all"
    for entry in entries[:5]:
        assert isinstance(entry, dict)
        identifier = entry.get("id")
        assert isinstance(identifier, str) and len(identifier) == 11, (
            f"a flat entry no longer carries an 11-character 'id' ({identifier!r})"
        )


def test_a_share_link_is_read_as_one_video_not_its_playlist() -> None:
    """The share button emits ``watch?v=…&list=…``, and the README says that
    names one video. `_is_collection` already rules it a single lecture; this
    asks whether the *transport* agrees. Without ``noplaylist`` it does not:
    yt-dlp's default reads the same URL as the whole playlist — one slow full
    extraction whose result carries no caption keys, surfacing here as
    `TransportContractChanged` rather than a manifest.
    """
    share = f"https://www.youtube.com/watch?v={LECTURE}&list={PLAYLIST_ID}"

    manifest = youtube.YouTubeProvider().list(share)

    assert manifest.meta.source_id == LECTURE


#: A recent public talk, for the one thing MIT 6.006 cannot show. Its automatic
#: track was produced by the recogniser YouTube runs *now* — punctuated, cased,
#: word timed — where the 2011 lecture's has two full stops in thirty-five
#: thousand characters. Every fidelity property in `test_fidelity.py` is
#: asserted against a synthetic stand-in for this; here is where the stand-in is
#: checked against the real thing.
#:
#: Nothing depends on this particular video. Any recently uploaded talk with
#: automatic English captions will do, and swapping it is the right repair if it
#: is ever taken down.
RECENT = "D7_ipDqhtwk"


def test_platform_captions_are_still_punctuated_prose() -> None:
    """The assumption that expired, and the one most likely to expire again.

    `TrustTier.assume_punctuated` used to answer this from the tier, and was
    wrong for years without anything noticing, because the only automatic
    fixture in the suite predates the change. `policy_for` now measures it —
    and a measurement can be wrong in the other direction too, so this is the
    check that the thing being measured still looks the way it did.
    """
    handle = (
        youtube.YouTubeProvider().list(RECENT).find(["en"], [TrustTier.ASR_PLATFORM])
    )
    lecture = handle.fetch()

    assert handle.track.tier is TrustTier.ASR_PLATFORM
    text = lecture.text
    assert text.count(".") > len(text.split()) / 100, (
        "the automatic track is no longer punctuated — `looks_punctuated` will "
        "fall back to the tier, and paragraphs will stop aligning to sentences"
    )


def test_a_stamp_still_lands_where_a_sentence_starts() -> None:
    """The property the product promises, against a live lecture.

    A synthetic fixture cannot notice YouTube changing its recogniser, which
    is exactly how the defect this test exists for survived a suite of seven
    hundred tests at full coverage.
    """
    lecture = (
        youtube.YouTubeProvider()
        .list(RECENT)
        .find(["en"], [TrustTier.ASR_PLATFORM])
        .fetch()
    )
    passages = lecture.passages

    orphans = [
        passage.text[:60]
        for previous, passage in pairwise(passages)
        if not passage.turn and not ends_sentence(previous.text)
    ]
    assert orphans == [], f"{len(orphans)} paragraphs begin mid-sentence"

    longest = sorted(passage.end - passage.start for passage in passages)
    assert longest[int(len(longest) * 0.95)] <= 60.0
