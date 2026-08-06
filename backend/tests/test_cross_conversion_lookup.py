"""CROSS_CONVERSION_LOOKUP — resolve a value from ANOTHER conversion of the project.

SELF_LOOKUP shipped broken for months because nothing built its index; the engine
returned its default on every real row. This rule is the cross-conversion twin, so
the same trap is guarded here: the rule resolves from ctx.cross_index, the generator
builds that index once before the row loop, and the config scanner finds the rules
that need it. If the index is absent the rule returns its default (an honest blank),
never a raw id where a name belongs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.transformations.engine import apply_pipeline          # noqa: E402
from app.services.output_service import _cross_conversion_configs  # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


_RULE = {"rule_type": "CROSS_CONVERSION_LOOKUP",
         "config": {"ref_conversion_id": "SUP1", "key_column": "parent_id",
                    "match_column": "internal_id", "value_column": "legal_name",
                    "default": ""}}
_CTX = {"cross_index": {"SUP1:internal_id->legal_name":
                        {"999": "Acme Global Inc", "1000": "Beta Corp"}}}


def test_resolves_from_another_conversions_index():
    check("hit returns the referenced value",
          apply_pipeline([_RULE], "", row={"parent_id": "999"}, ctx=_CTX) == "Acme Global Inc")


def test_miss_returns_default_not_the_id():
    check("an unmatched key returns the default (blank)",
          apply_pipeline([_RULE], "", row={"parent_id": "does-not-exist"}, ctx=_CTX) == "")


def test_no_index_returns_default():
    """The SELF_LOOKUP trap: no index built (preview, or a caller that didn't build
    one) must be a blank, never the raw id."""
    check("no cross_index -> default",
          apply_pipeline([_RULE], "", row={"parent_id": "999"}, ctx={}) == "")


def test_integral_spelling_of_a_netsuite_id():
    """NetSuite writes an id as 1000 in one column and 1000.0 in another once pandas
    has seen a blank. The lookup must still resolve."""
    check("1000.0 resolves to the 1000 entry",
          apply_pipeline([_RULE], "", row={"parent_id": "1000.0"}, ctx=_CTX) == "Beta Corp")


def test_blank_key_returns_default():
    check("a blank key returns the default",
          apply_pipeline([_RULE], "", row={"parent_id": ""}, ctx=_CTX) == "")


def test_config_scanner_finds_the_rules():
    """The generator scans pipelines to know which references to pre-index."""
    pipelines = {
        "f1": [{"rule_type": "CROSS_CONVERSION_LOOKUP", "config": _RULE["config"]}],
        "f2": [{"rule_type": "TRIM", "config": {}}],
        "f3": [{"rule_type": "cross_conversion_lookup",  # case-insensitive
                "config": {"ref_conversion_id": "SUP2", "match_column": "a",
                           "value_column": "b"}}],
    }
    got = _cross_conversion_configs(pipelines)
    check("finds both cross-conversion configs, ignores TRIM", len(got) == 2,
          f"got {len(got)}")
    refs = {c.get("ref_conversion_id") for c in got}
    check("both references present", refs == {"SUP1", "SUP2"}, f"got {refs}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\ncross-conversion lookup resolves and never leaks an id")
