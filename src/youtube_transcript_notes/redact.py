"""Taking the secrets out of a URL before it is written down.

YouTube's caption URLs are signed, and a transport error quotes them — into
stderr, the `--json` envelope and any log that collects them. Redaction keeps
scheme, host and path (which request failed) and drops userinfo, query and
fragment (where secrets live). It happens before an error is constructed, so
no later surface has to remember to strip it.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

__all__ = ["redact", "redact_url"]

#: Any absolute http(s) URL in free text. Greedy on purpose: over-redacting a
#: trailing bracket costs less than leaking a credential.
_URL_IN_TEXT = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def redact_url(url: str) -> str:
    """``url`` with everything that could be a credential removed."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable url>"

    if not parts.scheme or not parts.netloc:
        return url

    # `hostname`, not `netloc`, which keeps `user:password@`.
    host = parts.hostname or ""

    try:
        port = parts.port
    except ValueError:
        # `port` parses lazily, so junk like `host:notaport` raises here.
        port = None

    if port:
        host = f"{host}:{port}"

    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def redact(text: str) -> str:
    """``text`` with every URL in it redacted.

    For third-party messages, which quote the URL they failed on.
    """
    return _URL_IN_TEXT.sub(lambda match: redact_url(match.group()), text)
