"""One click from "Oracle will reject this" to the rule that stops it.

The Cleansing tab names exactly what will fail — "Inactive Date: 3,872 of 3,872 rows
are not in YYYY/MM/DD", "Taxpayer Country: 19 values exceed VARCHAR2(2)". Reading it is
half the job; every one of those has a single obvious remedy, and making the analyst
hand-build the same rule in another screen is asking them to retype what the tool
already knows.

The REFUSALS are the interesting half of this suite. Three findings look just as
fixable and are not: which accepted code a wrong value becomes is a business decision,
a number too big for its column is nearly always a mis-mapped source that truncation
would hide, and nothing can invent a missing mandatory value. A button that did any of
those quietly would turn a visible problem into an invisible one — which is the exact
failure this panel exists to prevent.

Pure: stdlib only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.column_rule_fix_service import (  # noqa: E402
    AUTO_FIXABLE, NOT_AUTO_FIXABLE, plan_fix, summarize,
)

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


# ── The three findings from the live screenshot ─────────────────────────────
def test_the_live_date_finding_becomes_a_date_format_rule():
    """Inactive Date / END_DATE_ACTIVE, 3,872 of 3,872 rows not in YYYY/MM/DD."""
    p = plan_fix({"field": "Inactive Date", "rule": "date_format",
                  "format_mask": "YYYY/MM/DD"})
    check("fixable", p["ok"] is True, f"got {p}")
    check("DATE_FORMAT", p["rule_type"] == "DATE_FORMAT")
    check("into the mask the TEMPLATE states, not a hardcoded one",
          p["rule_config"]["output_format"] == "%Y/%m/%d", f"got {p['rule_config']}")


def test_an_unusual_mask_is_honoured():
    p = plan_fix({"field": "X", "rule": "date_format", "format_mask": "YYYYMMDD"})
    check("%Y%m%d", p["rule_config"]["output_format"] == "%Y%m%d")


def test_the_date_fix_does_not_pin_an_input_format():
    """DATE_FORMAT leaves a value it cannot parse alone rather than mangling it.
    Pinning one input spelling would silently drop every other."""
    p = plan_fix({"field": "X", "rule": "date_format"})
    check("no input_format", "input_format" not in p["rule_config"], f"got {p}")


def test_the_live_length_findings_become_a_trim():
    """Taxpayer Country / VARCHAR2(2), 19 rows — 'Rwanda' is 6 characters."""
    p = plan_fix({"field": "Taxpayer Country", "rule": "max_length", "limit": 2})
    check("SUBSTRING", p["rule_type"] == "SUBSTRING")
    check("to the column's own limit", p["rule_config"]["length"] == 2)
    check("and warns that truncation loses meaning",
          "loses meaning" in p["description"], f"got {p['description']!r}")


def test_a_length_finding_with_no_limit_is_refused():
    """Better to refuse than to invent a limit and truncate to it."""
    p = plan_fix({"field": "X", "rule": "max_length"})
    check("refused", p["ok"] is False)


def test_a_non_numeric_column_gets_the_characters_stripped():
    p = plan_fix({"field": "Supplier Number", "rule": "numeric"})
    check("REMOVE_SPECIAL_CHARS", p["rule_type"] == "REMOVE_SPECIAL_CHARS")
    check("keeps the sign and the decimal point",
          p["rule_config"]["keep"] == "-.", f"got {p['rule_config']}")


def test_a_scale_finding_rounds_to_the_stated_places():
    p = plan_fix({"field": "Amount", "rule": "scale", "scale": 2})
    check("NUMBER_FORMAT", p["rule_type"] == "NUMBER_FORMAT")
    check("2 dp", p["rule_config"]["decimals"] == 2)


def test_a_do_not_populate_column_is_blanked():
    """Oracle's own comment says the column is not used, so blank is not a guess."""
    p = plan_fix({"field": "Attribute 24", "rule": "do_not_populate"})
    check("CONSTANT ''", p["rule_type"] == "CONSTANT" and p["rule_config"]["value"] == "")


