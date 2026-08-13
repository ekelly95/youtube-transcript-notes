"""Tests for the untrusted-text helpers.

The renderer tests prove hostile content comes out inert. These prove *why*,
one rule at a time, so a failure says which rule broke rather than that a
document changed somewhere.
"""

from __future__ import annotations

import pytest

from youtube_transcript_notes.render.escape import body, label, safe_url


class TestLabel:
    def test_a_newline_cannot_open_a_new_line(self) -> None:
        """The whole title injection, in one assertion.

        `# {title}` gives an uploader one heading. A newline in the title
        would give them every line after it.
        """
        assert label("Week 3\n\n# Injected") == "Week 3 # Injected"

    def test_link_and_image_syntax_is_inert(self) -> None:
        assert label("![pixel](http://evil.test/p.png)") == (
            "!\\[pixel\\](http://evil.test/p.png)"
        )

    def test_raw_html_is_inert(self) -> None:
        assert label("<img src=x>") == "\\<img src=x\\>"

    def test_ordinary_titles_are_untouched(self) -> None:
        """The common case has to stay readable, or the fix is a tax."""
        assert label("Lecture 4: Dynamic Programming") == (
            "Lecture 4: Dynamic Programming"
        )

    def test_emphasis_is_left_alone(self) -> None:
        """Deliberate: `*` and `_` restyle text, they do not act."""
        assert label("a_i * b_j") == "a_i * b_j"

    def test_surrounding_whitespace_goes(self) -> None:
        assert label("  spaced  out  ") == "spaced out"

    def test_a_pipe_cannot_add_a_table_column(self) -> None:
        """Labels end up in table cells — the corrections appendix — where a
        raw pipe splits the row and most renderers drop the overflow."""
        assert label("a|b") == "a\\|b"


class TestBody:
    def test_a_pipe_in_prose_is_left_alone(self) -> None:
        # Prose never sits in a cell, and the delimiter row a table needs
        # cannot form because `_LINE_START` neutralises runs of - and =.
        assert body("x | y") == "x | y"

    def test_newlines_survive(self) -> None:
        """Unlike `label`: a passage is prose and may hold a line break."""
        assert body("one\ntwo") == "one\ntwo"

    def test_a_heading_at_the_start_of_a_line_is_inert(self) -> None:
        assert body("text\n# Fake heading") == "text\n\\# Fake heading"

    def test_a_hash_mid_sentence_is_left_alone(self) -> None:
        """`C#` should not read as `C\\#`. Mid-line, a hash means nothing."""
        assert body("written in C# mostly") == "written in C# mostly"

    def test_indented_headings_are_caught_too(self) -> None:
        assert body("   # sneaky") == "   \\# sneaky"

    def test_tilde_fences_are_inert(self) -> None:
        """Escaping backticks alone would leave the other fence character
        able to swallow the rest of the document."""
        assert body("~~~\nfenced\n~~~") == "\\~~~\nfenced\n\\~~~"

    def test_backtick_fences_are_inert(self) -> None:
        assert body("```\nfenced\n```") == "\\`\\`\\`\nfenced\n\\`\\`\\`"

    def test_autolinks_are_inert(self) -> None:
        assert body("see <http://evil.test>") == "see \\<http://evil.test\\>"

    def test_a_heading_that_needs_no_hash_is_inert(self) -> None:
        """A line of `=` *under* text promotes it to a heading — restructuring
        the document without writing a single character `#` would catch."""
        assert body("Innocent line\n===") == "Innocent line\n\\==="
        assert body("Innocent line\n---") == "Innocent line\n\\---"

    def test_blockquotes_are_inert(self) -> None:
        assert body("> quoted at you") == "\\> quoted at you"

    def test_obsidian_wikilinks_and_embeds_are_inert(self) -> None:
        """A vault is the documented destination for this output, so the
        syntax that reaches *into* one matters as much as Markdown's own."""
        assert body("[[Secret Note]]") == "\\[\\[Secret Note\\]\\]"
        assert body("![[embed.png]]") == "!\\[\\[embed.png\\]\\]"

    def test_a_backslash_cannot_escape_the_escaping(self) -> None:
        """Without this, `\\[` in a transcript would arrive as a live `[`."""
        assert body("\\[not a link](x)") == "\\\\\\[not a link\\](x)"

    def test_ordinary_prose_is_untouched(self) -> None:
        text = "The key idea is that we remember answers, so we solve it once."
        assert body(text) == text


class TestSafeUrl:
    def test_an_ordinary_lecture_url_passes(self) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42"
        assert safe_url(url) == url

    def test_http_passes_too(self) -> None:
        assert safe_url("http://example.test/x") == "http://example.test/x"

    def test_no_url_stays_no_url(self) -> None:
        assert safe_url(None) is None

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
        ],
    )
    def test_schemes_that_are_not_the_web_are_refused(self, url: str) -> None:
        assert safe_url(url) is None

    def test_a_relative_url_is_refused(self) -> None:
        """No host to attribute it to, so there is nothing safe to link."""
        assert safe_url("/watch?v=x") is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.test/a b",
            "https://evil.test/a)b",
            "https://evil.test/a(b",
            "https://evil.test/a<b",
            'https://evil.test/a"b',
            "https://evil.test/a`b",
            "https://evil.test/a\nb",
        ],
    )
    def test_urls_that_would_break_out_of_a_link_are_refused(self, url: str) -> None:
        """A `)` ends the destination early and hands the rest back to the
        document as live markup."""
        assert safe_url(url) is None

    def test_the_scheme_is_matched_case_insensitively(self) -> None:
        assert safe_url("JavaScript:alert(1)") is None
        assert safe_url("HTTPS://example.test/x") == "HTTPS://example.test/x"

    @pytest.mark.parametrize(
        "url",
        [
            "https://[not-ipv6/watch?v=x",
            "https://[::1/watch?v=x",
            "https://[/x",
        ],
    )
    def test_a_url_the_parser_refuses_is_refused_here_too(self, url: str) -> None:
        """An unclosed IPv6 literal makes `urlsplit` itself raise.

        This used to escape the renderer as a bare `ValueError`, which is the
        docstring's promise broken for precisely the input it exists for:
        `webpage_url` is whatever the uploader typed. Returning None costs a
        link; raising cost the whole document.
        """
        assert safe_url(url) is None
