"""Unit tests for the migrated rule strategies (Phase 1c). Pins each string-transform
rule's contract and the registry dispatch, so the extracted classes cannot drift."""
from app.domain.rules.registry import standard_rule_engine

eng = standard_rule_engine()


def test_registry_contains_migrated_types_only():
    for rt in ["TRIM", "UPPERCASE", "LOWERCASE", "TITLE_CASE",
               "REMOVE_HYPHEN", "REMOVE_SPECIAL_CHARS", "REPLACE"]:
        assert rt in eng
    assert "FORMAT_DATE" not in eng        # not migrated yet -> engine keeps its branch
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


def test_second_batch_rule_types():
    assert eng.apply("PAD", {"side": "left", "length": 5, "char": "0"}, "42") == "00042"
    assert eng.apply("PAD", {"side": "right", "length": 4}, "ab") == "ab00"
    assert eng.apply("SUBSTRING", {"start": 1, "length": 3}, "abcdef") == "bcd"
    assert eng.apply("SUBSTRING", {"start": 2}, "abcdef") == "cdef"
    assert eng.apply("REGEX_REPLACE", {"pattern": r"\d+", "replace": "#"}, "a12b3") == "a#b#"
    assert eng.apply("REGEX_EXTRACT", {"pattern": r"(\d+)", "group": 1}, "x99y") == "99"
    assert eng.apply("REGEX_EXTRACT", {"pattern": r"z", "default": "NA"}, "x99y") == "NA"
    assert eng.apply("DEFAULT_VALUE", {"value": "D"}, "") == "D"
    assert eng.apply("DEFAULT_VALUE", {"value": "D"}, "keep") == "keep"
    assert eng.apply("CONSTANT", {"value": "K"}, "anything") == "K"
    assert eng.apply("VALUE_MAP", {"business": "C", "default": "O"}, "BUSINESS") == "C"
    assert eng.apply("VALUE_MAP", {"yes": "Y", "case_insensitive": False}, "Yes") == "Yes"
    assert eng.apply("VALUE_MAP", {"a": "1", "default": "D"}, "zzz") == "D"


def test_registry_size_grew_but_stateful_types_not_migrated():
    for rt in ["PAD", "SUBSTRING", "REGEX_REPLACE", "REGEX_EXTRACT",
               "DEFAULT_VALUE", "CONSTANT", "VALUE_MAP"]:
        assert rt in eng
    for rt in ["FORMAT_DATE", "CONCAT", "CASE_WHEN", "SELF_LOOKUP"]:
        assert rt not in eng           # still handled by engine's if/elif


def test_third_batch_numeric_and_boolean():
    assert eng.apply("NUMBER_FORMAT", {"decimals": 2}, "1234.5") == "1234.50"
    assert eng.apply("NUMBER_FORMAT", {}, "1,234.567") == "1234.57"
    assert eng.apply("NUMBER_FORMAT", {}, "abc") == "abc"
    assert eng.apply("ARITHMETIC", {"op": "add", "amount": "1.5"}, "2") == 3.5
    assert eng.apply("ARITHMETIC", {"op": "round"}, "2.6") == 3
    assert eng.apply("ARITHMETIC", {"op": "divide", "amount": 0}, "10") == 10.0  # guarded, passes value through
    assert eng.apply("SPLIT", {"separator": "-", "index": 1}, "a-b-c") == "b"
    assert eng.apply("SPLIT", {"index": 9}, "one two") == "one two"    # out of range -> value
    assert eng.apply("MAP_BOOLEAN", {}, "Yes") == "Y"
    assert eng.apply("MAP_BOOLEAN", {"false_output": "N"}, "no") == "N"
    assert eng.apply("MAP_BOOLEAN", {"default": "?"}, "maybe") == "?"


def test_registry_now_has_eighteen_and_stateful_still_out():
    for rt in ["NUMBER_FORMAT", "ARITHMETIC", "SPLIT", "MAP_BOOLEAN"]:
        assert rt in eng
    for rt in ["CONCAT", "CASE_WHEN", "SELF_LOOKUP", "COALESCE", "COUNTRY_ISO2"]:
        assert rt not in eng      # still engine branches (need shared helpers / country table)
