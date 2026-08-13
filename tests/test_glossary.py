"""Corrections: what gets proposed, and — mostly — what does not.

The interesting assertions here are the negative ones. An earlier version of
this stage matched phrases by `difflib` ratio and proposed 140 corrections on a
two-hour interview, of which about eight were right; the failure was not the
threshold but the measure, because a ratio rewards the part of a string that
matches and "Claude to" therefore beat "quad code" as a reading of "Claude
Code". Every test below that asserts *nothing* was proposed is holding that
door shut.
"""

from __future__ import annotations

import pytest

from youtube_transcript_notes.errors import MalformedCorrections, PayloadTooLarge
from youtube_transcript_notes.limits import MAX_GLOSSARY_TERMS
from youtube_transcript_notes.models import Chapter, LectureMeta, Passage
from youtube_transcript_notes.refine import (
    Glossary,
    propose_corrections,
    read_corrections,
    read_glossary,
    terms_from,
)


def passage(text: str, start: float = 0.0) -> Passage:
    return Passage(text=text, start=start, end=start + 1.0)


def meta(title: str, chapters: tuple[str, ...] = (), channel: str | None = None):
    return LectureMeta(
        source_id="x",
        title=title,
        channel=channel,
        chapters=tuple(
            Chapter(title=name, start=float(n)) for n, name in enumerate(chapters)
        ),
    )


class TestWhatCountsAsAName:
    def test_a_heading_does_not_make_its_first_word_a_proper_noun(self) -> None:
        """`## Lessons from Meta` is about Meta, not about Lessons.

        Getting this wrong is not cosmetic. "Lessons" as a watched term
        annotates every "lesson" and "session" in the transcript, and a single
        generic chapter title is enough to ruin a document.
        """
        assert terms_from(meta("Talk", chapters=("Lessons from Meta",))).terms == {}

        # The two-word run keeps both its forms, because the second word being
        # capitalised is evidence the first one cannot fake.
        joining = terms_from(meta("Talk", chapters=("Joining Anthropic",))).terms
        assert set(joining) == {"Joining Anthropic", "Anthropic"}

    def test_a_title_offers_the_name_inside_it(self) -> None:
        found = terms_from(meta("Building Claude Code with Boris Cherny"))

        assert set(found.terms) == {
            "Building Claude Code",
            "Claude Code",
            "Boris Cherny",
        }

    def test_a_name_mid_sentence_stands_on_its_own(self) -> None:
        found = terms_from(meta("Talk", chapters=("Engineering culture at Anthropic",)))

        assert "Anthropic" in found.terms
        assert "Engineering" not in found.terms

    def test_short_words_are_never_watched(self) -> None:
        """ "Meta" is one edit from meat, beta, mega and met."""
        found = terms_from(meta("Talk", chapters=("A word about Meta",)))

        assert found.terms == {}

    def test_the_channel_counts_too(self) -> None:
        found = terms_from(meta("Talk", channel="Latent Space"))

        assert found.terms == {"Latent Space": "channel"}


