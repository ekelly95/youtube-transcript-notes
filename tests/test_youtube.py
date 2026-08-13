"""YouTube provider tests, driven entirely by a captured `extract_info` dict.

No test here touches the network — `conftest.block_network` would fail it if
one tried. The provider takes its extractor and its URL opener as arguments
precisely so that this is possible.
"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

import pytest

from conftest import FIXTURES, load_caption
from youtube_transcript_notes import TranscriptFetcher
from youtube_transcript_notes.cache import Cache, NullCache
from youtube_transcript_notes.errors import (
    AcquisitionFailed,
    AgeRestricted,
    LectureUnavailable,
    MalformedCaptions,
    NoCaptionsAvailable,
    PayloadTooLarge,
    PlaylistEmpty,
    PlaylistNotSupported,
    PlaylistTooLarge,
    RegionBlocked,
    TrackNotFound,
    TransportContractChanged,
)
from youtube_transcript_notes.limits import MAX_PAYLOAD_BYTES, MAX_PLAYLIST_ITEMS
from youtube_transcript_notes.models import TrustTier
from youtube_transcript_notes.render import get_renderer
from youtube_transcript_notes.sources import (
    LocalProvider,
    YouTubeProvider,
    provider_for,
)
from youtube_transcript_notes.sources.youtube import YouTubeTrackRef, _classify

INFO_PATH = FIXTURES / "youtube" / "HtSuA80QTyo.info.json"
VIDEO_ID = "HtSuA80QTyo"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.fixture(scope="module")
def info() -> dict:
    return json.loads(INFO_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def provider(info: dict) -> YouTubeProvider:
    """A provider wired to captured data instead of to YouTube."""
    return YouTubeProvider(
        extractor=lambda url: info,
        opener=lambda url: load_caption("mit6006-lec1.manual.en.json3"),
    )


class TestCitationMetadata:
    """Without this half, a transcript cannot be referenced in written work."""

    def test_everything_needed_to_cite_the_lecture_is_captured(
        self, provider: YouTubeProvider
    ) -> None:
        from datetime import date

        meta = provider.list(WATCH_URL).meta

        assert meta.source_id == VIDEO_ID
        assert meta.title == "Lecture 1: Algorithmic Thinking, Peak Finding"
        assert meta.channel == "MIT OpenCourseWare"
        assert meta.published == date(2013, 1, 14)
        assert meta.duration == 3201.0
        assert meta.url == WATCH_URL

    def test_chapters_come_through(self, provider: YouTubeProvider) -> None:
        chapters = provider.list(WATCH_URL).meta.chapters

        assert len(chapters) == 9
        assert chapters[0].title == "Intro"
        assert chapters[0].start == 0.0
        assert chapters[0].end == 275.0

    def test_a_missing_upload_date_is_not_fatal(self, info: dict) -> None:
        stripped = {**info, "upload_date": None, "release_date": None}
        meta = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL).meta

        assert meta.published is None

    def test_a_malformed_upload_date_is_not_fatal(self, info: dict) -> None:
        stripped = {**info, "upload_date": "not-a-date"}
        meta = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL).meta

        assert meta.published is None

    def test_a_video_with_no_title_falls_back_to_its_id(self, info: dict) -> None:
        stripped = {**info, "title": None}
        meta = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL).meta

        assert meta.title == VIDEO_ID


class TestTrustTiers:
    """Telling a transcript from a translation of a transcription."""

    def test_human_written_tracks_are_manual(self, provider: YouTubeProvider) -> None:
        tracks = [h.track for h in provider.list(WATCH_URL)]
        manual = {t.raw_language for t in tracks if t.tier is TrustTier.MANUAL}

        assert manual == {"ar", "en", "zh-CN"}

    def test_automatic_captions_in_the_spoken_language_are_asr(
        self, provider: YouTubeProvider
    ) -> None:
        tracks = [h.track for h in provider.list(WATCH_URL)]
        asr = {t.raw_language for t in tracks if t.tier is TrustTier.ASR_PLATFORM}

        # The video is in English; both the plain and the -orig automatic
        # English tracks are transcriptions rather than translations.
        assert asr == {"en", "en-orig"}

    def test_the_other_156_automatic_languages_are_translations(
        self, provider: YouTubeProvider
    ) -> None:
        tracks = [h.track for h in provider.list(WATCH_URL)]
        translated = {t.raw_language for t in tracks if t.tier is TrustTier.TRANSLATED}

        assert len(translated) == 155
        assert "fr" in translated
        assert "en" not in translated

    def test_a_video_with_no_declared_language_still_lists_tracks(
        self, info: dict
    ) -> None:
        stripped = {**info, "language": None}
        manifest = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

        assert len(manifest) > 0

    def test_the_original_track_is_asr_even_with_no_declared_language(
        self, info: dict
    ) -> None:
        """The `-orig` marker is yt-dlp saying which track the rest were
        translated from, so it settles the question on its own.

        Deriving the same answer by comparing against the video's declared
        language only works while there *is* one. Without this, a video that
        declares nothing has its single genuine transcription filed as
        `translated` — the least trustworthy tier, for the best automatic
        track available — and the resolver then prefers a machine translation
        of it over the thing itself.
        """
        stripped = {**info, "language": None}
        tracks = [
            h.track
            for h in YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)
        ]

        original = [t for t in tracks if t.raw_language == "en-orig"]

        assert original
        assert all(t.tier is TrustTier.ASR_PLATFORM for t in original)

    def test_the_original_track_outranks_the_translations_without_a_language(
        self, info: dict
    ) -> None:
        """What the tier is actually for. A caller asking for English gets the
        transcription, not a translation that happens to be labelled `en`."""
        stripped = {**info, "language": None, "subtitles": {}}
        manifest = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

        assert manifest.find(["en"]).track.tier is TrustTier.ASR_PLATFORM


class TestTrackSelection:
    def test_the_default_choice_is_human_written_json3(
        self, provider: YouTubeProvider
    ) -> None:
        track = provider.list(WATCH_URL).find(["en"]).track

        assert track.tier is TrustTier.MANUAL
        assert track.caption_format == "json3"
        assert track.raw_language == "en"

    def test_formats_we_cannot_parse_are_not_offered(
        self, provider: YouTubeProvider
    ) -> None:
        # YouTube also publishes srv1, srv2, srv3 and ttml.
        formats = {h.track.caption_format for h in provider.list(WATCH_URL)}
        assert formats == {"json3", "vtt", "srt"}

    def test_asking_for_a_translation_gets_one(self, provider: YouTubeProvider) -> None:
        track = provider.list(WATCH_URL).find(["fr"]).track

        assert track.tier is TrustTier.TRANSLATED
        assert track.language == "fr"

    def test_an_unavailable_language_lists_alternatives_without_flooding(
        self, provider: YouTubeProvider
    ) -> None:
        manifest = provider.list(WATCH_URL)
        listing = manifest.describe_tracks()

        # Nearly 500 tracks exist. The listing stays readable, and says how
        # many it left out rather than truncating silently.
        assert len(manifest) > 400
        assert len(listing) == 13
        assert listing[-1].startswith("... and ")

        with pytest.raises(TrackNotFound) as caught:
            manifest.find(["xx-nonexistent"])

        assert "... and " in str(caught.value)

    def test_human_written_tracks_are_listed_first(
        self, provider: YouTubeProvider
    ) -> None:
        # Truncation keeps whatever comes first, so the useful tracks have to
        # be at the front.
        listing = provider.list(WATCH_URL).describe_tracks()

        assert all("manual" in line for line in listing[:3])

    def test_a_video_with_no_captions_at_all(self, info: dict) -> None:
        stripped = {**info, "subtitles": {}, "automatic_captions": {}}

        with pytest.raises(NoCaptionsAvailable):
            YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)


class TestSourceRecognition:
    @pytest.mark.parametrize(
        "source",
        [
            WATCH_URL,
            "https://youtu.be/HtSuA80QTyo",
            "https://www.youtube-nocookie.com/embed/HtSuA80QTyo",
            VIDEO_ID,
        ],
    )
    def test_recognised_forms(self, source: str) -> None:
        assert YouTubeProvider.handles(source)

    @pytest.mark.parametrize(
        "source",
        [
            "youtube.com/watch?v=HtSuA80QTyo",  # scheme left off, still a URL
            "https://m.youtube.com/watch?v=HtSuA80QTyo",
            "https://music.youtube.com/watch?v=HtSuA80QTyo",
            "HTTPS://WWW.YOUTUBE.COM/watch?v=HtSuA80QTyo",
        ],
    )
    def test_other_shapes_of_the_same_address(self, source: str) -> None:
        assert YouTubeProvider.handles(source)

    @pytest.mark.parametrize("source", ["/home/me/lectures", "notes.vtt", "hello"])
    def test_unrecognised_forms(self, source: str) -> None:
        assert not YouTubeProvider.handles(source)

    @pytest.mark.parametrize(
        "source",
        [
            "https://notyoutube.com.evil/watch?v=HtSuA80QTyo",
            "https://elsewhere.invalid/youtube.com/watch?v=HtSuA80QTyo",
            "https://youtube.com.attacker.test/x",
            "./lectures/youtu.be-mirror/lec1.vtt",
        ],
    )
    def test_an_address_that_merely_mentions_youtube_is_not_youtube(
        self, source: str
    ) -> None:
        """The host is matched, not searched for.

        A substring test hands every one of these to the extractor, which is a
        strange thing to do with an address nobody at YouTube controls.
        """
        assert not YouTubeProvider.handles(source)

    def test_an_unparseable_url_is_simply_not_ours(self) -> None:
        assert not YouTubeProvider.handles("https://[::1")


class TestCollections:
    @pytest.mark.parametrize(
        "source",
        [
            "https://www.youtube.com/playlist?list=PLUl4u3cNGP61Oq3tWYp6V_F-5jb5L2iHb",
            "https://www.youtube.com/@MITOCW",
            "https://www.youtube.com/channel/UCEBb1b_L6zDS3xTUrIALZOw",
            "https://www.youtube.com/c/mitocw",
            "https://www.youtube.com/user/MIT",
            "https://www.youtube.com/results?search_query=algorithms",
        ],
    )
    def test_a_collection_says_so_rather_than_claiming_no_captions(
        self, source: str, info: dict
    ) -> None:
        """Without this the extractor returns a playlist carrying no caption
        tracks of its own, and the user is told the lecture has no captions —
        true of the playlist, and no help at all."""
        with pytest.raises(PlaylistNotSupported) as caught:
            YouTubeProvider(extractor=lambda url: info).list(source)

        assert caught.value.remedy["code"] == "PLAYLIST_NOT_SUPPORTED"
        assert "expanded into its videos automatically" in str(caught.value)

    def test_a_video_that_happens_to_sit_in_a_playlist_still_works(
        self, info: dict
    ) -> None:
        """What YouTube's own share button produces. Rejecting every URL that
        carries `list=` would reject the most common thing anyone pastes."""
        source = f"{WATCH_URL}&list=PLUl4u3cNGP61Oq3tWYp6V_F-5jb5L2iHb&index=1"
        manifest = YouTubeProvider(extractor=lambda url: info).list(source)

        assert len(manifest) > 400

    @pytest.mark.parametrize(
        "source",
        [
            f"https://youtu.be/{VIDEO_ID}?list=PLUl4u3cNGP61Oq3tWYp6V_F-5jb5L2iHb",
            f"https://youtu.be/{VIDEO_ID}?list=PL123&t=42",
            f"https://www.youtube.com/shorts/{VIDEO_ID}?list=PL123",
            f"https://www.youtube.com/live/{VIDEO_ID}?list=PL123",
            f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}?list=PL123",
            f"https://www.youtube.com/v/{VIDEO_ID}?list=PL123",
        ],
    )
    def test_a_video_named_by_its_path_is_not_a_playlist(
        self, source: str, info: dict
    ) -> None:
        """The share-button case again, for every address that puts the video
        id in the path instead of in `v=`.

        `youtu.be/<id>?list=PL...` is exactly what "Share" produces for a video
        sitting in a playlist, and it has no `v=` parameter at all — so a rule
        of "carries `list`, carries no `v`, therefore a collection" reads the
        single most commonly pasted link as a playlist and refuses it.
        """
        manifest = YouTubeProvider(extractor=lambda url: info).list(source)

        assert len(manifest) > 400

    @pytest.mark.parametrize(
        "source",
        [
            "https://youtu.be/?list=PL123",
            "https://www.youtube.com/shorts/?list=PL123",
            "https://www.youtube.com/embed/videoseries?list=PL123",
            "https://www.youtube.com/?list=PLUl4u3cNGP61Oq3tWYp6V_F-5jb5L2iHb",
        ],
    )
    def test_a_path_with_no_video_id_in_it_is_still_a_collection(
        self, source: str
    ) -> None:
        """The exemption is for addresses that name a video, not for every
        address shaped vaguely like one.

        `embed/videoseries?list=` is YouTube's own way of embedding a whole
        playlist, and `videoseries` is not an eleven-character video id — so
        the id shape is what the check has to test, not merely the presence of
        a path segment.
        """

        def explode(url: str) -> dict:
            raise AssertionError("a collection URL was sent to the extractor")

        with pytest.raises(PlaylistNotSupported):
            YouTubeProvider(extractor=explode).list(source)

    def test_a_collection_never_reaches_the_extractor(self) -> None:
        def explode(url: str) -> dict:
            raise AssertionError("a playlist URL was sent to the extractor")

        with pytest.raises(PlaylistNotSupported):
            YouTubeProvider(extractor=explode).list(
                "https://www.youtube.com/playlist?list=PL123"
            )

    def test_a_url_is_chosen_over_the_local_provider(self) -> None:
        assert isinstance(provider_for(WATCH_URL), YouTubeProvider)

    def test_an_existing_path_still_goes_to_the_local_provider(
        self, tmp_path: Path
    ) -> None:
        assert isinstance(provider_for(str(tmp_path)), LocalProvider)

    def test_a_file_named_like_a_video_id_is_read_from_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ambiguous case, which decides the registration order.

        Both providers claim this string — it exists on disk *and* it is
        eleven legal video-ID characters. Local wins because the file is the
        stronger evidence. Asserted through `provider_for` rather than
        through `providers.names()`, so reordering the imports in
        `sources/__init__.py` fails here instead of being waved through by
        editing an expected tuple.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / VIDEO_ID).write_text("", encoding="utf-8")

        assert YouTubeProvider.handles(VIDEO_ID)
        assert LocalProvider.handles(VIDEO_ID)
        assert isinstance(provider_for(VIDEO_ID), LocalProvider)


PLAYLIST_ID = "PLUl4u3cNGP61Oq3tWYp6V_F-5jb5L2iHb"
PLAYLIST_URL = f"https://www.youtube.com/playlist?list={PLAYLIST_ID}"
COURSE_IDS = ("HtSuA80QTyo", "ZA-tUyM_y7s", "Zc54gFhdpLA")


def flat_playlist(*ids: str) -> dict:
    """The shape yt-dlp's flat extraction returns, reduced to what is read."""
    return {
        "id": PLAYLIST_ID,
        "title": "6.006 Introduction to Algorithms",
        "entries": [
            {
                "id": video,
                "title": f"Lecture {n}",
                "url": f"https://www.youtube.com/watch?v={video}",
            }
            for n, video in enumerate(ids, start=1)
        ],
    }


