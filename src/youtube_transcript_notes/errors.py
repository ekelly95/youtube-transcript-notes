"""The error taxonomy.

This module is written before any code that can fail, because the taxonomy is
the specification of what is allowed to go wrong. Anything that raises should
be able to name its failure here; if it cannot, the failure has not been
thought about yet.

Every error carries two audiences:

``cause``
    Prose for a human, explaining what happened and what to do about it. Error
    text is documentation people actually read, so it is worth writing well.

``remedy``
    The same information as data — a stable ``code`` plus an ordered list of
    next actions — so that an agent driving the tool can branch on a failure
    instead of pattern-matching English.

This module depends on nothing but the standard library, and must stay that way
so that any layer can raise from it without an import cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

__all__ = [
    "AcquisitionFailed",
    "AgeRestricted",
    "CaptionError",
    "ConfigError",
    "EmptyTranscript",
    "InputUnreadable",
    "LectureUnavailable",
    "MalformedCaptions",
    "MalformedCorrections",
    "MalformedLecture",
    "NoCaptionsAvailable",
    "OutputError",
    "OutputExists",
    "OutputUnwritable",
    "PayloadTooLarge",
    "PlaylistEmpty",
    "PlaylistNotSupported",
    "PlaylistTooLarge",
    "RegionBlocked",
    "SeveralLectures",
    "SourceError",
    "TrackNotFound",
    "TranscriptError",
    "TransportContractChanged",
    "UnknownCaptionFormat",
    "UnknownProvider",
    "UnknownRenderer",
]


class TranscriptError(Exception):
    """Base class for everything the tool raises deliberately.

    Subclasses declare three class attributes and usually nothing else:

    ``CODE``
        A stable machine identifier. Never change one once released — agents
        and scripts branch on it.
    ``CAUSE``
        Human prose. May contain ``{placeholders}`` filled from the keyword
        context passed to the constructor.
    ``TRY``
        Ordered suggestions for what to do next, most likely to work first.
    """

    CODE = "YOUTUBE_TRANSCRIPT_NOTES_ERROR"
    CAUSE = "Something went wrong that this tool does not have a specific name for."
    TRY: tuple[str, ...] = ()

    def __init__(self, **context: Any) -> None:
        self.context = context
        super().__init__()

    @property
    def cause(self) -> str:
        """Human-readable explanation, with context substituted in."""
        return self.CAUSE.format(**self.context)

    @property
    def remedy(self) -> dict[str, Any]:
        """Machine-readable failure description.

        The shape is deliberately boring and stable: a code, an ordered list of
        things to try, and the context that produced the failure.
        """
        return {"code": self.CODE, "try": list(self.TRY), "context": dict(self.context)}

    def __str__(self) -> str:
        parts = [self.cause]
        if self.TRY:
            suggestions = "\n".join(f"  - {suggestion}" for suggestion in self.TRY)
            parts.append(f"What to try:\n{suggestions}")
        return "\n\n".join(parts)


# --------------------------------------------------------------------------
# Acquisition — something went wrong reaching or reading the source.
# --------------------------------------------------------------------------


class SourceError(TranscriptError):
    """A lecture could not be reached, or is not the kind of thing we can use."""

    CODE = "SOURCE_ERROR"
    CAUSE = "The lecture at {source!r} could not be retrieved."


class LectureUnavailable(SourceError):
    CODE = "LECTURE_UNAVAILABLE"
    CAUSE = (
        "The lecture at {source!r} is not available. It may have been deleted, "
        "made private, or the identifier may be wrong."
    )
    TRY = (
        "Check the URL or video ID in a browser.",
        (
            "Search for a re-upload — lecture series are often mirrored on a "
            "department or course channel."
        ),
    )


class AgeRestricted(SourceError):
    CODE = "AGE_RESTRICTED"
    CAUSE = (
        "The lecture at {source!r} is age-restricted, so its captions cannot be "
        "retrieved without an authenticated session."
    )
    TRY = (
        "Look for the same lecture on the institution's own site or course page.",
        (
            "If you can get its captions as a file — course platforms usually "
            "let enrolled students download one — pass that file's path "
            "instead."
        ),
    )


class RegionBlocked(SourceError):
    CODE = "REGION_BLOCKED"
    CAUSE = "The lecture at {source!r} is not available in this region."
    TRY = (
        "Check whether the hosting institution publishes the lecture elsewhere.",
        "Look for an official mirror or course archive.",
    )


class PlaylistNotSupported(SourceError):
    """A collection, where a single lecture was expected.

    Its own error because the alternative is worse than unhelpful: a playlist
    URL reaches the extractor, comes back carrying no caption tracks of its
    own, and is reported as a lecture with no captions — which is true of the
    playlist and says nothing about the lectures in it.

    Playlists are turned into their videos by ``expand`` before discovery, so
    from the command line this now names the collections the tool deliberately
    leaves alone — channels and search pages, which can run to thousands of
    videos with nothing in common. ``list`` itself still refuses every
    collection: it answers for one lecture, and expansion is a separate,
    earlier step.
    """

    CODE = "PLAYLIST_NOT_SUPPORTED"
    CAUSE = (
        "{source!r} names a channel, search page or other collection rather "
        "than one lecture."
    )
    TRY = (
        (
            "Open it and pass the lecture or playlist URLs you want — a "
            "playlist is expanded into its videos automatically."
        ),
        (
            "Pass several at once — sources are processed one at a time and "
            "one failure will not lose the rest."
        ),
    )


class PlaylistTooLarge(SourceError):
    """A playlist past the size the tool is willing to expand.

    Refused whole rather than truncated, because the first few hundred videos
    of an enormous playlist look exactly like a complete run to anyone reading
    the output folder — a silent cap is data loss reported as success.
    """

    CODE = "PLAYLIST_TOO_LARGE"
    CAUSE = (
        "The playlist at {source!r} holds more than {limit} videos, so it was "
        "refused outright rather than quietly cut short."
    )
    TRY = (
        "Pass a smaller playlist, or the individual lecture URLs you want.",
        (
            "The ceiling is MAX_PLAYLIST_ITEMS in "
            "youtube_transcript_notes/limits.py, with the reasoning."
        ),
    )


class PlaylistEmpty(SourceError):
    """A playlist that was reached and holds nothing.

    Deliberately raised outside the cache fallback in ``expand``: emptiness is
    a present-tense fact learned by reaching the source, and serving last
    week's roster for a playlist that now says it has none would be inventing
    lectures it no longer offers.
    """

    CODE = "PLAYLIST_EMPTY"
    CAUSE = "The playlist at {source!r} holds no videos."
    TRY = (
        (
            "Check the playlist in a browser — it may have been emptied, made "
            "private, or the identifier may be wrong."
        ),
    )


class SeveralLectures(SourceError):
    """A folder holding more than one lecture, where one was expected.

    The line `PlaylistNotSupported` draws, for the other provider: `list`
    answers for one lecture, and turning a collection into its members is
    `expand`'s job, a separate and earlier step. The command line never reaches
    this — a folder is expanded before the loop — so it is what a library
    caller meets on handing `list` a course directory.
    """

    CODE = "SEVERAL_LECTURES"
    CAUSE = (
        "The folder {source!r} holds {count} lectures, and a manifest "
        "describes one:\n{lectures}"
    )
    TRY = (
        "Pass one of them — its path, or its name without the extension.",
        (
            "From the command line, pass the folder: each lecture in it is "
            "processed on its own, and one failure costs one lecture."
        ),
        "As a library, call expand() first — it names one source per lecture.",
    )

    def __init__(self, **context: Any) -> None:
        names = list(context.pop("lectures", ()))
        shown = names[: self.MAX_LISTED]
        listing = "\n".join(f"  - {name}" for name in shown)
        if len(names) > len(shown):
            listing += f"\n  … and {len(names) - len(shown)} more"
        super().__init__(count=len(names), lectures=listing, **context)

    #: Contract 5 applies to an error message holding three hundred lecture
    #: names as much as to a listing, and for the same reason.
    MAX_LISTED = 12


class TransportContractChanged(SourceError):
    """The tool and its transport no longer agree on the shape of a lecture.

    The failure this exists to stop being invisible: the tool reads
    ``subtitles`` and ``automatic_captions`` out of what yt-dlp returns, and
    read them defensively enough that a rename produced *no tracks* rather
    than an error. That surfaced as `NoCaptionsAvailable` — a confident
    statement that the lecture has no captions, made about a video that has
    plenty, sending the reader to check a video that is fine.

    Same reasoning as `PlaylistNotSupported`: an empty result is only honest
    when the emptiness is the *source's*, and a wrong diagnosis is worse than
    an unhelpful one. So the distinction drawn is narrow and deliberate —
    caption keys *absent* means the contract moved, caption keys *present and
    empty* means the lecture really has none.

    Nearly always fixed by upgrading yt-dlp, which is why that is the first
    thing suggested and why the tool no longer caps the version it accepts.
    """

    CODE = "TRANSPORT_CONTRACT_CHANGED"
    CAUSE = (
        "The YouTube transport did not return what this tool expects: {detail}.\n"
        "This is a compatibility problem between this tool and yt-dlp {version} "
        "— not a problem with {source!r}, which may be perfectly fine."
    )
    TRY = (
        "Update the transport: pip install -U yt-dlp",
        (
            "If yt-dlp is already current, YouTube may have changed something "
            "it has not caught up with yet. Try again in a day or two."
        ),
        (
            "Check the seam directly: pytest -m canary, from a clone. It says "
            "whether the transport or the lecture is at fault."
        ),
    )


class AcquisitionFailed(SourceError):
    """The transport failed in a way we could not classify.

    This is the honest fallback. It exists so unclassified transport failures
    still arrive as a ``TranscriptError`` with the underlying detail attached,
    rather than leaking a third-party exception type to the caller.
    """

    CODE = "ACQUISITION_FAILED"
    CAUSE = "Retrieving {source!r} failed: {detail}"
    TRY = (
        "Retry — transient network and rate-limit failures are common.",
        "Check that the transport dependency is up to date.",
    )


# --------------------------------------------------------------------------
# Captions — we reached the source, but the caption data is missing or bad.
# --------------------------------------------------------------------------


class CaptionError(TranscriptError):
    """The lecture exists, but usable caption data does not."""

    CODE = "CAPTION_ERROR"
    CAUSE = "No usable captions could be produced for {source!r}."


class NoCaptionsAvailable(CaptionError):
    CODE = "NO_CAPTIONS_AVAILABLE"
    CAUSE = "The lecture at {source!r} has no caption tracks of any kind."
    TRY = (
        "Check whether the same lecture is posted elsewhere with captions.",
        (
            "Captions are required: this tool reads them, it does not create "
            "them. If you can get a transcript as a caption file (.srt, .vtt "
            "or .json3) — from the course platform, or by transcribing the "
            "recording with a separate tool — pass its path instead."
        ),
    )


class PayloadTooLarge(CaptionError):
    """A caption payload was past the size the tool is willing to hold.

    Separate from `MalformedCaptions` because the data is not malformed — it
    may be perfectly well-formed and simply enormous, and telling someone
    their file is corrupt when it is merely huge sends them to fix the wrong
    thing.

    Raised *before* the payload is fully in memory wherever the boundary
    allows it, which is the only way this helps. An error raised after the
    allocation that would have killed the process is an epitaph, not a guard.
    See `limits` for the numbers and the measurements behind them.
    """

    CODE = "PAYLOAD_TOO_LARGE"
    CAUSE = (
        "The caption data for {source!r} is larger than the tool will read: "
        "{measured} against a limit of {limit}.\n"
        "Refused before loading it, so that one oversized source cannot end "
        "the run for every other one."
    )
    TRY = (
        (
            "Check the file is what you think it is. Nothing a lecture "
            "produces comes close to this — the usual cause is a wrong path, "
            "a concatenated dump, or a download that captured a whole page."
        ),
        ("If the source really is this large, split it and pass the parts separately."),
        (
            "The ceilings are in youtube_transcript_notes/limits.py, with the "
            "reasoning for each."
        ),
    )


class TrackNotFound(CaptionError):
    """No track matched the requested languages and trust tiers.

    Carries the full availability listing, because "not found" without showing
    what *was* available forces the caller to go and look it up by hand. The
    listing is passed in pre-formatted so this module stays free of imports
    from the resolver.
    """

    CODE = "TRACK_NOT_FOUND"
    CAUSE = (
        "No caption track for {source!r} matched languages {languages} and "
        "tiers {tiers}.\n\nAvailable tracks:\n{available}"
    )
    TRY = (
        "Widen the language list.",
        (
            "Allow a lower trust tier — auto-generated captions are usually present "
            "even when human-written ones are not."
        ),
    )

    def __init__(
        self,
        source: str,
        languages: Sequence[str],
        tiers: Sequence[str],
        available: Sequence[str],
    ) -> None:
        super().__init__(
            source=source,
            languages=list(languages),
            tiers=list(tiers),
            available=self._format_available(available),
        )

    @staticmethod
    def _format_available(available: Sequence[str]) -> str:
        if not available:
            return "  (none)"
        return "\n".join(f"  - {track}" for track in available)


class MalformedCaptions(CaptionError):
    CODE = "MALFORMED_CAPTIONS"
    CAUSE = "The {fmt} caption data for {source!r} could not be parsed: {detail}"
    TRY = (
        "Try a different caption format for the same lecture.",
        (
            "Capture the payload as a test fixture — a parser that meets real data "
            "it cannot handle is a bug worth pinning down."
        ),
    )


class EmptyTranscript(CaptionError):
    """A track was fetched, read and understood, and holds no text at all.

    Distinct from `MalformedCaptions`, which is a file that could not be read,
    and from `NoCaptionsAvailable`, which is a claim about the *lecture* — that
    it offers no tracks of any kind. This is narrower and better evidenced than
    either: a track was offered, fetched and parsed without complaint, and
    there was nothing in it.

    Raised rather than returned, because the alternative is what it replaced —
    a note holding a title and nothing else, written to disk, reported as
    success. Contract 7 names this case exactly: a fetched track with no usable
    text is `EmptyTranscript`, and the source was reached to establish it.
    """

    CODE = "EMPTY_TRANSCRIPT"
    CAUSE = "The {fmt} track for {source!r} was read successfully but holds no text."
    TRY = (
        "Run --list and try another track: one format may carry what another lost.",
        (
            "For a local file, open it — a caption file with no timing lines "
            "parses to nothing, however much text is in it."
        ),
    )


class MalformedLecture(TranscriptError):
    """A serialised lecture could not be read back.

    Raised at the I/O boundary — the cache, or anything loading a stored
    lecture — so the data model itself stays free of defensive branching.
    """

    CODE = "MALFORMED_LECTURE"
    CAUSE = "Stored lecture data could not be read: {detail}"
    TRY = (
        "Delete the cached entry and re-fetch the lecture.",
        (
            "If this followed an upgrade, the schema version has moved on and the "
            "cache needs clearing."
        ),
    )


# --------------------------------------------------------------------------
# Configuration — the caller asked for something that does not exist.
# --------------------------------------------------------------------------


class ConfigError(TranscriptError):
    """The caller asked for a component that is not registered."""

    CODE = "CONFIG_ERROR"
    CAUSE = "Unknown {kind}: {name!r}. Available: {available}."


class UnknownRenderer(ConfigError):
    CODE = "UNKNOWN_RENDERER"
    CAUSE = "Unknown output format {name!r}. Available formats: {available}."
    TRY = ("Pick one of the listed formats.",)


class UnknownProvider(ConfigError):
    """Nothing recognised the source.

    In practice this nearly always means a mistyped path or URL, so the
    message addresses that rather than reporting a provider-registry miss —
    which is a true statement about the internals and no help at all to
    someone who dropped a character from a filename.
    """

    CODE = "UNKNOWN_PROVIDER"
    CAUSE = (
        "Could not tell what {name!r} is. Expected a YouTube URL or video ID, "
        "or the path of a caption file or folder that exists.\n"
        "(Known sources: {available}.)"
    )
    TRY = (
        (
            "Check the path — a path that does not exist looks the same as a "
            "source the tool does not recognise."
        ),
        "Check the URL, or pass the bare 11-character video ID.",
    )


class UnknownCaptionFormat(ConfigError):
    CODE = "UNKNOWN_CAPTION_FORMAT"
    CAUSE = "No parser for caption format {name!r}. Supported: {available}."
    TRY = (
        "Request one of the supported formats from the source.",
        "For YouTube, json3 is preferred — it carries word-level timings.",
    )


class InputUnreadable(ConfigError):
    """A file the caller pointed at could not be read at all.

    Separate from the shape errors below because the two need different
    advice: a file with the wrong contents needs editing, and a file that is
    not there needs a different path. Without this, a mistyped `--glossary`
    left the CLI raising a bare `FileNotFoundError` — the commonest possible
    mistake, reported as a traceback.
    """

    CODE = "INPUT_UNREADABLE"
    CAUSE = "Could not read {source}: {detail}."
    TRY = (
        (
            "Check the path — a file that is not there and one that cannot be "
            "opened look the same from here."
        ),
        "If it exists, check it is UTF-8 text and not something binary.",
    )


class MalformedCorrections(ConfigError):
    """A corrections file could not be read.

    Its own error rather than a caption one: the file is something the caller
    wrote or a model produced, so the fix is in their hands and the message
    should say what shape was expected rather than blame the lecture.
    """

    CODE = "MALFORMED_CORRECTIONS"
    CAUSE = "Could not read corrections from {source}: {detail}."
    TRY = (
        ('Expected a JSON list of objects, each with at least "wrong" and "right".'),
        'Optional per entry: "evidence" — where the right spelling came from.',
    )


# --- Output ------------------------------------------------------------------
#
# Its own family rather than a kind of `ConfigError`: the request was fine and
# the lecture was fetched, parsed and rendered. Only the copy on disk is
# missing, and the document itself is intact — which is why a failed write
# costs one lecture rather than the run.


class OutputError(TranscriptError):
    """A document was produced but could not be filed where it was asked to go."""

    CODE = "OUTPUT_ERROR"
    CAUSE = "The document for {path} could not be written."


class OutputUnwritable(OutputError):
    """The destination refused the write — no directory, no room, no permission."""

    CODE = "OUTPUT_UNWRITABLE"
    CAUSE = "Could not write {path}: {detail}."
    TRY = (
        "Check the directory exists, is writable, and has room.",
        "Write somewhere else with --out and move the notes afterwards.",
    )


class OutputExists(OutputError):
    """Something is already at that name, and it is not this lecture's note.

    Two leaves rather than one, on the same reasoning `InputUnreadable` gives:
    a read-only directory needs a different directory and an occupied name
    needs a decision about replacing it, so one message would give the wrong
    advice to half the people reading it.
    """

    CODE = "OUTPUT_EXISTS"
    CAUSE = (
        "{path} already exists and differs from this lecture, so it was left "
        "alone and nothing was written."
    )
    TRY = (
        "Pass --force to replace files that are already there.",
        "Or move the existing file out of the way and run it again.",
        "Or write into an empty directory with --out.",
    )