# ── The refusals ────────────────────────────────────────────────────────────
def test_a_wrong_code_is_never_picked_automatically():
    p = plan_fix({"field": "Allow AWT", "rule": "value_set",
                  "allowed_values": ["Y", "N"]})
    check("refused", p["ok"] is False)
    check("and says where to go instead", "crosswalk" in p["reason"],
          f"got {p['reason']!r}")


def test_an_over_long_number_is_not_truncated():
    """The most important refusal. A number too big for its column is nearly always a
    mis-mapped source; truncating the digits produces a plausible wrong value and
    removes the only evidence of the real problem."""
    p = plan_fix({"field": "Batch Identifier", "rule": "precision", "precision": 18})
    check("refused", p["ok"] is False)
    check("and says why", "mis-mapped" in p["reason"], f"got {p['reason']!r}")


def test_a_missing_mandatory_value_cannot_be_invented():
    p = plan_fix({"field": "Supplier Name", "rule": "mandatory"})
    check("refused", p["ok"] is False)
    check("and names the two real options",
          "default" in p["reason"] and "Map" in p["reason"], f"got {p['reason']!r}")


def test_every_refusal_carries_a_reason():
    """A disabled button with no explanation reads as the tool having forgotten."""
    for rule in NOT_AUTO_FIXABLE:
        p = plan_fix({"field": "X", "rule": rule})
        check(f"{rule} explains itself", len(p.get("reason") or "") > 40)


def test_the_two_sets_do_not_overlap():
    check("disjoint", not (AUTO_FIXABLE & set(NOT_AUTO_FIXABLE)),
          "a rule that is both fixable and not is a coin toss at runtime")


def test_an_unknown_rule_is_refused_rather_than_guessed():
    p = plan_fix({"field": "X", "rule": "something_new"})
    check("refused", p["ok"] is False)


def test_a_finding_with_no_field_is_refused():
    check("refused", plan_fix({"rule": "numeric"})["ok"] is False)


# ── The bulk summary ────────────────────────────────────────────────────────
def test_a_bulk_summary_names_what_it_skipped():
    """'3 of 5 fixed' with no word on the other two reads as though they were fine."""
    plans = [plan_fix({"field": "A", "rule": "numeric"}),
             plan_fix({"field": "B", "rule": "max_length", "limit": 5}),
             plan_fix({"field": "C", "rule": "value_set"}),
             plan_fix({"field": "D", "rule": "mandatory"})]
    s = summarize(plans)
    check("2 fixable", s["fixable"] == 2, f"got {s}")
    check("2 skipped", s["skipped"] == 2)
    check("each skip carries its reason",
          all(x["reason"] for x in s["skipped_reasons"]), f"got {s['skipped_reasons']}")


def test_every_auto_fix_produces_a_registered_rule_type():
    """A rule_type the engine does not implement is accepted at save and then does
    nothing — the fix would look applied and change no data."""
    from app.models.transformation import RULE_TYPES
    samples = {"date_format": {"format_mask": "YYYY/MM/DD"},
               "max_length": {"limit": 10}, "numeric": {}, "scale": {"scale": 2},
               "do_not_populate": {}}
    for rule, extra in samples.items():
        p = plan_fix({"field": "X", "rule": rule, **extra})
        check(f"{rule} -> {p.get('rule_type')} registered",
              p["rule_type"] in RULE_TYPES, f"got {p.get('rule_type')}")


def test_every_fix_explains_itself_to_the_analyst():
    for rule in AUTO_FIXABLE:
        p = plan_fix({"field": "Some Field", "rule": rule,
                      "limit": 5, "scale": 2, "format_mask": "YYYY/MM/DD"})
        check(f"{rule} has a description", len(p.get("description") or "") > 25)
        check(f"{rule} names the column", "Some Field" in p["description"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except AssertionError:
            pass
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("all checks passed")