def expander(*ids: str, **kwargs) -> YouTubeProvider:
    """A provider whose flat extraction is served from a built fixture."""
    return YouTubeProvider(flat_extractor=lambda url: flat_playlist(*ids), **kwargs)


class TestPlaylistExpansion:
    def test_a_playlist_expands_to_its_videos_in_order(self) -> None:
        expansion = expander(*COURSE_IDS).expand(PLAYLIST_URL)

        assert expansion.sources == tuple(
            f"https://www.youtube.com/watch?v={video}" for video in COURSE_IDS
        )
        assert expansion.origin == PLAYLIST_URL
        assert expansion.stale_reason is None

    @pytest.mark.parametrize(
        "source",
        [
            "https://youtu.be/?list=PL123",
            "https://www.youtube.com/shorts/?list=PL123",
            "https://www.youtube.com/embed/videoseries?list=PL123",
            "https://www.youtube.com/?list=PL123",
        ],
    )
    def test_every_form_that_names_only_a_playlist_expands(self, source: str) -> None:
        """The same four forms `list()` refuses as collections. Refusal was
        the best available answer while expansion did not exist; now they
        mean the playlist, and the playlist is expandable."""
        expansion = expander(*COURSE_IDS).expand(source)

        assert len(expansion.sources) == len(COURSE_IDS)
        assert expansion.origin == source

    def test_expanding_costs_one_flat_call_and_fetches_nothing(self) -> None:
        calls: list[str] = []

        def flat(url: str) -> dict:
            calls.append(url)
            return flat_playlist(*COURSE_IDS)

        def explode(url: str) -> dict:
            raise AssertionError("expansion visited a video")

        provider = YouTubeProvider(
            flat_extractor=flat, extractor=explode, opener=explode
        )
        provider.expand(PLAYLIST_URL)

        assert calls == [PLAYLIST_URL]

    @pytest.mark.parametrize(
        "source",
        [
            # Channels and searches: collections, but not bounded lists.
            "https://www.youtube.com/@MITOCW",
            "https://www.youtube.com/channel/UCEBb1b_L6zDS3xTUrIALZOw",
            "https://www.youtube.com/c/mitocw",
            "https://www.youtube.com/user/MIT",
            "https://www.youtube.com/results?search_query=algorithms",
            # Single lectures, however they happen to be addressed.
            WATCH_URL,
            f"{WATCH_URL}&list={PLAYLIST_ID}&index=1",
            f"https://youtu.be/{VIDEO_ID}?list={PLAYLIST_ID}",
            VIDEO_ID,
        ],
    )
    def test_everything_that_is_not_a_playlist_passes_through_untouched(
        self, source: str
    ) -> None:
        """Channels stay refused (by `list`, which these never reach past),
        and the share-button forms stay single lectures — expanding
        `watch?v=ID&list=...` would turn the most commonly pasted URL into a
        fifty-video run nobody asked for."""

        def explode(url: str) -> dict:
            raise AssertionError("a non-playlist source reached the flat extractor")

        expansion = YouTubeProvider(flat_extractor=explode).expand(source)

        assert expansion.sources == (source,)
        assert expansion.origin is None
        assert expansion.stale_reason is None

    def test_exactly_the_ceiling_expands(self) -> None:
        ids = tuple(f"Video{n:06d}" for n in range(MAX_PLAYLIST_ITEMS))

        expansion = expander(*ids).expand(PLAYLIST_URL)

        assert len(expansion.sources) == MAX_PLAYLIST_ITEMS

    def test_one_past_the_ceiling_refuses_the_whole_playlist(self) -> None:
        ids = tuple(f"Video{n:06d}" for n in range(MAX_PLAYLIST_ITEMS + 1))

        with pytest.raises(PlaylistTooLarge) as caught:
            expander(*ids).expand(PLAYLIST_URL)

        assert caught.value.remedy["code"] == "PLAYLIST_TOO_LARGE"
        assert str(MAX_PLAYLIST_ITEMS) in str(caught.value)

    def test_the_ceiling_holds_even_when_the_far_end_over_delivers(self) -> None:
        """`playlistend` is a request to yt-dlp, not a guarantee. The read is
        capped on this side too, so an ignored request costs a bounded read
        and an honest refusal rather than an unbounded expansion."""
        ids = tuple(f"Video{n:06d}" for n in range(MAX_PLAYLIST_ITEMS + 300))

        with pytest.raises(PlaylistTooLarge):
            expander(*ids).expand(PLAYLIST_URL)

    def test_lazily_produced_entries_are_read_the_same_way(self) -> None:
        """yt-dlp hands back a generator for large playlists; a list is not
        part of the contract and must not be required."""
        listed = flat_playlist(*COURSE_IDS)
        lazy = {**listed, "entries": iter(listed["entries"])}

        expansion = YouTubeProvider(flat_extractor=lambda url: lazy).expand(
            PLAYLIST_URL
        )

        assert len(expansion.sources) == len(COURSE_IDS)

    def test_an_empty_playlist_says_so(self) -> None:
        with pytest.raises(PlaylistEmpty) as caught:
            expander().expand(PLAYLIST_URL)

        assert caught.value.remedy["code"] == "PLAYLIST_EMPTY"

    def test_a_playlist_with_no_id_in_its_url_still_expands(
        self, tmp_path: Path
    ) -> None:
        """A bare `/playlist` path names no playlist id, so there is nothing
        to key a cache entry on — expansion works, and simply writes none."""
        cache = Cache(tmp_path)
        expansion = expander(*COURSE_IDS, cache=cache).expand(
            "https://www.youtube.com/playlist"
        )

        assert len(expansion.sources) == len(COURSE_IDS)
        assert list(tmp_path.rglob("*.json")) == []

    def test_the_flat_client_names_videos_without_visiting_them(self) -> None:
        """The options that make enumeration cost one request — and their
        absence from the single-video client, whose dict must not pick them
        up by accident."""
        from youtube_transcript_notes.sources.youtube import _youtube_dl

        flat = _youtube_dl(flat=True).params
        assert flat["extract_flat"] == "in_playlist"
        assert flat["playlistend"] == MAX_PLAYLIST_ITEMS + 1

        single = _youtube_dl().params
        assert "extract_flat" not in single
        assert "playlistend" not in single

    def test_a_share_link_names_one_video_to_the_transport_too(self) -> None:
        """`watch?v=X&list=PL…` is one lecture — `_is_collection` says so —
        and the transport has to agree. Without `noplaylist`, yt-dlp's default
        reads the same URL as the whole playlist: a slow full extraction whose
        result carries no caption keys, reported as a moved contract."""
        from youtube_transcript_notes.sources.youtube import _youtube_dl

        assert _youtube_dl().params["noplaylist"] is True
        assert _youtube_dl(flat=True).params["noplaylist"] is True

    def test_the_flat_extractor_builds_a_flat_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from youtube_transcript_notes.sources import youtube

        built: list[bool] = []

        class FakeYoutubeDL:
            def extract_info(self, url, download=False):
                return flat_playlist(*COURSE_IDS)

        def factory(flat: bool = False):
            built.append(flat)
            return FakeYoutubeDL()

        monkeypatch.setattr(youtube, "_youtube_dl", factory)

        assert youtube._extract_flat_info(PLAYLIST_URL)["id"] == PLAYLIST_ID
        assert built == [True]

    def test_a_flat_extraction_failure_is_classified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from youtube_transcript_notes.sources import youtube

        class FakeYoutubeDL:
            def extract_info(self, url, download=False):
                raise RuntimeError("Video unavailable")

        monkeypatch.setattr(youtube, "_youtube_dl", lambda flat=False: FakeYoutubeDL())

        with pytest.raises(LectureUnavailable):
            youtube._extract_flat_info(PLAYLIST_URL)


