"""Putting text written by a stranger into a document without giving it power.

Title, channel, chapters and transcript are all the uploader's text. Without
escaping they could open headings, embed images (a read receipt), add links,
or make `webpage_url` a `javascript:` URL behind every timestamp.

The rule is *neutralise what acts, leave alone what merely reads*: link and
image syntax, raw HTML, autolinks, code fences and headings are escaped;
asterisks and underscores are not. Escaping lives in the renderers because
only they know whether they are writing Markdown. Text that addresses an agent
is a separate problem — see `render.context`.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

__all__ = ["body", "body_resumed", "label", "safe_url"]

#: Escaped everywhere: brackets build links and images, angle brackets open
#: HTML and autolinks, a backtick opens code and fences, and the backslash must
#: be escaped so it cannot escape the escape. `str.translate` visits each
#: character once, so it never rewrites a backslash it just inserted.
_ALWAYS = {
    "\\": "\\\\",
    "[": "\\[",
    "]": "\\]",
    "<": "\\<",
    ">": "\\>",
    "`": "\\`",
}
_ACTIVE = str.maketrans(_ALWAYS)

#: Labels also land in table cells (the corrections appendix), where ``|`` would
#: add a column. Prose never sits in a cell, so `body` leaves pipes alone.
_ACTIVE_IN_LABEL = str.maketrans({**_ALWAYS, "|": "\\|"})

#: Neutralised only at the start of a line, where they mean something (a
#: lecture on C# stays readable). ``~`` is the other fence character; ``=`` and
#: ``-`` underline text into a heading. Escaping the first character of the run
#: is enough to break each construct.
_LINE_START = re.compile(r"^(\s*)([#~=-]+)", re.MULTILINE)

_WHITESPACE = re.compile(r"\s+")


def label(text: str) -> str:
    """Source text safe to use as a heading, byline, or link label.

    Flattened to one line as well as escaped: a newline in a title would
    otherwise hand the uploader every line that follows.
    """
    return _WHITESPACE.sub(" ", text).strip().translate(_ACTIVE_IN_LABEL)


def body(text: str) -> str:
    """Source prose, safe to place in a Markdown paragraph.

    Newlines survive, so line-start constructs are neutralised instead.
    """
    return _LINE_START.sub(_escape_line_start, text.translate(_ACTIVE))


#: `_LINE_START`, minus the match at position zero — see `body_resumed`.
_LINE_RESUMED = re.compile(r"(?<=\n)(\s*)([#~=-]+)")


def body_resumed(text: str) -> str:
    """`body`, for text that resumes a line already begun.

    Inline corrections split a passage; the piece after a split starts
    mid-line, where a leading ``--`` is punctuation, not markup.
    """
    return _LINE_RESUMED.sub(_escape_line_start, text.translate(_ACTIVE))


def _escape_line_start(match: re.Match[str]) -> str:
    indent, marker = match.groups()
    return f"{indent}\\{marker}"


#: The only schemes worth linking to — an allowlist, not a blocklist.
_SAFE_SCHEMES = frozenset({"http", "https"})

#: Characters that would end a Markdown link destination early. The
#: ``watch?v=`` links this tool emits contain none of them.
_BREAKS_OUT = frozenset(' \t\n\r()<>"`\\')


def safe_url(url: str | None) -> str | None:
    """``url`` if it is one we are willing to link to, otherwise None.

    None rather than an error: a source with an unusable URL is still worth
    rendering, just without links.
    """
    if url is None:
        return None
    if any(character in _BREAKS_OUT for character in url):
        return None

    try:
        parsed = urlsplit(url)
    except ValueError:
        # e.g. `https://[nope/x`, an unclosed IPv6 literal.
        return None

    if parsed.scheme.lower() not in _SAFE_SCHEMES or not parsed.netloc:
        return None
    return url
