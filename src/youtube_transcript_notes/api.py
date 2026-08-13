"""The public entry point.

```python
fetcher = TranscriptFetcher()

manifest = fetcher.list("lectures/6006-lec1")   # nothing downloaded yet
for handle in manifest:
    print(handle.track.describe())

lecture = manifest.find(["en"]).fetch()
print(MarkdownRenderer().render(lecture))
```

`fetch` is a shortcut defined in terms of the primitives rather than the other
way round, so anything the convenience method can do is also reachable a step
at a time — which is what makes the discovery stage worth having.
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
    """Turns a source of lectures into readable, citable text."""

    def __init__(
        self,
        provider: SourceProvider | str | None = None,
        clock: Callable[[], datetime] | None = None,
        cache: Cache | None = None,
    ) -> None:
        """`provider` may be an instance, a registered name, or omitted.

        Omitted is the usual case: the provider is chosen per source, so a
        path and a video URL can be handed to the same object — which is also
        why `cache` is configured here rather than on a provider the caller
        never constructs.
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
        """Turn a playlist into the lectures it holds; anything else comes
        back alone. Costs at most one request and fetches nothing.

        A source nothing recognises also comes back alone rather than
        raising: expansion answers "what does this name", not "is this
        valid", and the `list` call that follows reports an unrecognised
        source once, where every other per-source failure is reported.
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
        """Discover, choose the best track, and reassemble it into a lecture."""
        return self.list(source).find(languages, tiers).fetch(policy, glossary)
