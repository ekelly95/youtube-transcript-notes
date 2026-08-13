"""Caption parsers.

Importing this package registers every built-in parser. The format imports
below look unused and are not: each runs a `@parsers.register` decorator.
"""

from .base import CaptionParser, parse_captions, parsers
from .json3 import parse_json3
from .srt import parse_srt
from .vtt import parse_vtt

__all__ = [
    "CaptionParser",
    "parse_captions",
    "parse_json3",
    "parse_srt",
    "parse_vtt",
    "parsers",
]
