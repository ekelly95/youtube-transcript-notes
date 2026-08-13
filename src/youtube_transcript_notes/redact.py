"""Taking the secrets out of a URL before it is written down.

YouTube's caption URLs are signed and expiring — `sources.youtube` says so
where it explains why the cache is never keyed on them. A signed URL is a
bearer credential for as long as it lasts, and the place they escape is the
one nobody thinks about: an error. A transport failure carried the whole URL
into the message *and* into the machine-readable context, and from there to
stderr, to a `--json` envelope, to whatever log or transcript collected it.

The impact is genuinely small — captions are public and the signature expires
in hours — which is exactly why it is worth fixing cheaply rather than
arguing about. Nothing needs the query string to diagnose a failure.

What survives is the scheme, the host and the path, because those say *which*
request failed, which is the part a reader can act on. What goes is userinfo,
query and fragment, because those are where anything secret lives.

Redaction happens before an error is constructed rather than when one is
printed. A secret that reaches the error object has already been copied into
`remedy`, and every later surface would have to remember to strip it again.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

__all__ = ["redact", "redact_url"]

#: Any absolute http(s) URL sitting in free text. Deliberately greedy about
#: what counts as the end of one — over-redacting a trailing bracket costs a
#: character of a diagnostic, and under-redacting costs the credential.
_URL_IN_TEXT = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def redact_url(url: str) -> str:
    """``url`` with everything that could be a credential removed.

    Anything unparseable is returned as the scheme and host alone, or as a
    placeholder if even that cannot be read — a string this function does not
    understand is exactly the string not to pass through untouched.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable url>"

    if not parts.scheme or not parts.netloc:
        return url

    # `hostname` rather than `netloc`: netloc keeps `user:password@`, which is
    # the credential most worth losing.
    host = parts.hostname or ""

    try:
        port = parts.port
    except ValueError:
        # `port` parses on access rather than at `urlsplit`, so the guard above
        # does not cover it, and `https://host:notaport/x` reached the caller as
        # a traceback out of the one function whose job is to make an error safe
        # to print. Junk where a port belongs is not a port: dropping it keeps
        # the scheme, host and path that say which request failed, and
        # `hostname` has already discarded the netloc that junk came from.
        port = None

    if port:
        host = f"{host}:{port}"

    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def redact(text: str) -> str:
    """``text`` with every URL in it redacted.

    For third-party exception messages, which quote the URL they failed on —
    ``HTTP Error 403: Forbidden for url: https://...&sig=...`` — so redacting
    the URL the tool passed around is not on its own enough.
    """
    return _URL_IN_TEXT.sub(lambda match: redact_url(match.group()), text)