class TestPlaylistContract:
    """The flat extraction's shape, held to `_require_shape`'s standard."""

    def test_an_extractor_returning_nothing(self) -> None:
        with pytest.raises(TransportContractChanged):
            YouTubeProvider(flat_extractor=lambda url: None).expand(PLAYLIST_URL)

    def test_an_extractor_returning_the_wrong_type(self) -> None:
        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(flat_extractor=lambda url: ["not", "a", "dict"]).expand(
                PLAYLIST_URL
            )

        assert "list" in caught.value.cause

    def test_entries_missing_entirely_is_not_an_empty_playlist(self) -> None:
        """The boundary again: absent must never read as empty. A renamed
        `entries` key reported as `PlaylistEmpty` sends the reader to check a
        playlist that is fine."""
        info = {
            key: value for key, value in flat_playlist().items() if key != "entries"
        }

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(flat_extractor=lambda url: info).expand(PLAYLIST_URL)

        assert "'entries'" in caught.value.cause

    @pytest.mark.parametrize("entries", ["not-a-sequence", b"bytes", {"a": 1}, 42])
    def test_entries_of_the_wrong_shape(self, entries: object) -> None:
        info = {**flat_playlist(), "entries": entries}

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(flat_extractor=lambda url: info).expand(PLAYLIST_URL)

        assert "sequence of videos" in caught.value.cause

    def test_an_entry_that_is_not_an_object_names_its_position(self) -> None:
        info = flat_playlist(*COURSE_IDS)
        info["entries"][1] = "not-an-entry"

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(flat_extractor=lambda url: info).expand(PLAYLIST_URL)

        assert "entry 2" in caught.value.cause

    @pytest.mark.parametrize("identifier", [None, "", 123, "too-short", "videoseries"])
    def test_an_entry_with_no_usable_id(self, identifier: object) -> None:
        """`videoseries` is eleven legal characters and still not a video —
        the same by-name exclusion `_is_video_id` applies everywhere else."""
        info = flat_playlist(*COURSE_IDS)
        info["entries"][0]["id"] = identifier

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(flat_extractor=lambda url: info).expand(PLAYLIST_URL)

        assert "entry 1" in caught.value.cause


