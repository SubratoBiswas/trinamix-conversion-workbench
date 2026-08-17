"""Unit tests for the migrated rule strategies (Phase 1c). Pins each string-transform
rule's contract and the registry dispatch, so the extracted classes cannot drift."""
from app.domain.rules.registry import standard_rule_engine

eng = standard_rule_engine()


def test_registry_contains_migrated_types_only():
    for rt in ["TRIM", "UPPERCASE", "LOWERCASE", "TITLE_CASE",
               "REMOVE_HYPHEN", "REMOVE_SPECIAL_CHARS", "REPLACE"]:
        assert rt in eng
    assert "REGEX_REPLACE" not in eng      # not migrated yet -> engine keeps its branch
    assert "case-insensitive" not in eng


def test_string_ops():
    assert eng.apply("TRIM", {}, "  hi  ") == "hi"
    assert eng.apply("UPPERCASE", {}, "aB") == "AB"
    assert eng.apply("LOWERCASE", {}, "aB") == "ab"
    assert eng.apply("TITLE_CASE", {}, "john smith") == "John Smith"
    assert eng.apply("REMOVE_HYPHEN", {}, "a-b-c") == "abc"
    assert eng.apply("REMOVE_SPECIAL_CHARS", {}, "a!b@c#") == "abc"
    assert eng.apply("REMOVE_SPECIAL_CHARS", {"keep": "."}, "a.b!c") == "a.bc"
    assert eng.apply("REPLACE", {"find": "x", "replace": "y"}, "axb") == "ayb"


def test_none_and_nonstring_coercion():
    assert eng.apply("TRIM", {}, None) == ""
    assert eng.apply("UPPERCASE", {}, 123) == "123"
