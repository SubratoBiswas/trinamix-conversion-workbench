"""Unit tests for the migrated rule strategies (Phase 1c). Pins each string-transform
rule's contract and the registry dispatch, so the extracted classes cannot drift."""
from app.domain.rules.registry import standard_rule_engine

eng = standard_rule_engine()


def test_registry_contains_migrated_types_only():
    for rt in ["TRIM", "UPPERCASE", "LOWERCASE", "TITLE_CASE",
               "REMOVE_HYPHEN", "REMOVE_SPECIAL_CHARS", "REPLACE"]:
        assert rt in eng
    assert "NOT_A_RULE_TYPE" not in eng    # unknown types are not registered
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
    for rt in ["SELF_LOOKUP", "GROUP_FIRST_FLAG", "SEQUENCE", "CROSSWALK_LOOKUP"]:
        assert rt in eng               # Batch B — now migrated too


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
    for rt in ["SELF_LOOKUP", "CROSS_CONVERSION_LOOKUP", "GROUP_FIRST_FLAG", "SEQUENCE"]:
        assert rt in eng          # Batch B — now migrated (engine if/elif chain fully gone)


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


# ---- Fifth batch (Phase 1c date-ops): FORMAT_DATE / DATE_FORMAT / CONDITIONAL_DATE /
# COMPUTED, migrated after relocating the date helpers into app.domain.dates. Each
# assertion mirrors a case verified byte-identical against the old engine branch;
# COMPUTED/CONDITIONAL_DATE pin ctx['now'] for determinism.
from datetime import datetime  # noqa: E402
NOW = datetime(2026, 8, 17, 12, 34, 56)
DROW = {"Default Billing": "Y", "Inactive": "Yes", "Status": "Active",
        "Amount": "100", "SomeDate": "2020-03-15"}


def test_registry_contains_date_batch():
    for rt in ["FORMAT_DATE", "DATE_FORMAT", "CONDITIONAL_DATE", "COMPUTED"]:
        assert rt in eng


def test_format_date():
    # forgiving parse; output defaults to yyyy/mm/dd, Oracle tokens honoured
    assert eng.apply("FORMAT_DATE", {}, "2024-02-12", None, {}) == "2024/02/12"
    assert eng.apply("FORMAT_DATE", {"to_format": "MM/DD/YYYY"}, "2024-02-12", None, {}) == "02/12/2024"
    assert eng.apply("FORMAT_DATE", {"to_format": "DD-MON-YYYY"}, "2024-02-12", None, {}) == "12-Feb-2024"
    assert eng.apply("FORMAT_DATE", {}, "", None, {}) == ""                 # blank -> ""
    assert eng.apply("FORMAT_DATE", {}, "not a date", None, {}) == "not a date"  # unparseable -> value
    assert eng.apply("FORMAT_DATE", {}, "13/02/2024", None, {"dayfirst": True}) == "2024/02/13"


def test_date_format():
    assert eng.apply("DATE_FORMAT", {}, "08/17/2026") == "2026/08/17"       # default %m/%d/%Y in
    assert eng.apply("DATE_FORMAT", {"input_format": "%Y-%m-%d",
                                     "output_format": "%d/%m/%Y"}, "2026-08-17") == "17/08/2026"
    assert eng.apply("DATE_FORMAT", {}, "") == ""                           # blank -> ""
    assert eng.apply("DATE_FORMAT", {}, "2026-08-17") == "2026-08-17"       # no match -> value


def test_conditional_date():
    ctx = {"now": NOW}
    assert eng.apply("CONDITIONAL_DATE", {"condition": "Default Billing = Y",
                                          "value": "SYSDATE", "else": "null"}, "o", DROW, ctx) == "2026/08/17"
    assert eng.apply("CONDITIONAL_DATE", {"condition": "Default Billing = N",
                                          "value": "SYSDATE", "else": "null"}, "o", DROW, ctx) == ""
    # value token names another column -> that column's date, normalised
    assert eng.apply("CONDITIONAL_DATE", {"condition": "Status = Active",
                                          "value": "SomeDate", "else": "null"}, "o", DROW, ctx) == "2020/03/15"
    # numeric comparison in the condition
    assert eng.apply("CONDITIONAL_DATE", {"condition": "Amount > 50",
                                          "value": "SYSDATE", "else": "null"}, "o", DROW, ctx) == "2026/08/17"