class TestSurvivingAnOutageWhenExpanding:
    """The manifest cache's promise, extended to playlists: a course you
    expanded last week still expands while the transport is down."""

    @staticmethod
    def _broken(url: str) -> dict:
        raise AcquisitionFailed(source=url, detail="the transport is down")

    def _warm(self, cache: Cache, *ids: str) -> None:
        expander(*(ids or COURSE_IDS), cache=cache).expand(PLAYLIST_URL)

    def test_a_known_playlist_still_expands_when_the_transport_is_down(
        self, tmp_path: Path
    ) -> None:
        cache = Cache(tmp_path)
        self._warm(cache)

        expansion = YouTubeProvider(flat_extractor=self._broken, cache=cache).expand(
            PLAYLIST_URL
        )

        assert expansion.sources == tuple(
            f"https://www.youtube.com/watch?v={video}" for video in COURSE_IDS
        )

    def test_falling_back_is_never_silent(self, tmp_path: Path) -> None:
        cache = Cache(tmp_path)
        self._warm(cache)

        expansion = YouTubeProvider(flat_extractor=self._broken, cache=cache).expand(
            PLAYLIST_URL
        )

        assert isinstance(expansion.stale_reason, AcquisitionFailed)
        assert "the transport is down" in expansion.stale_reason.cause

    def test_a_contract_change_falls_back_too(self, tmp_path: Path) -> None:
        """The same reasoning as `list`: a transport that no longer speaks
        the language is as unreachable as one that is down."""
        cache = Cache(tmp_path)
        self._warm(cache)

        moved = YouTubeProvider(
            flat_extractor=lambda url: {"no": "entries"}, cache=cache
        )
        expansion = moved.expand(PLAYLIST_URL)

        assert len(expansion.sources) == len(COURSE_IDS)
        assert isinstance(expansion.stale_reason, TransportContractChanged)

    def test_a_playlist_never_seen_before_still_fails(self, tmp_path: Path) -> None:
        with pytest.raises(AcquisitionFailed):
            YouTubeProvider(flat_extractor=self._broken, cache=Cache(tmp_path)).expand(
                PLAYLIST_URL
            )

    def test_a_playlist_with_no_id_is_not_looked_up(self, tmp_path: Path) -> None:
        with pytest.raises(AcquisitionFailed):
            YouTubeProvider(flat_extractor=self._broken, cache=Cache(tmp_path)).expand(
                "https://www.youtube.com/playlist"
            )

    @pytest.mark.parametrize(
        "stored",
        [
            "{not json at all",
            '{"nothing": "here"}',
            '{"videos": "not-a-list"}',
            '{"videos": ["HtSuA80QTyo", 123]}',
            '{"videos": []}',
        ],
    )
    def test_an_unreadable_entry_yields_to_the_real_failure(
        self, tmp_path: Path, stored: str
    ) -> None:
        """Including the empty roster: one is never written, so one read back
        is corruption — and serving it would make the playlist quietly vanish
        from the run."""
        cache = Cache(tmp_path)
        provider = YouTubeProvider(flat_extractor=self._broken, cache=cache)
        cache.write(provider._expansion_key(PLAYLIST_ID), stored)

        with pytest.raises(AcquisitionFailed):
            provider.expand(PLAYLIST_URL)

    def test_an_empty_playlist_does_not_resurrect_last_weeks_roster(
        self, tmp_path: Path
    ) -> None:
        """Emptiness is a fact learned by *reaching* the playlist, the same
        line `NoCaptionsAvailable` draws for captions. Only unreachable falls
        back."""
        cache = Cache(tmp_path)
        self._warm(cache)

        with pytest.raises(PlaylistEmpty):
            expander(cache=cache).expand(PLAYLIST_URL)

    def test_an_oversized_playlist_does_not_either(self, tmp_path: Path) -> None:
        cache = Cache(tmp_path)
        self._warm(cache)
        ids = tuple(f"Video{n:06d}" for n in range(MAX_PLAYLIST_ITEMS + 1))

        with pytest.raises(PlaylistTooLarge):
            expander(*ids, cache=cache).expand(PLAYLIST_URL)

    def test_a_refused_playlist_is_never_cached(self, tmp_path: Path) -> None:
        """A refusal that landed in the cache would be served as a roster
        after the next outage."""
        for bad in (expander(), expander(*(f"Video{n:06d}" for n in range(501)))):
            bad.cache = Cache(tmp_path)
            with pytest.raises((PlaylistEmpty, PlaylistTooLarge)):
                bad.expand(PLAYLIST_URL)

        assert list(tmp_path.rglob("*.json")) == []


class TestFetching:
    def test_discovery_downloads_no_captions(self, info: dict) -> None:
        def explode(url: str) -> str:
            raise AssertionError("list() downloaded a caption payload")

        manifest = YouTubeProvider(extractor=lambda url: info, opener=explode).list(
            WATCH_URL
        )
        assert len(manifest) > 400

    def test_exactly_one_metadata_call_per_discovery(self, info: dict) -> None:
        calls: list[str] = []

        def extractor(url: str) -> dict:
            calls.append(url)
            return info

        YouTubeProvider(extractor=extractor).list(WATCH_URL)
        assert calls == [WATCH_URL]

    def test_a_bare_video_id_becomes_a_watch_url(self, info: dict) -> None:
        calls: list[str] = []

        def extractor(url: str) -> dict:
            calls.append(url)
            return info

        YouTubeProvider(extractor=extractor).list(VIDEO_ID)
        assert calls == [WATCH_URL]

    def test_a_fetched_lecture_is_complete(self, provider: YouTubeProvider) -> None:
        lecture = provider.list(WATCH_URL).find(["en"]).fetch()

        assert lecture.meta.title.startswith("Lecture 1")
        assert lecture.provenance.provider == "youtube"
        assert lecture.provenance.tier is TrustTier.MANUAL
        assert len(lecture.passages) == 92

    def test_chapters_become_sections(self, provider: YouTubeProvider) -> None:
        lecture = provider.list(WATCH_URL).find(["en"]).fetch()

        assert [section.title for section in lecture.sections][:3] == [
            "Intro",
            "Class Overview",
            "Content",
        ]

    def test_the_notes_carry_working_deep_links(
        self, provider: YouTubeProvider
    ) -> None:
        lecture = provider.list(WATCH_URL).find(["en"]).fetch()
        notes = get_renderer("markdown").render(lecture)

        assert "## Intro" in notes
        assert f"https://www.youtube.com/watch?v={VIDEO_ID}&t=" in notes

    def test_the_citation_names_the_lecture_properly(
        self, provider: YouTubeProvider
    ) -> None:
        lecture = provider.list(WATCH_URL).find(["en"]).fetch()
        citation = get_renderer("citation").render(lecture)

        assert citation.startswith(
            "MIT OpenCourseWare. (2013, January 14). "
            "Lecture 1: Algorithmic Thinking, Peak Finding [Video]. YouTube."
        )


class TestCaching:
    def test_a_second_fetch_costs_nothing(self, info: dict, tmp_path: Path) -> None:
        calls: list[str] = []

        def opener(url: str) -> str:
            calls.append(url)
            return load_caption("mit6006-lec1.manual.en.json3")

        def build() -> YouTubeProvider:
            return YouTubeProvider(
                extractor=lambda url: info,
                opener=opener,
                cache=Cache(tmp_path / "cache"),
            )

        build().list(WATCH_URL).find(["en"]).fetch()
        assert len(calls) == 1

        # A fresh provider, so nothing is held in memory between the two.
        second = build().list(WATCH_URL).find(["en"]).fetch()
        assert len(calls) == 1
        assert second.provenance.content_hash

    def test_cache_keys_ignore_the_expiring_url(self) -> None:
        # YouTube caption URLs are signed and change every request; keying on
        # them would mean never getting a hit.
        first = YouTubeTrackRef(VIDEO_ID, "en", TrustTier.MANUAL, "json3", "url-a")
        second = YouTubeTrackRef(VIDEO_ID, "en", TrustTier.MANUAL, "json3", "url-b")

        assert first.cache_key() == second.cache_key()

    def test_cache_keys_separate_tiers_and_formats(self) -> None:
        base = YouTubeTrackRef(VIDEO_ID, "en", TrustTier.MANUAL, "json3", "u")
        other_tier = YouTubeTrackRef(
            VIDEO_ID, "en", TrustTier.ASR_PLATFORM, "json3", "u"
        )
        other_format = YouTubeTrackRef(VIDEO_ID, "en", TrustTier.MANUAL, "vtt", "u")

        assert (
            len({base.cache_key(), other_tier.cache_key(), other_format.cache_key()})
            == 3
        )

    def test_a_partial_write_never_becomes_a_cache_entry(self, tmp_path: Path) -> None:
        cache = Cache(tmp_path)
        cache.write("abc123", "payload")

        assert cache.read("abc123") == "payload"
        assert not list(tmp_path.rglob("*.partial"))

    def test_a_miss_reads_as_absent(self, tmp_path: Path) -> None:
        assert Cache(tmp_path).read("never-written") is None

    def test_the_null_cache_remembers_nothing(self, tmp_path: Path) -> None:
        cache = NullCache()
        cache.write("abc", "payload")

        assert cache.read("abc") is None

    def test_caching_is_off_unless_asked_for(self) -> None:
        assert isinstance(YouTubeProvider().cache, NullCache)


