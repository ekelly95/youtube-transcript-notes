"""URL redaction, rule by rule.

The provider tests prove a secret does not reach stderr or the JSON envelope.
These prove why, so a failure names the rule that broke rather than reporting
that some output changed.
"""

from __future__ import annotations

import pytest

from youtube_transcript_notes.redact import redact, redact_url


class TestRedactUrl:
    def test_the_query_goes(self) -> None:
        assert redact_url("https://host.test/a/b?sig=SECRET&t=1") == (
            "https://host.test/a/b"
        )

    def test_the_fragment_goes(self) -> None:
        assert redact_url("https://host.test/a#token=SECRET") == "https://host.test/a"

    def test_userinfo_goes(self) -> None:
        """The credential most worth losing, and the one `netloc` keeps."""
        assert redact_url("https://user:pw@host.test/a") == "https://host.test/a"

    def test_the_host_and_path_stay(self) -> None:
        """They say which request failed, which is the actionable part."""
        assert redact_url("https://www.youtube.com/api/timedtext?v=x&sig=y") == (
            "https://www.youtube.com/api/timedtext"
        )

    def test_a_port_survives(self) -> None:
        assert redact_url("http://host.test:8080/a?x=1") == "http://host.test:8080/a"

    def test_something_that_is_not_a_url_is_left_alone(self) -> None:
        """`_classify` is also called with video ids and logical labels."""
        assert redact_url("HtSuA80QTyo") == "HtSuA80QTyo"
        assert redact_url("HtSuA80QTyo (en, json3)") == "HtSuA80QTyo (en, json3)"

    def test_an_unparseable_url_is_not_passed_through(self) -> None:
        """A string this cannot read is exactly the one not to trust."""
        assert redact_url("https://[oops") == "<unparseable url>"

    def test_junk_where_a_port_belongs_does_not_raise(self) -> None:
        """`port` parses on access, not at `urlsplit`, so the guard around
        parsing did not cover it.

        This is the worst place for a raw traceback: redaction runs while an
        error is being built, so the one function meant to make a failure safe
        to print was itself the failure. The host and path still say which
        request went wrong, and the junk is dropped with the netloc it came in.
        """
        assert redact_url("https://host.test:notaport/a?sig=S") == (
            "https://host.test/a"
        )

    def test_a_message_quoting_a_url_with_a_bad_port_is_still_redacted(self) -> None:
        message = "HTTP Error 403: Forbidden for url: https://h.test:8x/a?sig=SECRET"

        assert "SECRET" not in redact(message)
        assert "403" in redact(message)


class TestRedactText:
    def test_a_url_quoted_inside_a_message_is_redacted(self) -> None:
        """yt-dlp quotes the URL it failed on, so redacting only the URL
        the tool passed around would not be enough."""
        message = "HTTP Error 403: Forbidden for url: https://h.test/a?sig=SECRET"

        assert redact(message) == "HTTP Error 403: Forbidden for url: https://h.test/a"

    def test_several_urls_in_one_message(self) -> None:
        message = "tried https://a.test/x?k=1 then https://b.test/y?k=2"

        assert redact(message) == "tried https://a.test/x then https://b.test/y"

    def test_the_diagnosis_around_the_url_survives(self) -> None:
        message = "HTTP Error 429: Too Many Requests for url: https://h.test/a?sig=S"
        redacted = redact(message)

        assert "429" in redacted
        assert "Too Many Requests" in redacted
        assert "sig=" not in redacted

    def test_text_with_no_url_is_unchanged(self) -> None:
        assert redact("yt-dlp could not extract player response") == (
            "yt-dlp could not extract player response"
        )

    @pytest.mark.parametrize("scheme", ["http", "https", "HTTPS"])
    def test_schemes_are_matched_case_insensitively(self, scheme: str) -> None:
        assert "SECRET" not in redact(f"at {scheme}://h.test/a?sig=SECRET")