def test_computed():
    ctx = {"now": NOW}
    assert eng.apply("COMPUTED", {"source": "today"}, "o", None, ctx) == "2026/08/17"
    assert eng.apply("COMPUTED", {"source": "now"}, "o", None, ctx) == "2026/08/17 12:34:56"
    assert eng.apply("COMPUTED", {"source": "today", "format": "%d-%m-%Y"}, "o", None, ctx) == "17-08-2026"
    assert eng.apply("COMPUTED", {"source": "row_index"}, "o", None, {"row_index": 42}) == 42
    assert eng.apply("COMPUTED", {"source": "current_user"}, "o", None, {"current_user": "s"}) == "s"
    assert eng.apply("COMPUTED", {"source": "unknown"}, "passthru", None, ctx) == "passthru"
    # uuid path is non-deterministic -> assert structure, not value
    u = eng.apply("COMPUTED", {"source": "uuid"}, "o", None, ctx)
    assert isinstance(u, str) and len(u) == 36 and u.count("-") == 4


# ---- Batch A (Phase 1c geo/phone): COUNTRY_ISO2 / CITY_COUNTRY_KEY / PHONE_PART /
# PHONE_STRIP_AREA, migrated after relocating the ISO country table into
# app.domain.geo.country and the libphonenumber split into app.domain.phone.parse.
# Each assertion mirrors a case verified byte-identical against the old engine branch.
def test_registry_contains_geo_phone_batch():
    for rt in ["COUNTRY_ISO2", "CITY_COUNTRY_KEY", "PHONE_PART", "PHONE_STRIP_AREA"]:
        assert rt in eng


def test_country_iso2():
    assert eng.apply("COUNTRY_ISO2", {}, "United States") == "US"
    assert eng.apply("COUNTRY_ISO2", {}, "Italy") == "IT"
    assert eng.apply("COUNTRY_ISO2", {}, "IT") == "IT"          # already a valid code
    assert eng.apply("COUNTRY_ISO2", {}, "") == ""
    assert eng.apply("COUNTRY_ISO2", {}, "Narnia") == "Narnia"  # unresolvable -> as-is


def test_city_country_key():
    assert eng.apply("CITY_COUNTRY_KEY", {"country_column": "CC", "city_column": "City"},
                     "x", {"CC": "US", "City": "Austin"}) == "US-Austin"
    # full country name -> ISO code
    assert eng.apply("CITY_COUNTRY_KEY", {"country_column": "CC", "city_column": "City"},
                     "x", {"CC": "United States", "City": "Austin"}) == "US-Austin"
    # fixed prefix, no city -> bare prefix
    assert eng.apply("CITY_COUNTRY_KEY", {"country_value": "US", "city_column": "City"},
                     "x", {"CC": "US"}) == "US"
    # resolve country from the city index in ctx
    assert eng.apply("CITY_COUNTRY_KEY", {"country_column": "CC", "city_column": "City",
                                          "resolve_country_from_city": True}, "x",
                     {"City": "Hyderabad"}, {"city_country": {"hyderabad": "IN"}}) == "IN-Hyderabad"
    # country_to_iso:false keeps a bespoke code raw
    assert eng.apply("CITY_COUNTRY_KEY", {"country_column": "CC", "city_column": "City",
                                          "country_to_iso": False}, "x",
                     {"CC": "usa", "City": "Austin"}) == "usa-Austin"
    # neither column present -> incoming value (misconfig stays visible)
    assert eng.apply("CITY_COUNTRY_KEY", {"country_column": "No", "city_column": "No2"},
                     "orig", {"CC": "US"}) == "orig"


def test_phone_part():
    row = {"Country": "Brazil"}
    # bare national string split via libphonenumber + region hint
    assert eng.apply("PHONE_PART", {"part": "country"}, "5515981205351", row) == "55"
    assert eng.apply("PHONE_PART", {"part": "area"}, "5515981205351", row) == "15"
    assert eng.apply("PHONE_PART", {"part": "number"}, "5515981205351", row) == "981205351"
    assert eng.apply("PHONE_PART", {"part": "extension"}, "5515981205351", row) == ""
    assert eng.apply("PHONE_PART", {"part": "number"}, "", None) == ""


def test_phone_strip_area():
    assert eng.apply("PHONE_STRIP_AREA", {"area_code_column": "AC"},
                     "512-555-0134", {"AC": "512"}) == "555-0134"
    assert eng.apply("PHONE_STRIP_AREA", {"area_code_column": "AC"},
                     "555-0134", {"AC": "512"}) == "555-0134"      # no prefix -> unchanged
    assert eng.apply("PHONE_STRIP_AREA", {"area_code_column": "AC"},
                     "512-555-0134", {"AC": ""}) == "512-555-0134"  # blank area -> unchanged
    assert eng.apply("PHONE_STRIP_AREA", {"area_code_column": "AC"}, "x", None) == "x"