class FakeResponse:
    """A response that reads like a real one: chunked, with headers.

    ``declared`` is deliberately separate from the actual bytes, because the
    interesting cases are the ones where a `Content-Length` disagrees with what
    arrives — omitted, understated, or simply a lie.
    """

    def __init__(self, payload: bytes, declared: object = "omit") -> None:
        self._payload = payload
        self._offset = 0
        self.closed = False
        if declared == "omit":
            self.headers: dict[str, str] = {}
        elif declared is not None:
            self.headers = {"Content-Length": str(declared)}
        else:
            self.headers = {"Content-Length": None}  # type: ignore[dict-item]

    def read(self, amount: int) -> bytes:
        chunk = self._payload[self._offset : self._offset + amount]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class TestFailureClassification:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("ERROR: Video unavailable", LectureUnavailable),
            ("This video is unavailable", LectureUnavailable),
            (
                "Private video. Sign in if you've been granted access",
                LectureUnavailable,
            ),
            ("This video has been removed by the uploader", LectureUnavailable),
            ("Sign in to confirm your age", AgeRestricted),
            ("This video is age-restricted", AgeRestricted),
            (
                "The uploader has not made this video available in your country",
                RegionBlocked,
            ),
        ],
    )
    def test_known_failures_are_named(self, message: str, expected: type) -> None:
        assert isinstance(_classify(VIDEO_ID, RuntimeError(message)), expected)

    def test_an_unrecognised_failure_keeps_its_detail(self) -> None:
        # Better an honest "something went wrong, here is what it said" than a
        # confident wrong guess.
        failure = _classify(VIDEO_ID, RuntimeError("HTTP 503 from the CDN"))

        assert isinstance(failure, AcquisitionFailed)
        assert "HTTP 503 from the CDN" in failure.cause

    def test_a_transport_failure_surfaces_as_a_transcript_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from youtube_transcript_notes.sources import youtube

        class FakeYoutubeDL:
            def extract_info(self, url, download=False):
                raise RuntimeError("Video unavailable")

        monkeypatch.setattr(youtube, "_youtube_dl", FakeYoutubeDL)

        with pytest.raises(LectureUnavailable):
            youtube._extract_info(WATCH_URL)

    def test_a_download_failure_surfaces_as_a_transcript_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from youtube_transcript_notes.sources import youtube

        class FakeYoutubeDL:
            def urlopen(self, url):
                raise RuntimeError("HTTP 403")

        monkeypatch.setattr(youtube, "_youtube_dl", FakeYoutubeDL)

        with pytest.raises(AcquisitionFailed):
            youtube._open_url("https://captions.test/en.json3")

    def test_the_real_client_is_built_quiet_and_download_free(self) -> None:
        # Constructing the client touches nothing; it is the call that would,
        # and this never makes one.
        from youtube_transcript_notes.sources.youtube import _youtube_dl

        params = _youtube_dl().params

        assert params["quiet"] is True
        assert params["skip_download"] is True
        assert params["noprogress"] is True

    def test_a_successful_download_is_decoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from youtube_transcript_notes.sources import youtube

        class FakeYoutubeDL:
            def urlopen(self, url):
                # `read(amt)`, the way a real response has it: the payload is
                # taken in bounded chunks now, not one unbounded call.
                return FakeResponse(b"WEBVTT\n")

        monkeypatch.setattr(youtube, "_youtube_dl", FakeYoutubeDL)

        assert youtube._open_url("https://captions.test/en.vtt") == "WEBVTT\n"


class TestTransportContract:
    """The seam between the tool and yt-dlp, and what happens when it moves.

    This is the class the `<2027` version ceiling used to be. The ceiling
    fired on a calendar boundary, at install time, whether or not anything had
    changed; these fire when the shape actually changes, wherever the tool is
    installed, and name what changed.

    The pair worth reading together is
    `test_caption_keys_present_but_empty_is_the_lecture_s_own_business` and
    `test_caption_keys_missing_entirely_is_not_the_lecture_s_fault`. Every
    other test here protects a boundary; those two *are* the boundary.
    """

    def test_caption_keys_present_but_empty_is_the_lectures_own_business(
        self, info: dict
    ) -> None:
        stripped = {**info, "subtitles": {}, "automatic_captions": {}}

        with pytest.raises(NoCaptionsAvailable):
            YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

    def test_caption_keys_missing_entirely_is_not_the_lectures_fault(
        self, info: dict
    ) -> None:
        """The whole point. Same empty result, entirely different cause.

        Before this existed, a renamed key produced zero tracks and the tool
        announced that the lecture had no captions of any kind — a confident
        statement about a video that has around five hundred.
        """
        stripped = {
            key: value
            for key, value in info.items()
            if key not in {"subtitles", "automatic_captions"}
        }

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

        assert "subtitles" in caught.value.cause
        assert "automatic_captions" in caught.value.cause
        # It must say whose problem this is not.
        assert "not a problem with" in caught.value.cause

    def test_one_caption_key_is_enough_to_be_no_false_alarm(self, info: dict) -> None:
        """Only `subtitles`. Unusual, not broken — and must not cry wolf."""
        stripped = {
            key: value for key, value in info.items() if key != "automatic_captions"
        }
        manifest = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

        assert len(manifest) > 0
        assert {h.track.tier for h in manifest} == {TrustTier.MANUAL}

    def test_an_extractor_returning_nothing(self, info: dict) -> None:
        """yt-dlp really does return None on some paths.

        This used to reach `_meta_from` and raise `AttributeError` from
        *outside* the try block that classifies transport failures, so it
        escaped the taxonomy entirely and a library caller got a raw
        `AttributeError`.
        """
        with pytest.raises(TransportContractChanged):
            YouTubeProvider(extractor=lambda url: None).list(WATCH_URL)

    def test_an_extractor_returning_the_wrong_type(self) -> None:
        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(extractor=lambda url: ["not", "a", "dict"]).list(WATCH_URL)

        assert "list" in caught.value.cause

    def test_metadata_with_no_id(self, info: dict) -> None:
        stripped = {key: value for key, value in info.items() if key != "id"}

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

        assert "'id'" in caught.value.cause

    def test_a_caption_key_of_the_wrong_shape(self, info: dict) -> None:
        """Present, but no longer an object keyed by language."""
        stripped = {**info, "automatic_captions": ["en", "fr"]}

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

        assert "automatic_captions" in caught.value.cause
        assert "list" in caught.value.cause

    def test_tracks_listed_but_none_usable_names_what_was_offered(
        self, info: dict
    ) -> None:
        """The same lie, one level down.

        `_require_shape` proves the caption keys exist. If yt-dlp renamed
        `ext` instead, every entry would be dropped for having no recognised
        format and the manifest would come out empty again — reported, again,
        as a lecture with no captions.
        """
        stripped = {
            **info,
            "subtitles": {},
            "automatic_captions": {
                "en": [{"ext": "srv3", "url": "https://captions.test/en.srv3"}]
            },
        }

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

        assert "srv3" in caught.value.cause
        assert "json3" in caught.value.cause  # what the tool does read

    def test_entries_that_name_no_format_at_all(self, info: dict) -> None:
        stripped = {
            **info,
            "subtitles": {},
            "automatic_captions": {"en": [{"url": "https://captions.test/en"}]},
        }

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

        assert "nothing naming a format" in caught.value.cause

    def test_entries_that_are_not_objects(self, info: dict) -> None:
        stripped = {
            **info,
            "subtitles": {},
            "automatic_captions": {"en": ["not-an-entry"], "fr": None},
        }

        with pytest.raises(TransportContractChanged):
            YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

    def test_the_remedy_is_machine_readable(self, info: dict) -> None:
        """Agents branch on `remedy`, not on English."""
        stripped = {**info, "subtitles": None, "automatic_captions": None}
        stripped = {key: value for key, value in stripped.items() if value is not None}

        with pytest.raises(TransportContractChanged) as caught:
            YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL)

        remedy = caught.value.remedy

        assert remedy["code"] == "TRANSPORT_CONTRACT_CHANGED"
        assert remedy["context"]["version"]
        assert any("pip install -U yt-dlp" in step for step in remedy["try"])


