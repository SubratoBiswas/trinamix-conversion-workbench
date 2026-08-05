"""The rule preview shows a row that actually hits a branch.

Reported 05-Aug: a CASE/WHEN of Country -> SA/AE/IL/CL showed BLANK output on
every preview row, because the first rows sampled were Australia, USA, Brazil —
none in the rule — so every one fell to the blank default. The rule was correct;
the sample never exercised it, which reads as "the rule does nothing / not
appearing in live preview".

The preview now collects the values the rule branches on and prefers rows that
match them, so the transformation is visible. This pins the value-collection that
drives that, and re-confirms the engine produces the mapped value for a matching
row (so a blank preview means no matching row, not a broken rule).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routers.mapping import _values_the_rules_key_on, PreviewRule  # noqa: E402
from app.transformations.engine import apply_rule                      # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _country_rule():
    return PreviewRule(rule_type="CASE_WHEN", config={"branches": [
        {"if_column": "Country", "op": "eq", "value": "Saudi Arabia", "then": "SA"},
        {"if_column": "Country", "op": "eq", "value": "United Arab Emirates", "then": "AE"},
        {"if_column": "Country", "op": "eq", "value": "Israel", "then": "IL"},
        {"if_column": "Country", "op": "eq", "value": "Chile", "then": "CL"},
    ], "default": ""})


def test_it_collects_the_branch_values():
    want = _values_the_rules_key_on([_country_rule()], "Country")
    check("all four countries are wanted",
          want == {"Saudi Arabia", "United Arab Emirates", "Israel", "Chile"},
          f"got {want}")


def test_a_branch_on_another_column_is_not_wanted_for_this_source():
    r = PreviewRule(rule_type="CASE_WHEN", config={"branches": [
        {"if_column": "Worker_Type", "op": "contains", "value": "Employee", "then": "E"},
    ], "default": ""})
    want = _values_the_rules_key_on([r], "Country")
    check("a Worker_Type branch does not add to Country's wanted set", want == set(),
          f"got {want}")


def test_value_map_keys_are_wanted():
    r = PreviewRule(rule_type="VALUE_MAP",
                    config={"mapping": {"Y": "Yes", "N": "No"}})
    want = _values_the_rules_key_on([r], "Flag")
    check("value-map keys are wanted", want == {"Y", "N"}, f"got {want}")


def test_the_engine_maps_a_matching_row_so_blank_means_no_match():
    cfg = _country_rule().config
    check("Saudi Arabia -> SA",
          apply_rule("CASE_WHEN", cfg, "Saudi Arabia", row={"Country": "Saudi Arabia"}) == "SA")
    check("Australia -> blank default (no branch)",
          apply_rule("CASE_WHEN", cfg, "Australia", row={"Country": "Australia"}) == "")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\npreview matching-row selection holds")
