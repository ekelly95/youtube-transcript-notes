"""Turning raw cues into readable, citable prose."""

from .annotations import consume_markup
from .dedupe import dedupe_rolling_window, overlap_length
from .glossary import (
    Glossary,
    propose_corrections,
    read_corrections,
    read_glossary,
    terms_from,
)
from .reflow import ReflowPolicy, passage_end, policy_for, reflow, speech_end
from .sections import build_sections
from .sentences import cut_at, ends_sentence, looks_punctuated, split_at_sentences

__all__ = [
    "Glossary",
    "ReflowPolicy",
    "build_sections",
    "consume_markup",
    "cut_at",
    "dedupe_rolling_window",
    "ends_sentence",
    "looks_punctuated",
    "overlap_length",
    "passage_end",
    "policy_for",
    "propose_corrections",
    "read_corrections",
    "read_glossary",
    "reflow",
    "speech_end",
    "split_at_sentences",
    "terms_from",
]
