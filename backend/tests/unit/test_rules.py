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
    for rt in ["FORMAT_DATE", "CONDITIONAL_DATE", "COMPUTED", "SELF_LOOKUP"]:
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
    for rt in ["SELF_LOOKUP", "COUNTRY_ISO2", "CONDITIONAL_DATE", "CITY_COUNTRY_KEY"]:
        assert rt not in eng      # still engine branches (need ctx / country table)


# ---- Fourth batch (Phase 1c): row-aware "stateful" rules migrated onto the
# domain-context helpers. Each assertion mirrors a case verified byte-identical
# against the pre-migration engine branch in the differential harness.
ROW = {
    "A": "foo", "B": "bar", "City": "Austin", "Country Code": "US",
    "Employee_ID": "12345", "Worker Type": "E", "Supplier Name": "Acme Inc",
    "Alternate Name": "acme  inc ", "Default Billing": "Y", "Is Individual": "No",
    "internalid": "999",
}


def test_registry_contains_stateful_batch():
    for rt in ["CONCAT", "COALESCE", "CONDITIONAL", "CASE_WHEN",
               "BLANK_IF_EQUALS", "PREFIX", "SUFFIX", "SUFFIX_WHEN"]:
        assert rt in eng


def test_concat():
    assert eng.apply("CONCAT", {"columns": ["A", "B"]}, "orig", ROW) == "foo bar"
    assert eng.apply("CONCAT", {"columns": ["A", "B"], "separator": "-"}, "o", ROW) == "foo-bar"
    # literal segment carries its own separator; require_all gates only column parts
    assert eng.apply("CONCAT", {"parts": [{"col": "A"}, {"literal": "_RS"}]}, "o", ROW) == "foo_RS"
    assert eng.apply("CONCAT", {"columns": ["A", "B"], "prefix": "P", "suffix": "S"}, "o", ROW) == "Pfoo barS"
    # all-blank inputs -> incoming value (misconfig stays visible), row None -> value
    assert eng.apply("CONCAT", {"columns": ["X", "Y"]}, "orig", {"X": "", "Y": ""}) == "orig"
    assert eng.apply("CONCAT", {"columns": ["A"]}, "orig", None) == "orig"
    # a half key under require_all is blanked
    assert eng.apply("CONCAT", {"columns": ["Country Code", "City"], "separator": "-",
                                "require_all": True}, "o", {"Country Code": "US", "City": ""}) == ""


def test_coalesce():
    assert eng.apply("COALESCE", {"columns": ["MISSING", "B"]}, "o", ROW) == "bar"
    assert eng.apply("COALESCE", {"columns": ["M1", "M2"]}, "fallback", ROW) == "fallback"
    assert eng.apply("COALESCE", {"columns": ["M1"], "default": "DEF"}, "", ROW) == "DEF"
    assert eng.apply("COALESCE", {"columns": ["A"]}, "orig", None) == "orig"


def test_blank_if_equals():
    # case- and whitespace-insensitive duplicate -> blank
    assert eng.apply("BLANK_IF_EQUALS", {"other_column": "Alternate Name"}, "Acme Inc", ROW) == ""
    assert eng.apply("BLANK_IF_EQUALS", {"other_column": "Supplier Name"}, "Different", ROW) == "Different"
    assert eng.apply("BLANK_IF_EQUALS", {}, "x", ROW) == "x"
    assert eng.apply("BLANK_IF_EQUALS", {"other_column": "A"}, "x", None) == "x"


def test_conditional():
    assert eng.apply("CONDITIONAL", {"if_column": "Worker Type", "equals": "E",
                                     "then": "IS_E", "else": "NOT_E"}, "o", ROW) == "IS_E"
    assert eng.apply("CONDITIONAL", {"if_column": "Worker Type", "equals": "C",
                                     "then": "IS_C", "else": "NOT_C"}, "o", ROW) == "NOT_C"
    # {Column} interpolation in the chosen result
    assert eng.apply("CONDITIONAL", {"if_column": "Worker Type", "equals": "E",
                                     "then": "E{Employee_ID}"}, "o", ROW) == "E12345"
    assert eng.apply("CONDITIONAL", {"equals": "x"}, "orig", ROW) == "orig"  # no if_column


def test_case_when():
    assert eng.apply("CASE_WHEN", {"branches": [{"if_column": "Worker Type", "op": "eq",
                                                 "value": "E", "then": "SA"}],
                                   "default": "DEF"}, "o", ROW) == "SA"
    assert eng.apply("CASE_WHEN", {"branches": [{"if_column": "internalid", "op": "gt",
                                                 "value": "500", "then": "BIG"}],
                                   "default": "SMALL"}, "o", ROW) == "BIG"
    # conjunction branch + interpolation
    assert eng.apply("CASE_WHEN", {"branches": [{"if_column": "Worker Type", "op": "eq",
                                                 "value": "E", "then": "E{Employee_ID}"}]},
                     "o", ROW) == "E12345"
    assert eng.apply("CASE_WHEN", {"branches": [], "default": "{City}"}, "o", ROW) == "Austin"
    assert eng.apply("CASE_WHEN", {"branches": [{"if_column": "Worker Type", "op": "eq",
                                                 "value": "Z", "then": "X"}]}, "passthru", ROW) == "passthru"


def test_prefix_suffix():
    assert eng.apply("PREFIX", {"prefix": "xx"}, "addr", ROW) == "xxaddr"
    assert eng.apply("PREFIX", {"prefix": "xx"}, "xxaddr", ROW) == "xxaddr"          # idempotent
    assert eng.apply("PREFIX", {"prefix": "xx", "skip_if_present": False}, "xxaddr", ROW) == "xxxxaddr"
    assert eng.apply("PREFIX", {"prefix": "xx"}, "  ", ROW) == "  "                  # skip_blank
    assert eng.apply("SUFFIX", {"suffix": "_S"}, "key", ROW) == "key_S"
    assert eng.apply("SUFFIX", {"suffix": "_S"}, "key_S", ROW) == "key_S"            # idempotent
    assert eng.apply("SUFFIX", {"suffix": "_S"}, "", ROW) == ""                      # skip_blank


def test_suffix_when():
    assert eng.apply("SUFFIX_WHEN", {"branches": [{"if_column": "Default Billing",
                                                   "op": "notblank", "suffix": "_b"}],
                                     "default_suffix": ""}, "key", ROW) == "key_b"
    assert eng.apply("SUFFIX_WHEN", {"branches": [{"if_column": "MISSING", "op": "notblank",
                                                   "suffix": "_b"}],
                                     "default_suffix": "_d"}, "key", ROW) == "key_d"
    assert eng.apply("SUFFIX_WHEN", {"branches": [], "default_suffix": ""}, "key", ROW) == "key"
    # idempotent: already carries the branch suffix
    assert eng.apply("SUFFIX_WHEN", {"branches": [{"if_column": "Default Billing",
                                                   "op": "notblank", "suffix": "_b"}]},
                     "key_b", ROW) == "key_b"