class TestTransportBreakageIsNotTheLecturesFault:
    """yt-dlp's own failures, told apart from the video's."""

    @pytest.mark.parametrize(
        "message",
        [
            (
                "ERROR: unable to extract player response; please report this "
                "issue on https://github.com/yt-dlp/yt-dlp/issues"
            ),
            "Confirm you are on the latest version using yt-dlp -U",
            "Unable to extract yt initial data",
            "Signature extraction failed: Some formats may be missing",
            "nsig extraction failed: Some formats may be missing",
        ],
    )
    def test_a_broken_extractor_says_so(self, message: str) -> None:
        failure = _classify(VIDEO_ID, RuntimeError(message))

        assert isinstance(failure, TransportContractChanged)
        assert any("pip install -U yt-dlp" in step for step in failure.remedy["try"])

    def test_breakage_wins_over_a_message_that_looks_like_a_dead_video(self) -> None:
        """Order matters, and this is why it is written down.

        A broken extractor says all sorts of things on its way down, and some
        of them read exactly like facts about the video. Classifying this as
        `LectureUnavailable` sends the reader to check a video that is fine
        while the actual fix — upgrading yt-dlp — goes unmentioned.
        """
        message = (
            "ERROR: [youtube] HtSuA80QTyo: Video unavailable. "
            "Please report this issue on https://github.com/yt-dlp/yt-dlp/issues"
        )

        assert isinstance(
            _classify(VIDEO_ID, RuntimeError(message)), TransportContractChanged
        )

    def test_a_missing_component_is_not_a_deleted_lecture(self) -> None:
        """The narrowed pattern, pinned.

        `is not available` on its own also matches "ffmpeg is not available",
        and diagnosing a broken install as a deleted lecture is worse than
        admitting the failure was not recognised.
        """
        failure = _classify(VIDEO_ID, RuntimeError("ffmpeg is not available"))

        assert isinstance(failure, AcquisitionFailed)
        assert "ffmpeg" in failure.cause

    def test_the_version_is_reported(self) -> None:
        failure = _classify(VIDEO_ID, RuntimeError("please report this issue"))

        assert failure.remedy["context"]["version"]

    def test_a_missing_transport_still_reports_a_version_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reporting the failure must not become a second failure."""
        from youtube_transcript_notes.sources import youtube

        def absent(name: str) -> str:
            raise importlib.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(youtube.importlib.metadata, "version", absent)

        assert youtube._yt_dlp_version() == "(not installed)"


class TestTransportDataIsNotTrusted:
    """Citation metadata is optional; failing a retrieval over it is not.

    Each of these used to raise a raw `ValueError`, `TypeError` or
    `AttributeError` past the error taxonomy — trading a whole transcript for
    a heading that would not parse.
    """

    def test_an_impossible_upload_date(self, info: dict) -> None:
        # Eight characters, every one a digit, and still not a date. The old
        # `isdigit()` guard passed it straight to `date(0, 0, 0)`.
        stripped = {**info, "upload_date": "00000000", "release_date": None}
        meta = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL).meta

        assert meta.published is None

    def test_an_upload_date_that_is_not_a_string(self, info: dict) -> None:
        stripped = {**info, "upload_date": 20130114, "release_date": None}
        meta = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL).meta

        assert meta.published is None

    @pytest.mark.parametrize(
        "chapters",
        [
            ["just a string"],
            [{"title": "Intro"}],
            [{"title": "Intro", "start_time": None}],
            [{"title": "Intro", "start_time": "not a number"}],
        ],
    )
    def test_a_chapter_that_will_not_read_is_skipped(
        self, info: dict, chapters: list
    ) -> None:
        stripped = {**info, "chapters": chapters}
        meta = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL).meta

        assert meta.chapters == ()
        # The lecture itself survives, which is the point.
        assert meta.title == "Lecture 1: Algorithmic Thinking, Peak Finding"

    def test_a_chapter_with_no_end_is_kept(self, info: dict) -> None:
        stripped = {**info, "chapters": [{"title": "All of it", "start_time": 0}]}
        chapters = (
            YouTubeProvider(extractor=lambda url: stripped)
            .list(WATCH_URL)
            .meta.chapters
        )

        assert len(chapters) == 1
        assert chapters[0].end is None

    @pytest.mark.parametrize("duration", ["not a number", [], {}])
    def test_an_unreadable_duration_is_dropped_not_fatal(
        self, info: dict, duration: object
    ) -> None:
        stripped = {**info, "duration": duration}
        meta = YouTubeProvider(extractor=lambda url: stripped).list(WATCH_URL).meta

        assert meta.duration is None


class TestTransportIsBounded:
    def test_the_client_will_not_wait_forever(self) -> None:
        """There were no timeouts anywhere in this project before this.

        An unbounded wait is indistinguishable from a hang, which matters for
        something run in a terminal and watched.
        """
        from youtube_transcript_notes.sources.youtube import _youtube_dl

        params = _youtube_dl().params

        assert params["socket_timeout"] > 0
        assert params["retries"] >= 1
        assert params["extractor_retries"] >= 1


class TestSurvivingAnOutage:
    """Lectures already fetched keep working when the transport does not.

    Discovery was the one step that always cost a live request: caption
    payloads were cached, but the metadata call that reaches them was not. So
    a broken yt-dlp took out lectures whose captions were already on disk —
    which quietly made `cache.py`'s opening claim untrue, that a transcript
    cited last week still says what you quoted.
    """

    @staticmethod
    def _broken(url: str) -> dict:
        raise AcquisitionFailed(source=url, detail="the transport is down")

    def test_a_cached_lecture_still_lists_when_the_transport_is_down(
        self, info: dict, tmp_path: Path
    ) -> None:
        cache = Cache(tmp_path)
        YouTubeProvider(extractor=lambda url: info, cache=cache).list(WATCH_URL)

        manifest = YouTubeProvider(extractor=self._broken, cache=cache).list(WATCH_URL)

        assert len(manifest) > 400
        assert manifest.meta.title == "Lecture 1: Algorithmic Thinking, Peak Finding"
        assert manifest.meta.channel == "MIT OpenCourseWare"
        assert manifest.meta.chapters

    def test_a_cached_lecture_still_renders_when_the_transport_is_down(
        self, info: dict, tmp_path: Path
    ) -> None:
        """The property that matters in a week when yt-dlp has broken."""
        cache = Cache(tmp_path)
        warm = YouTubeProvider(
            extractor=lambda url: info,
            opener=lambda url: load_caption("mit6006-lec1.manual.en.json3"),
            cache=cache,
        )
        expected = warm.list(WATCH_URL).find(["en"]).fetch()

        offline = YouTubeProvider(extractor=self._broken, cache=cache)
        lecture = offline.list(WATCH_URL).find(["en"]).fetch()

        assert lecture.text == expected.text
        assert lecture.provenance.content_hash == expected.provenance.content_hash

    def test_falling_back_is_never_silent(self, info: dict, tmp_path: Path) -> None:
        """Contract 5, applied to a mode rather than to output.

        A run served from cache looks exactly like a run that reached YouTube.
        The manifest carries why, so the CLI can say so on stderr.
        """
        cache = Cache(tmp_path)
        YouTubeProvider(extractor=lambda url: info, cache=cache).list(WATCH_URL)

        manifest = YouTubeProvider(extractor=self._broken, cache=cache).list(WATCH_URL)

        assert isinstance(manifest.stale_reason, AcquisitionFailed)
        assert "the transport is down" in manifest.stale_reason.cause

    def test_a_live_manifest_is_not_marked_stale(
        self, provider: YouTubeProvider
    ) -> None:
        assert provider.list(WATCH_URL).stale_reason is None

    @pytest.mark.parametrize(
        "addressed_as",
        [WATCH_URL, "https://youtu.be/HtSuA80QTyo", VIDEO_ID],
    )
    def test_one_lecture_is_one_entry_however_it_was_addressed(
        self, info: dict, tmp_path: Path, addressed_as: str
    ) -> None:
        """Warmed via the watch URL, recalled via whatever was pasted."""
        cache = Cache(tmp_path)
        YouTubeProvider(extractor=lambda url: info, cache=cache).list(WATCH_URL)

        manifest = YouTubeProvider(extractor=self._broken, cache=cache).list(
            addressed_as
        )

        assert len(manifest) > 400

    def test_an_uncached_track_reports_the_real_reason(
        self, info: dict, tmp_path: Path
    ) -> None:
        """No payload on disk and no transport is a failure, honestly named.

        The stored manifest deliberately keeps no caption URLs — YouTube signs
        them and they expire in hours — so there is nothing to try. Reporting
        the transport failure that put us in this mode beats a complaint about
        an empty address.
        """
        cache = Cache(tmp_path)
        YouTubeProvider(extractor=lambda url: info, cache=cache).list(WATCH_URL)

        offline = YouTubeProvider(extractor=self._broken, cache=cache)

        with pytest.raises(AcquisitionFailed) as caught:
            offline.list(WATCH_URL).find(["en"]).fetch()

        assert "the transport is down" in caught.value.cause

    def test_a_lecture_never_seen_before_still_fails(self, tmp_path: Path) -> None:
        with pytest.raises(AcquisitionFailed):
            YouTubeProvider(extractor=self._broken, cache=Cache(tmp_path)).list(
                WATCH_URL
            )

    def test_nothing_is_remembered_without_a_cache(self, info: dict) -> None:
        """A library caller gets `NullCache`, so this changes nothing for them."""
        YouTubeProvider(extractor=lambda url: info).list(WATCH_URL)

        with pytest.raises(AcquisitionFailed):
            YouTubeProvider(extractor=self._broken).list(WATCH_URL)

    @pytest.mark.parametrize(
        "source",
        [
            # A YouTube URL naming no video at all.
            "https://www.youtube.com/watch?feature=share",
            # A `v=` that is not a video ID. Eleven characters from the legal
            # alphabet is the only thing that counts as one.
            "https://www.youtube.com/watch?v=too-short",
            # Not a YouTube address in the first place. `list` is public and
            # can be called with anything, so `_recall` must not assume it was
            # only ever reached through `handles`.
            "some/local/path.vtt",
        ],
    )
    def test_a_source_that_names_no_video_is_not_looked_up(
        self, tmp_path: Path, source: str
    ) -> None:
        with pytest.raises(AcquisitionFailed):
            YouTubeProvider(extractor=self._broken, cache=Cache(tmp_path)).list(source)

    def test_an_unreadable_cache_entry_yields_to_the_real_failure(
        self, info: dict, tmp_path: Path
    ) -> None:
        """A corrupt entry is not news; the transport failure is."""
        cache = Cache(tmp_path)
        provider = YouTubeProvider(extractor=lambda url: info, cache=cache)
        provider.list(WATCH_URL)
        cache.write(provider._manifest_key(VIDEO_ID), "{not json at all")

        with pytest.raises(AcquisitionFailed):
            YouTubeProvider(extractor=self._broken, cache=cache).list(WATCH_URL)

    def test_a_cache_entry_missing_its_fields_yields_too(
        self, info: dict, tmp_path: Path
    ) -> None:
        cache = Cache(tmp_path)
        provider = YouTubeProvider(extractor=lambda url: info, cache=cache)
        provider.list(WATCH_URL)
        cache.write(provider._manifest_key(VIDEO_ID), '{"meta": {}}')

        with pytest.raises(AcquisitionFailed):
            YouTubeProvider(extractor=self._broken, cache=cache).list(WATCH_URL)

    def test_a_lecture_with_no_captions_does_not_resurrect_an_old_manifest(
        self, info: dict, tmp_path: Path
    ) -> None:
        """The line the taxonomy already draws, and why it is the right one.

        `NoCaptionsAvailable` is a `CaptionError`: the source *was* reached and
        had nothing to offer. Overriding that with a stored manifest would
        claim tracks that no longer exist. Only a `SourceError` — could not
        reach or read it — falls back.
        """
        cache = Cache(tmp_path)
        YouTubeProvider(extractor=lambda url: info, cache=cache).list(WATCH_URL)

        emptied = {**info, "subtitles": {}, "automatic_captions": {}}

        with pytest.raises(NoCaptionsAvailable):
            YouTubeProvider(extractor=lambda url: emptied, cache=cache).list(WATCH_URL)

    def test_the_stored_tracks_round_trip_exactly(
        self, info: dict, tmp_path: Path
    ) -> None:
        cache = Cache(tmp_path)
        live = YouTubeProvider(extractor=lambda url: info, cache=cache).list(WATCH_URL)

        recalled = YouTubeProvider(extractor=self._broken, cache=cache).list(WATCH_URL)

        assert [h.track for h in recalled] == [h.track for h in live]

    def test_a_stored_manifest_still_resolves_the_same_track(
        self, info: dict, tmp_path: Path
    ) -> None:
        """Selection is the reason the tracks are stored rather than just meta."""
        cache = Cache(tmp_path)
        live = YouTubeProvider(extractor=lambda url: info, cache=cache).list(WATCH_URL)

        recalled = YouTubeProvider(extractor=self._broken, cache=cache).list(WATCH_URL)

        assert recalled.find(["en"]).track == live.find(["en"]).track
        assert recalled.find(["fr"]).track.tier is TrustTier.TRANSLATED


class TestThroughTheFacade:
    def test_the_fetcher_picks_the_youtube_provider_for_a_url(self, info: dict) -> None:
        fetcher = TranscriptFetcher(
            provider=YouTubeProvider(
                extractor=lambda url: info,
                opener=lambda url: load_caption("mit6006-lec1.manual.en.json3"),
            )
        )
        lecture = fetcher.fetch(WATCH_URL, ["en"])

        assert lecture.meta.channel == "MIT OpenCourseWare"


class TestRemotePayloadLimits:
    """The size ceiling on what a transport can hand back.

    `cli.run` catches per source so one bad lecture costs one item. That
    promise ends at the process boundary: an allocation large enough to be
    killed by the OS takes every already-fetched lecture with it, and no
    `except` clause runs. So the refusal has to happen before the bytes are
    held, which is what these check.
    """

    @staticmethod
    def _open(monkeypatch: pytest.MonkeyPatch, response: FakeResponse) -> str:
        from youtube_transcript_notes.sources import youtube

        class FakeYoutubeDL:
            def urlopen(self, url):
                return response

        monkeypatch.setattr(youtube, "_youtube_dl", FakeYoutubeDL)
        return youtube._open_url("https://captions.test/en.vtt")

    def test_exactly_at_the_limit_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b"a" * MAX_PAYLOAD_BYTES

        assert len(self._open(monkeypatch, FakeResponse(payload))) == MAX_PAYLOAD_BYTES

    def test_one_byte_under_the_limit_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b"a" * (MAX_PAYLOAD_BYTES - 1)

        assert len(self._open(monkeypatch, FakeResponse(payload))) == len(payload)

    def test_one_byte_over_the_limit_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b"a" * (MAX_PAYLOAD_BYTES + 1)

        with pytest.raises(PayloadTooLarge):
            self._open(monkeypatch, FakeResponse(payload))

    def test_an_honest_content_length_refuses_before_reading_anything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cheap refusal: no point downloading what will be rejected."""
        response = FakeResponse(b"", declared=MAX_PAYLOAD_BYTES + 1)

        with pytest.raises(PayloadTooLarge):
            self._open(monkeypatch, response)

        assert response._offset == 0  # never read a byte

    def test_an_undecodable_payload_is_malformed_not_a_network_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wire payload that is not UTF-8 gets a typed refusal.

        It used to escape as a bare `UnicodeDecodeError` and reach the CLI's
        last resort, whose advice is to retry — and no retry reads the same
        bytes differently.
        """
        with pytest.raises(MalformedCaptions) as caught:
            self._open(monkeypatch, FakeResponse(b"\xff\xfe\xfd"))

        assert "not UTF-8" in caught.value.cause
        assert "retry" not in caught.value.cause.lower()

    def test_a_lying_content_length_does_not_get_past_the_stream_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The header is a claim, not a measurement. A hostile end can
        understate it and the streaming cap still has to hold."""
        response = FakeResponse(b"a" * (MAX_PAYLOAD_BYTES + 1), declared=10)

        with pytest.raises(PayloadTooLarge):
            self._open(monkeypatch, response)

    def test_a_missing_content_length_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert self._open(monkeypatch, FakeResponse(b"WEBVTT\n")) == "WEBVTT\n"

    def test_an_unparseable_content_length_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = FakeResponse(b"WEBVTT\n", declared="not-a-number")

        assert self._open(monkeypatch, response) == "WEBVTT\n"

    def test_a_none_content_length_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = FakeResponse(b"WEBVTT\n", declared=None)

        assert self._open(monkeypatch, response) == "WEBVTT\n"

    def test_the_response_is_closed_after_a_successful_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = FakeResponse(b"WEBVTT\n")

        self._open(monkeypatch, response)

        assert response.closed

    def test_the_response_is_closed_when_the_payload_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refused payload must not also leak the connection carrying it."""
        response = FakeResponse(b"a" * (MAX_PAYLOAD_BYTES + 1))

        with pytest.raises(PayloadTooLarge):
            self._open(monkeypatch, response)

        assert response.closed

    def test_a_response_with_no_headers_at_all_is_tolerated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """yt-dlp's response objects are not all the same shape."""
        from youtube_transcript_notes.sources import youtube

        class Bare:
            def __init__(self) -> None:
                self._sent = False

            def read(self, amount: int) -> bytes:
                if self._sent:
                    return b""
                self._sent = True
                return b"WEBVTT\n"

        class FakeYoutubeDL:
            def urlopen(self, url):
                return Bare()

        monkeypatch.setattr(youtube, "_youtube_dl", FakeYoutubeDL)

        assert youtube._open_url("https://captions.test/en.vtt") == "WEBVTT\n"

    def test_an_oversized_payload_is_never_cached(
        self, info: dict, tmp_path: Path
    ) -> None:
        """A rejected payload that landed in the cache would be served forever
        after, and the second run would not even reach the size check."""
        cache = Cache(tmp_path / "cache")
        provider = YouTubeProvider(
            extractor=lambda url: info,
            opener=lambda url: "a" * (MAX_PAYLOAD_BYTES + 1),
            cache=cache,
        )
        handle = provider.list(WATCH_URL).find(["en"])

        # The opener here returns an oversized *string*, so the refusal comes
        # from `parse_captions` rather than the transport — the other end of
        # the same policy.
        with pytest.raises(PayloadTooLarge):
            handle.fetch()

        assert list(tmp_path.rglob("*.json")) == []


