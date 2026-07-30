"""A boolean column is TRUE or FALSE, not present or absent.

Analyst, 30-Jul, looking at the generated Supplier file: "why tax organization type
is INDIVIDUAL for many?"

Because the branch read {"if_column": "Is Individual", "op": "notblank"}. In the
NetSuite extract that column carries "No" on 6,985 of 7,495 rows, "Yes" on 437 and
is EMPTY on 73. "No" is not blank — so 7,422 suppliers were written as INDIVIDUAL,
including "3D Hubs Manufacturing LLC" and "A.B Boyd Co", and the only rows that
came out CORPORATION were the 73 where the column said nothing at all. Exactly
backwards, on 99% of the file.

Measured against the real extract, before and after:
    BEFORE   CORPORATION    73   INDIVIDUAL  7,422
    AFTER    CORPORATION 7,039   INDIVIDUAL    456

Worth recording why the rule was written that way: the 13-Jul strategy quotes the
instruction as "there is one column in the extract, IS INDIVIDUAL. If there is a
value in that, only in that case this value will change". `notblank` is a faithful
reading of those words. The data is what shows the words meant "if it SAYS yes" —
which is why this file asserts against the extract's real vocabulary rather than
against the sentence.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.strategy_overlay import directive_for       # noqa: E402
from app.transformations.engine import _COMPARISON_OPS, apply_pipeline  # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def tax(row):
    d = directive_for("Supplier", "Tax Organization Type")
    return apply_pipeline([d["rule"]], "", row=row)


# ── the operators ────────────────────────────────────────────────────────────
def test_istrue_reads_the_spellings_these_extracts_carry():
    t = _COMPARISON_OPS["istrue"]
    for v in ("Yes", "yes", "YES", " Y ", "true", "T", "1"):
        check(f"{v!r} is true", t(v, None) is True)
    for v in ("No", "no", " n ", "false", "F", "0", "", None, "maybe"):
        check(f"{v!r} is not true", t(v, None) is False)


def test_isfalse_is_not_merely_the_negation_of_istrue():
    """Blank and junk are neither — a three-state column must stay three-state, or
    "unknown" silently becomes "false" and the default branch never runs."""
    f = _COMPARISON_OPS["isfalse"]
    for v in ("No", "n", "FALSE", "0", " f "):
        check(f"{v!r} is false", f(v, None) is True)
    for v in ("", None, "Yes", "maybe"):
        check(f"{v!r} is not false", f(v, None) is False)


def test_the_two_operators_share_MAP_BOOLEANs_vocabulary():
    """Two places deciding what "Y" means is two places to disagree."""
    from app.transformations import engine
    src = open(engine.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    check("MAP_BOOLEAN uses the shared set", "sorted(_TRUEISH)" in src)
    check("and the shared false set", "sorted(_FALSEISH)" in src)


# ── the rule ─────────────────────────────────────────────────────────────────
def test_a_company_is_a_corporation():
    """The reported symptom, in the extract's own vocabulary."""
    check("No -> CORPORATION",
          tax({"Is Individual": "No", "Entity Type": "Business"}) == "CORPORATION")
    check("whitespace and case do not matter",
          tax({"Is Individual": " no ", "Entity Type": "Coops"}) == "CORPORATION")


def test_an_individual_is_still_an_individual():
    check("Yes -> INDIVIDUAL",
          tax({"Is Individual": "Yes", "Entity Type": ""}) == "INDIVIDUAL")


def test_entity_type_is_a_second_witness():
    """Is Individual is empty on 73 rows; Entity Type names 19 individuals and
    1,419 businesses, so it answers where the first column is silent."""
    check("Entity Type Individual -> INDIVIDUAL",
          tax({"Is Individual": "", "Entity Type": "Individual"}) == "INDIVIDUAL")
    check("Entity Type Business -> CORPORATION",
          tax({"Is Individual": "", "Entity Type": "Business"}) == "CORPORATION")


def test_no_signal_at_all_is_a_corporation():
    """The default a supplier with nothing said about it should get."""
    check("blank/blank -> CORPORATION",
          tax({"Is Individual": "", "Entity Type": ""}) == "CORPORATION")


def test_the_rule_no_longer_tests_a_boolean_with_notblank():
    """Seam: the operator IS the fix, so the operator is what gets asserted."""
    d = directive_for("Supplier", "Tax Organization Type")
    ops = {b.get("op") for b in d["rule"]["config"]["branches"]}
    check("notblank is gone", "notblank" not in ops, f"got {sorted(ops)}")
    check("istrue is used", "istrue" in ops, f"got {sorted(ops)}")
    check("default is CORPORATION", d["rule"]["config"]["default"] == "CORPORATION")


def test_no_other_rule_tests_a_boolean_looking_column_with_notblank():
    """The same trap is set wherever a yes/no column is read for presence. This
    fails on a new one rather than waiting for another 99%-wrong file."""
    import json
    from pathlib import Path
    data = Path(__file__).resolve().parent.parent / "app" / "data"
    _BOOLISH = ("is ", "has ", "default ", "reportable", "individual", "hold",
                "inactive", "eligible")
    offenders = []
    for f in data.glob("*.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for br in _branches(doc):
            col = str(br.get("if_column") or "").lower()
            if br.get("op") == "notblank" and any(k in col for k in _BOOLISH):
                offenders.append(f"{f.name}: {br}")
    check("no boolean column is read with notblank", not offenders,
          "; ".join(offenders[:4]))


def _branches(node):
    if isinstance(node, dict):
        if "branches" in node and isinstance(node["branches"], list):
            for b in node["branches"]:
                if isinstance(b, dict):
                    yield b
        for v in node.values():
            yield from _branches(v)
    elif isinstance(node, list):
        for v in node:
            yield from _branches(v)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall boolean-condition checks passed")
