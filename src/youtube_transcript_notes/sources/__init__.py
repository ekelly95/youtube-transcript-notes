"""Where lectures come from.

Importing this package registers every built-in provider. The import below
looks unused and is not: it runs a `@providers.register` decorator.
"""

from .base import Expansion, SourceProvider, get_provider, provider_for, providers
from .local import LocalProvider
from .youtube import YouTubeProvider

__all__ = [
    "Expansion",
    "LocalProvider",
    "SourceProvider",
    "YouTubeProvider",
    "get_provider",
    "provider_for",
    "providers",
]