class TestNonFiniteMetadataNumbers:
    """JSON's parser accepts ``Infinity`` and ``NaN`` as literals.

    A non-finite duration or chapter start survives every stage without
    complaint and then crashes `format_timestamp` inside a renderer, stages
    away from the cause — the exact failure `require_finite` refuses on the
    caption path. Metadata is the forgiving path, so here it reads as missing.
    """

    @pytest.mark.parametrize(
        "value", [float("nan"), float("inf"), float("-inf"), "Infinity", "NaN"]
    )
    def test_a_non_finite_number_reads_as_missing(self, value: object) -> None:
        from youtube_transcript_notes.sources import youtube

        assert youtube._optional_float(value) is None

    def test_a_non_finite_duration_never_reaches_the_meta(self) -> None:
        from youtube_transcript_notes.sources import youtube

        meta = youtube._meta_from(
            {"id": "dQw4w9WgXcQ", "title": "Lecture", "duration": float("inf")}
        )

        assert meta.duration is None


class TestTheIdMustBeUsable:
    """A lecture with no identity cannot be named or cached.

    `_require_shape` used to test only that the `id` *key* was there. An empty
    string passed that and reopened the overwrite finding — `naming` falls back
    to the bare title when there is no id to distinguish it with, so the
    filename went back to being something the uploader chose. `None` and a bare
    number got further and raised `TypeError` out of `sanitise`, which is not
    in the taxonomy at all.
    """

    @pytest.mark.parametrize("identifier", ["", "   ", None, 123, ["x"]])
    def test_an_unusable_id_is_a_contract_change(
        self, info: dict, identifier: object
    ) -> None:
        broken = {**info, "id": identifier}

        with pytest.raises(TransportContractChanged, match="no usable 'id'"):
            YouTubeProvider(extractor=lambda url: broken).list(WATCH_URL)

    def test_a_missing_id_key_still_fails(self, info: dict) -> None:
        broken = {key: value for key, value in info.items() if key != "id"}

        with pytest.raises(TransportContractChanged, match="no usable 'id'"):
            YouTubeProvider(extractor=lambda url: broken).list(WATCH_URL)

    def test_an_ordinary_id_is_untouched(self, provider: YouTubeProvider) -> None:
        assert provider.list(WATCH_URL).meta.source_id == VIDEO_ID