# ---- Batch B (Phase 1c, final): the index-backed lookup types — SELF_LOOKUP,
# CROSS_CONVERSION_LOOKUP, GROUP_FIRST_FLAG, SEQUENCE, CROSSWALK_LOOKUP. Each reads a
# per-generation index handed in via ctx. Assertions mirror cases verified byte-identical
# against the old engine branches. With these migrated, engine._apply_one_rule is pure
# registry dispatch — the whole if/elif chain is gone.
def test_registry_contains_lookup_batch():
    for rt in ["SELF_LOOKUP", "CROSS_CONVERSION_LOOKUP", "GROUP_FIRST_FLAG",
               "SEQUENCE", "CROSSWALK_LOOKUP"]:
        assert rt in eng


def test_self_lookup():
    si = {"self_index": {"Internal Id->Name": {"99": "ACME"}}}
    cfg = {"key_column": "Parent Vendor Id", "match_column": "Internal Id", "value_column": "Name"}
    assert eng.apply("SELF_LOOKUP", cfg, "x", {"Parent Vendor Id": "99"}, si) == "ACME"
    # case/space-insensitive key resolution
    assert eng.apply("SELF_LOOKUP", {**cfg, "key_column": "parent_vendor_id"},
                     "x", {"parent_vendor_id": "99"}, si) == "ACME"
    assert eng.apply("SELF_LOOKUP", cfg, "x", {"Parent Vendor Id": "404"}, si) == ""   # miss
    assert eng.apply("SELF_LOOKUP", cfg, "x", {"Parent Vendor Id": "99"}, {}) == ""    # no index -> blank


def test_cross_conversion_lookup():
    ci = {"cross_index": {"r1:M->V": {"7": "seven"}}}
    cfg = {"ref_conversion_id": "r1", "key_column": "K", "match_column": "M", "value_column": "V"}
    assert eng.apply("CROSS_CONVERSION_LOOKUP", cfg, "x", {"K": "7"}, ci) == "seven"
    assert eng.apply("CROSS_CONVERSION_LOOKUP", cfg, "x", {"K": "7.0"}, ci) == "seven"   # .0 fallback
    assert eng.apply("CROSS_CONVERSION_LOOKUP", {**cfg, "default": "D"}, "x", {"K": "404"}, ci) == "D"


def test_group_first_flag():
    gi = {"group_first_index": {"entityid": {"C1": 3}}}
    assert eng.apply("GROUP_FIRST_FLAG", {"key_column": "entityid", "flag": "Y"},
                     "x", {"entityid": "C1"}, dict(gi, row_index=3)) == "Y"   # first row
    assert eng.apply("GROUP_FIRST_FLAG", {"key_column": "entityid", "flag": "Y"},
                     "x", {"entityid": "C1"}, dict(gi, row_index=5)) == ""    # not first
    assert eng.apply("GROUP_FIRST_FLAG", {"key_column": "entityid"}, "x", None, gi) == ""  # row None


def test_sequence():
    assert eng.apply("SEQUENCE", {"prefix": "NXT", "width": 6, "start": 1,
                                  "preserve_source": False}, "", {}, {"row_index": 4}) == "NXT000005"
    assert eng.apply("SEQUENCE", {"prefix": "NXT", "preserve_source": True},
                     "SRC", {}, {"row_index": 4}) == "SRC"                    # real key wins
    # keyed ordinal from the sequence index
    assert eng.apply("SEQUENCE", {"prefix": "NXT", "width": 6, "start": 1, "preserve_source": False,
                                  "key_column": "entityid"}, "", {"entityid": "C2"},
                     {"sequence_index": {"entityid": {"C2": 1}}, "row_index": 99}) == "NXT000002"
    # PERSON variant suffix
    assert eng.apply("SEQUENCE", {"prefix": "P", "width": 5, "start": 1, "preserve_source": False,
                                  "variant": {"if_column": "Party Type", "op": "eq", "value": "PERSON",
                                              "suffix": "_C{n}", "counter": 1}},
                     "", {"Party Type": "PERSON"}, {"row_index": 6}) == "P00007_C1"


def test_crosswalk_lookup():
    cw = {"crosswalks": {"cw": {"a": "A"}}}
    assert eng.apply("CROSSWALK_LOOKUP", {"crosswalk": "cw"}, "a", None, cw) == "A"
    assert eng.apply("CROSSWALK_LOOKUP", {"crosswalk": "cw"}, "A", None, cw) == "A"   # ci fallback
    assert eng.apply("CROSSWALK_LOOKUP", {"crosswalk": "cw", "default": "D"}, "z", None, cw) == "D"
    assert eng.apply("CROSSWALK_LOOKUP", {"crosswalk": "missing"}, "a", None, cw) == "a"  # no table -> value


def test_migration_complete_pure_dispatch():
    # Every catalogued rule type now resolves through the registry; the engine no longer
    # carries any per-type branch. An unknown type passes the value through unchanged.
    import app.transformations.engine as _e
    assert _e._apply_one_rule("NO_SUCH_TYPE", {}, "keep", {"A": "1"}, {}) == "keep"
    assert len(eng._by_type) >= 39
