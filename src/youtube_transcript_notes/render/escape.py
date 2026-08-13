"""Putting text written by a stranger into a document without giving it power.

Everything a lecture is made of — its title, its channel, its chapter names,
every word of its transcript — was written by whoever published it. Renderers
used to interpolate all of it raw, which meant the uploader was not supplying
*text* to a Markdown document, they were supplying *Markdown*. A title
containing a newline could open its own headings. A caption could carry an
image whose URL a viewer fetches the moment the note is opened, which is a
read receipt, and a link that phishes. `webpage_url` could be `javascript:`,
and because `Locator` builds every deep link on top of it, one bad URL poisons
every timestamp in the document rather than just the byline.

The rule here is *neutralise what acts, leave alone what merely reads*. A
transcript is prose, and prose is full of asterisks and underscores that mean
nothing; backslashing them would make the source view unreadable to defend
against emphasis, which is not a threat. What is escaped is the set that
restructures a document or reaches the network: link and image syntax, raw
HTML, autolinks, code fences, and headings.

Which is why escaping happens *here* rather than at the parsers. A parser is
supposed to be faithful to what the source published, and `plain` and
`citation` render to `.txt`, where a backslash is not an escape but a
backslash. Only a renderer knows whether it is writing Markdown.

None of this makes hostile text safe to *obey* — see `render.context` for the
separate problem of a transcript that talks to an agent rather than a reader.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

__all__ = ["body", "body_resumed", "label", "safe_url"]

#: Escaped everywhere, because each one begins something Markdown acts on:
#: ``[`` and ``]`` build links, references and — after a ``!`` — images;
#: ``<`` and ``>`` open raw HTML and autolinks; a backtick opens code, and
#: killing it here also kills ``` fences. The backslash comes first in spirit:
#: without escaping it, ``\`` in a transcript would escape the escape.
#:
#: `str.translate` rather than chained `str.replace`, because translate visits
#: each character once. Chained replaces would rewrite the backslashes they
#: had just inserted.
_ALWAYS = {
    "\\": "\\\\",
    "[": "\\[",
    "]": "\\]",
    "<": "\\<",
    ">": "\\>",
    "`": "\\`",
}
_ACTIVE = str.maketrans(_ALWAYS)

#: `label` output must also survive a table cell, where a ``|`` adds a column
#: and most renderers silently drop the overflow — the corrections appendix
#: builds its rows from labels. `body` leaves pipes alone: prose never sits in
#: a cell, and the delimiter row a table needs cannot form because
#: `_LINE_START` already neutralises runs of ``-`` and ``=``.
_ACTIVE_IN_LABEL = str.maketrans({**_ALWAYS, "|": "\\|"})

#: Neutralised only at the start of a line, where they mean something. A ``#``
#: mid-sentence is a sharp or a number, and a lecture on C# should not read as
#: ``C\#``.
#:
#: ``~`` is here because escaping backticks alone would leave the other fence
#: character able to swallow the rest of the document. ``=`` and ``-`` are here
#: for the heading that needs no ``#`` at all: a line of them *underneath* text
#: promotes that text to a heading, so a transcript could restructure a
#: document without ever writing a character this file would otherwise catch.
#:
#: Escaping the first character of the run is enough. ``\===`` is no longer a
#: line of only ``=``, which is what the heading form requires, and ``\~~~``
#: is two tildes short of a fence.
_LINE_START = re.compile(r"^(\s*)([#~=-]+)", re.MULTILINE)

_WHITESPACE = re.compile(r"\s+")


def label(text: str) -> str:
    """Source text safe to use as a heading, byline, or link label.

    Flattened to a single line as well as escaped, and that is the half that
    matters most: the injection is not a clever character inside the title, it
    is the newline *after* it. ``# {title}`` puts the uploader in charge of one
    heading; a title containing a newline puts them in charge of every line
    that follows.
    """
    return _WHITESPACE.sub(" ", text).strip().translate(_ACTIVE_IN_LABEL)


def body(text: str) -> str:
    """Source prose, safe to place in a Markdown paragraph.

    Newlines survive here — a passage is prose and may legitimately hold one —
    so line-start constructs are neutralised rather than flattened away.
    """
    return _LINE_START.sub(_escape_line_start, text.translate(_ACTIVE))


#: The constructs `_LINE_START` neutralises, matched only after a real
#: newline. What is missing is the match at position zero, and that is the
#: point — see `body_resumed`.
_LINE_RESUMED = re.compile(r"(?<=\n)(\s*)([#~=-]+)")


def body_resumed(text: str) -> str:
    """`body`, for text that resumes a line already begun.

    An inline correction is written by splitting the passage and putting the
    note between the pieces. The piece after the split starts mid-line, where
    its first characters mean nothing to Markdown — escaping them as if they
    opened a line turned ``one]--`` into ``one]\\--``. Interior newlines still
    get `body`'s full treatment.
    """
    return _LINE_RESUMED.sub(_escape_line_start, text.translate(_ACTIVE))


def _escape_line_start(match: re.Match[str]) -> str:
    indent, marker = match.groups()
    return f"{indent}\\{marker}"


#: The only schemes worth linking to. An allowlist and not a blocklist of
#: ``javascript:``/``data:``, because the next scheme worth refusing is one
#: nobody has thought of yet, and a blocklist would already have shipped.
_SAFE_SCHEMES = frozenset({"http", "https"})

#: Characters that end a Markdown link destination early, handing whatever
#: follows back to the document as live markup. Legitimate lecture URLs — the
#: ``watch?v=`` links this actually emits — contain none of them.
_BREAKS_OUT = frozenset(' \t\n\r()<>"`\\')


def safe_url(url: str | None) -> str | None:
    """``url`` if it is one we are willing to link to, otherwise None.

    None rather than a raised error on purpose: a lecture whose URL is
    unusable is still a perfectly good lecture, and the renderers already know
    how to write a byline or a timestamp with no link behind it. Refusing the
    link loses a convenience; trusting it would spend the reader's browser on
    whatever the uploader chose.
    """
    if url is None:
        return None
    if any(character in _BREAKS_OUT for character in url):
        return None

    try:
        parsed = urlsplit(url)
    except ValueError:
        # A URL malformed enough that the standard library refuses to read it —
        # `https://[nope/x`, an unclosed IPv6 literal. This used to escape as a
        # bare `ValueError` out of the renderer, which broke the promise in the
        # docstring above and did it for exactly the input that promise is for:
        # `webpage_url` is the uploader's to type. A URL nothing can parse is
        # certainly not one to link to.
        return None

    if parsed.scheme.lower() not in _SAFE_SCHEMES or not parsed.netloc:
        return None
    return url