class TestNearMisses:
    @pytest.mark.parametrize(
        ("wrong", "right"),
        [
            ("Enthropic", "Anthropic"),
            ("Boris Cherney", "Boris Cherny"),
            ("Cloud Code", "Claude Code"),
            ("Claude core", "Claude Code"),
        ],
    )
    def test_a_spelling_within_two_characters_is_proposed(
        self, wrong: str, right: str
    ) -> None:
        glossary = Glossary(terms={right: "title"}, variants={})
        found = propose_corrections([passage(f"and then {wrong} appeared")], glossary)

        assert [(c.wrong, c.right) for c in found] == [(wrong, right)]

    @pytest.mark.parametrize(
        "phrase",
        ["Claude to", "Claude on", "clean code", "made Node", "Claude agent"],
    )
    def test_a_different_phrase_is_left_alone(self, phrase: str) -> None:
        """Each of these was proposed as "Claude Code" by the ratio measure."""
        glossary = Glossary(terms={"Claude Code": "title"}, variants={})

        assert propose_corrections([passage(f"we used {phrase} there")], glossary) == ()

    def test_a_digit_is_not_a_spelling(self) -> None:
        """ "Sonnet 4.5" is one edit from "Sonnet 3.5" and is a different model.

        Numbers are where transcription errors do their real damage — "20
        bucks" for "20 bugs" destroys a statistic — so this stage will not
        touch one on a guess.
        """
        glossary = Glossary(terms={"Sonnet 3.5": "glossary"}, variants={})

        assert propose_corrections([passage("we shipped Sonnet 4.5")], glossary) == ()

    def test_the_right_spelling_is_not_corrected_to_itself(self) -> None:
        glossary = Glossary(terms={"Anthropic": "title"}, variants={})

        assert propose_corrections([passage("at Anthropic today")], glossary) == ()

    @pytest.mark.parametrize(
        ("term", "spoken"),
        [
            # Measured on MIT 6.006 Lecture 1: the chapter "Simple Algorithm"
            # contributed the word "Algorithm", and the lecturer says
            # "algorithms" all afternoon. It annotated a correct transcript
            # thirty-six times — in the middle of the sentences the note exists
            # to make readable — and every correction on that lecture was one
            # of these.
            ("Algorithm", "algorithms"),
            ("Simple Algorithm", "simple algorithms"),
            # The other direction is just as wrong, and just as common: a
            # heading naming several, a speaker naming one.
            ("Algorithms", "algorithm"),
            # And the possessive, which is the same word again.
            ("Anthropic", "Anthropic's"),
        ],
    )
    def test_a_plural_is_the_same_word_not_a_misspelling(
        self, term: str, spoken: str
    ) -> None:
        glossary = Glossary(terms={term: "chapter"}, variants={})

        assert propose_corrections([passage(f"about {spoken} today")], glossary) == ()

    def test_suppressing_a_plural_does_not_stop_the_rest_of_the_pass(self) -> None:
        """The suppression skips that term and keeps looking, rather than
        deciding the phrase is fine and moving on."""
        glossary = Glossary(
            terms={"Algorithm": "chapter", "Anthropic": "chapter"}, variants={}
        )
        found = propose_corrections(
            [passage("about algorithms at Enthropic today")], glossary
        )

        assert [(c.wrong, c.right) for c in found] == [("Enthropic", "Anthropic")]

    def test_a_named_term_still_catches_a_missing_s(self) -> None:
        """The limit of the rule above, and why it is scoped to the automatic
        half. Nothing about "Devada" distinguishes it from a plural — but a
        term written in a glossary file is a decision somebody made, and
        honouring it is what makes keeping the list worthwhile."""
        glossary = Glossary(terms={"Devadas": "glossary"}, variants={})
        found = propose_corrections([passage("professor Devada spoke")], glossary)

        assert [(c.wrong, c.right) for c in found] == [("Devada", "Devadas")]

    def test_one_mistake_is_reported_once(self) -> None:
        """ "Erik Domane" and "Domane" both match; the reader made one error."""
        glossary = Glossary(
            terms={"Erik Demaine": "glossary", "Demaine": "glossary"}, variants={}
        )
        found = propose_corrections([passage("with Erik Domane, who")], glossary)

        assert [c.wrong for c in found] == ["Erik Domane"]

    def test_a_swallowed_overlap_does_not_swallow_what_follows(self) -> None:
        """The shorter match inside "Erik Domane" is dropped, and the next
        mistake along is still found. Tested directly rather than left to fall
        out of a fixture: it did, until the plural suppression stopped that
        lecture producing overlaps at all."""
        glossary = Glossary(
            terms={
                "Anthropic Labs": "glossary",
                "Anthropic": "glossary",
                "Cherny": "glossary",
            },
            variants={},
        )
        found = propose_corrections(
            [passage("at Enthropic Labs and Cherney here")], glossary
        )

        # "Enthropic" matches on its own and is dropped for sitting inside
        # "Enthropic Labs" — one mistake, one row. "Cherney" comes after it in
        # the same pass and must survive that.
        assert sorted(c.wrong for c in found) == ["Cherney", "Enthropic Labs"]

    def test_occurrences_are_counted_across_passages(self) -> None:
        glossary = Glossary(terms={"Anthropic": "title"}, variants={})
        found = propose_corrections(
            [passage("at Enthropic", 1.0), passage("Enthropic again", 9.0)], glossary
        )

        assert found[0].occurrences == 2
        assert found[0].at == 1.0  # where it was first seen

    def test_nothing_to_watch_means_nothing_to_do(self) -> None:
        assert propose_corrections([passage("anything")], Glossary({}, {})) == ()


class TestKnownWrongForms:
    def test_an_exact_form_is_corrected_however_far_it_is(self) -> None:
        """ "quad code" is four edits from "Claude Code" — unreachable by
        spelling, and the whole reason the list exists."""
        glossary = read_glossary("Claude Code: quad code, Squad code")
        found = propose_corrections([passage("open quad code and run it")], glossary)

        assert [(c.wrong, c.right, c.confidence) for c in found] == [
            ("quad code", "Claude Code", 1.0)
        ]

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        glossary = read_glossary("# names\n\n  \nAnthropic  # the company\n:no term\n")

        assert glossary.terms == {"Anthropic": "glossary"}

    def test_a_form_listed_twice_resolves_the_same_way_every_run(self) -> None:
        glossary = read_glossary("Claude Code: colab\nClaude Cowork: colab")

        assert glossary.variants["colab"] == ("Claude Code", "glossary")

    def test_a_term_is_not_a_variant_of_itself(self) -> None:
        assert read_glossary("Anthropic: anthropic").variants == {}

    def test_later_files_win(self) -> None:
        standing = read_glossary("Claude Code: colab")
        this_run = read_corrections(
            [{"wrong": "colab", "right": "Claude Cowork"}], "run.json"
        )

        assert this_run.merged_with(standing).variants["colab"][0] == "Claude Cowork"


