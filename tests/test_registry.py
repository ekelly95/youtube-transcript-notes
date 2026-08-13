from __future__ import annotations

import pytest

from youtube_transcript_notes.errors import UnknownRenderer
from youtube_transcript_notes.registry import Registry


@pytest.fixture
def registry() -> Registry[str]:
    return Registry("renderer", UnknownRenderer)


class TestRegistration:
    def test_decorator_returns_the_component_unchanged(
        self, registry: Registry[str]
    ) -> None:
        @registry.register("markdown")
        class Markdown:
            pass

        assert registry.get("markdown") is Markdown

    def test_aliases_resolve_to_the_same_component(
        self, registry: Registry[str]
    ) -> None:
        registry.add("markdown", "component", "md")

        assert registry.get("md") == registry.get("markdown")

    def test_aliases_stay_out_of_the_canonical_names(
        self, registry: Registry[str]
    ) -> None:
        registry.add("markdown", "component", "md")

        assert registry.names() == ("markdown",)
        assert set(registry.keys()) == {"markdown", "md"}

    def test_duplicate_registration_fails_loudly(self, registry: Registry[str]) -> None:
        registry.add("markdown", "first")

        with pytest.raises(ValueError, match="already registered"):
            registry.add("markdown", "second")

    def test_a_duplicate_alias_also_fails(self, registry: Registry[str]) -> None:
        registry.add("markdown", "first", "md")

        with pytest.raises(ValueError, match="already registered"):
            registry.add("mdown", "second", "md")


class TestLookup:
    def test_unknown_name_raises_the_configured_error(
        self, registry: Registry[str]
    ) -> None:
        registry.add("markdown", "component")
        registry.add("plain", "component2")

        with pytest.raises(UnknownRenderer) as caught:
            registry.get("pdf")

        message = str(caught.value)
        assert "'pdf'" in message
        assert "markdown, plain" in message

    def test_the_error_does_not_chain_a_keyerror(self, registry: Registry[str]) -> None:
        # A KeyError in the traceback would bury the useful message.
        with pytest.raises(UnknownRenderer) as caught:
            registry.get("nope")

        assert caught.value.__cause__ is None


class TestCollectionProtocol:
    def test_membership_covers_aliases(self, registry: Registry[str]) -> None:
        registry.add("markdown", "component", "md")

        assert "md" in registry
        assert "markdown" in registry
        assert "pdf" not in registry

    def test_iteration_and_length_use_canonical_names(
        self, registry: Registry[str]
    ) -> None:
        registry.add("markdown", "one", "md")
        registry.add("plain", "two")

        assert list(registry) == ["markdown", "plain"]
        assert len(registry) == 2

    def test_registration_order_is_preserved(self, registry: Registry[str]) -> None:
        for name in ("plain", "markdown", "citation", "jsonl"):
            registry.add(name, name.upper())

        assert registry.names() == ("plain", "markdown", "citation", "jsonl")
