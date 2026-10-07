"""The public entry point.

```python
from youtube_transcript_notes import TranscriptFetcher, get_renderer

fetcher = TranscriptFetcher()
manifest = fetcher.list("HtSuA80QTyo")   # nothing downloaded yet
for handle in manifest:
    print(handle.track.describe())

lecture = manifest.find(["en"]).fetch()
print(get_renderer("markdown").render(lecture))
```

`fetch` is built from the primitives, so everything it does is also reachable
a step at a time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from .cache import Cache
from .errors import UnknownProvider
from .models import Lecture, TrustTier
from .refine import Glossary, ReflowPolicy
from .resolve import TrackManifest
from .sources import Expansion, SourceProvider, get_provider, provider_for

__all__ = ["TranscriptFetcher"]


class TranscriptFetcher:
    """Turns a source into readable, citable text."""

    def __init__(
        self,
        provider: SourceProvider | str | None = None,
        clock: Callable[[], datetime] | None = None,
        cache: Cache | None = None,
    ) -> None:
        """`provider` may be an instance, a registered name, or omitted.

        Omitted is the usual case: the provider is chosen per source, so a path
        and a video URL can go to the same fetcher.
        """
        self._clock = clock
        self._cache = cache
        self._provider = (
            get_provider(provider, clock=clock, cache=cache)
            if isinstance(provider, str)
            else provider
        )

    def provider_for(self, source: str) -> SourceProvider:
        return self._provider or provider_for(
            source, clock=self._clock, cache=self._cache
        )

    def expand(self, source: str) -> Expansion:
        """Turn a playlist into its videos; anything else comes back alone.

        An unrecognised source also comes back alone rather than raising, so
        `list` reports it once alongside every other per-source failure.
        """
        try:
            provider = self.provider_for(source)
        except UnknownProvider:
            return Expansion(sources=(source,))
        return provider.expand(source)

    def list(self, source: str) -> TrackManifest:
        """Discover what transcripts exist, without downloading any of them."""
        return self.provider_for(source).list(source)

    def fetch(
        self,
        source: str,
        languages: Sequence[str] = ("en",),
        tiers: Sequence[TrustTier] | None = None,
        policy: ReflowPolicy | None = None,
        glossary: Glossary | None = None,
    ) -> Lecture:
        """Discover, choose the best track, and reassemble it."""
        return self.list(source).find(languages, tiers).fetch(policy, glossary)