class TestACorrectionsTable:
    def test_a_models_findings_become_forms_to_watch(self) -> None:
        glossary = read_corrections(
            [
                {
                    "wrong": "Andrew Carpet",
                    "right": "Andrej Karpathy",
                    "evidence": "0:12",
                }
            ],
            "run.json",
        )
        found = propose_corrections(
            [passage("Andrew Carpet posted that"), passage("and Andrew Carpet again")],
            glossary,
        )

        # Reported once by the model, found and counted everywhere it occurs.
        assert [(c.right, c.occurrences, c.evidence) for c in found] == [
            ("Andrej Karpathy", 2, "0:12")
        ]

    def test_evidence_is_optional(self) -> None:
        glossary = read_corrections([{"wrong": "a", "right": "b"}], "run.json")

        assert glossary.variants["a"] == ("b", "given")

    @pytest.mark.parametrize(
        "records",
        [["not an object"], [{"wrong": "a"}], [{"wrong": 1, "right": 2}]],
    )
    def test_a_malformed_entry_says_so(self, records: list) -> None:
        with pytest.raises(MalformedCorrections):
            read_corrections(records, "run.json")

    def test_a_correction_to_the_same_word_is_dropped(self) -> None:
        assert read_corrections([{"wrong": "a", "right": "a"}], "x").variants == {}


class TestTermsThatAreNotWords:
    def test_a_term_with_no_letters_is_never_watched(self) -> None:
        """A bare number as a term would put every similar number in the
        transcript up for correction, which is the one thing this stage is
        most careful never to do."""
        glossary = Glossary(terms={"123456": "glossary"}, variants={})

        assert propose_corrections([passage("call 123457 today")], glossary) == ()


class TestVersionsAreNotSpellings:
    @pytest.mark.parametrize(
        ("term", "phrase"),
        [("Sonnet 3.5", "Sonnet 4.5"), ("GPT-4o", "GPT-4"), ("Claude 3", "Claude 4")],
    )
    def test_a_term_carrying_a_digit_is_matched_exactly_or_not_at_all(
        self, term: str, phrase: str
    ) -> None:
        """These are different things, not different spellings of one thing.

        Numbers and version letters are where a wrong correction does real
        damage: it silently rewrites which model somebody was talking about.
        """
        glossary = Glossary(terms={term: "glossary"}, variants={})

        assert propose_corrections([passage(f"we used {phrase}")], glossary) == ()

    def test_naming_the_wrong_form_still_works(self) -> None:
        """Saying so is a decision somebody made on purpose."""
        glossary = read_glossary("Sonnet 3.5: sauna 3.5")
        found = propose_corrections([passage("built on sauna 3.5 back then")], glossary)

        assert [c.right for c in found] == ["Sonnet 3.5"]


class TestTitleCaseProvesNothingTwice:
    def test_a_word_after_a_separator_is_not_a_name(self) -> None:
        """`Stanford CS230 | Autumn 2025 | Lecture 8: Agents, Prompts, and RAG`
        offers no evidence that "Lecture" is a name — the bar capitalised it,
        exactly as the start of the title capitalises its first word. Watching
        it annotated every "lectures" in a lecture."""
        found = terms_from(
            meta("Stanford CS230 | Autumn 2025 | Lecture 8: Agents, Prompts, and RAG")
        )

        assert "Lecture" not in found.terms
        assert "Autumn" not in found.terms
        assert "Agents" not in found.terms

    def test_a_word_nothing_forced_is_still_a_name(self) -> None:
        """The rule has to stay narrow: a capital in the middle of a segment,
        after an ordinary lowercase word, is evidence and is kept."""
        found = terms_from(meta("What we learned building at Anthropic"))

        assert "Anthropic" in found.terms


class TestGlossarySizeCeiling:
    """The byte cap is not the binding one: every term runs an edit distance
    against every word window of every passage, so a list the byte cap
    happily admits can cost minutes per lecture — which on a playlist reads
    as a hang."""

    def test_a_glossary_at_the_term_limit_is_accepted(self) -> None:
        text = "\n".join(f"uniqueterm{n:05}" for n in range(MAX_GLOSSARY_TERMS))

        assert len(read_glossary(text).terms) == MAX_GLOSSARY_TERMS

    def test_a_glossary_past_the_term_limit_is_refused_whole(self) -> None:
        text = "\n".join(f"uniqueterm{n:05}" for n in range(MAX_GLOSSARY_TERMS + 1))

        with pytest.raises(PayloadTooLarge) as caught:
            read_glossary(text, "names.txt")

        assert f"{MAX_GLOSSARY_TERMS + 1:,} terms" in caught.value.cause
        assert "names.txt" in caught.value.cause