SIGNED_URL = (
    "https://www.youtube.com/api/timedtext"
    "?v=HtSuA80QTyo&sig=SUPERSECRET123&token=ALSOSECRET&expire=1799999999"
)
SECRETS = ("SUPERSECRET123", "ALSOSECRET", "sig=", "token=")


class TestSignedUrlsAreRedacted:
    """A signed caption URL is a bearer credential until it expires.

    The impact is genuinely small — captions are public and the signature
    lasts hours — which is why it is worth fixing cheaply rather than arguing
    about. Nothing needs the query string to diagnose a failure.

    These drive the real path rather than substituting the opener: the
    transport is what fails, `_open_url` classifies it, and the ref names it.
    A replaced opener would skip the classifier that does the redacting and
    prove nothing about production.
    """

    @pytest.fixture
    def failing_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from youtube_transcript_notes.sources import youtube

        class FakeYoutubeDL:
            def urlopen(self, url):
                # yt-dlp quotes the URL it failed on, so the secret is in the
                # message as well as in what the tool passed around.
                raise RuntimeError(f"HTTP Error 403: Forbidden for url: {url}")

        monkeypatch.setattr(youtube, "_youtube_dl", FakeYoutubeDL)

    @pytest.fixture
    def handle(self, info: dict, failing_transport: None):
        provider = YouTubeProvider(extractor=lambda url: info, cache=NullCache())
        return provider.list(WATCH_URL).find(["en"])

    def test_neither_the_message_nor_the_context_carries_the_query(
        self, handle
    ) -> None:
        with pytest.raises(AcquisitionFailed) as caught:
            handle.fetch()

        rendered = str(caught.value) + json.dumps(caught.value.remedy)
        for secret in SECRETS:
            assert secret not in rendered

    def test_the_track_is_still_named(self, handle) -> None:
        """Redaction must not cost the diagnosis. Video, language and format
        are what say which of five hundred tracks failed."""
        with pytest.raises(AcquisitionFailed) as caught:
            handle.fetch()

        message = str(caught.value)
        assert VIDEO_ID in message
        assert "en" in message
        assert "json3" in message
        assert "403" in message  # the actionable part of the third-party text

    def test_both_cli_streams_are_clean(
        self, info: dict, failing_transport: None, capsys
    ) -> None:
        from youtube_transcript_notes import cli

        cli.main([WATCH_URL])
        captured = capsys.readouterr()

        for secret in SECRETS:
            assert secret not in captured.out + captured.err
        assert VIDEO_ID in captured.err

    def test_the_json_envelope_is_clean_and_still_serializable(
        self, info: dict, failing_transport: None, capsys
    ) -> None:
        from youtube_transcript_notes import cli

        cli.main([WATCH_URL, "--json"])
        out = capsys.readouterr().out

        payload = json.loads(out)
        for secret in SECRETS:
            assert secret not in out
        assert payload["errors"][0]["code"] == "ACQUISITION_FAILED"

    def test_an_unclassified_exception_is_redacted_too(
        self, info: dict, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """`cli._wrap` is the catch-all for anything the taxonomy did not
        recognise, and it embeds the message verbatim. A classified failure has
        at least been looked at; this path by definition has not."""
        from youtube_transcript_notes import cli
        from youtube_transcript_notes.resolve import TrackHandle

        def explode(self, *args, **kwargs):
            raise ValueError(f"something odd happened at {SIGNED_URL}")

        monkeypatch.setattr(TrackHandle, "fetch", explode)
        monkeypatch.setattr(
            "youtube_transcript_notes.api.TranscriptFetcher.list",
            lambda self, source: YouTubeProvider(
                extractor=lambda url: info, cache=NullCache()
            ).list(source),
        )

        cli.main([WATCH_URL])
        captured = capsys.readouterr()

        for secret in SECRETS:
            assert secret not in captured.out + captured.err
