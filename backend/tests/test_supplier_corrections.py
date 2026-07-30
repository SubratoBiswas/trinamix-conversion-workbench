"""The 30-Jul Supplier corrections, and the precedence rule that came with them.

Two things arrived together and they interact.

THE CORRECTIONS. Twelve analyst instructions: six fields that must ship blank, one
constant, and five rules. One of them closes a question I raised on 28-Jul and could
not answer myself — whether Tax Organization Type takes the Oracle CODE
(``CORPORATION``) or the display value. The answer is the display value, "C caps, rest
small", so the strategy now says ``Corporation`` / ``Individual`` and both are protected
from cleansing. That protection is not theoretical: legal-suffix standardisation once
rewrote ``CORPORATION`` to ``Corp`` on 1,392 rows of a required field, and a case family
would do the same to a mixed-case value.

THE PRECEDENCE RULE. "If user modifies anything from the tool UI and then approves it,
it should get highest precedence." Before this, ``apply_learned_to_conversion(force=True)``
overrode every approved mapping regardless of who approved it — so an analyst could
correct a field, approve it, generate, and find the library had quietly put its own value
back. The screen and the file disagreed and the screen looked right, which is the most
expensive shape of bug this tool can have.

Pure: stdlib only. The engine rules are exercised directly; the wiring is asserted by
AST so it cannot regress silently.
"""
import ast
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.cleansing_rules import _norm_protect  # noqa: E402
from app.services.generate_dq import protected_values  # noqa: E402
from app.transformations.engine import apply_rule  # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent
_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def doc():
    return json.loads((_BACKEND / "app" / "data"
                       / "supplier_corrections_30jul.json").read_text(encoding="utf-8"))


def rule_for(field):
    for r in doc()["rules"]:
        if r["target_field"] == field:
            return r
    raise AssertionError(f"no correction for {field!r}")


def run(r, value="", row=None, ctx=None):
    return apply_rule(r["rule_type"], r["rule_config"], value, row or {}, ctx or {})


# ── Tax Organization Type: the 28-Jul question, answered ────────────────────
def test_tax_organization_type_uses_the_oracle_code():
    """Reversed within the day: first "C caps, rest small", then "please make it
    caps". The instance takes the CODE. Pinned here because it changed once — if a
    load ever rejects on this value, this test is the record of which way it went."""
    r = rule_for("Tax Organization Type")
    check("default is CORPORATION",
          r["rule_config"]["default"] == "CORPORATION",
          f"got {r['rule_config']['default']!r}")
    check("and the individual branch matches",
          r["rule_config"]["branches"][0]["then"] == "INDIVIDUAL")


def test_an_individual_supplier_is_not_loaded_as_a_corporation():
    """The defect the CASE_WHEN exists to prevent: a flat constant made every
    individual a corporation."""
    r = rule_for("Tax Organization Type")
    check("individual", run(r, "", {"Is Individual": "T"}) == "INDIVIDUAL")
    check("everything else", run(r, "", {"Is Individual": ""}) == "CORPORATION")


def test_the_new_case_is_protected_from_cleansing():
    """A mixed-case decision is exactly what a case-normalising family rewrites."""
    pv = protected_values()
    for v in ("CORPORATION", "INDIVIDUAL", "SPEND_AUTHORIZED"):
        check(f"{v} protected", _norm_protect(v) in pv)


def test_the_strategy_and_the_corrections_agree_on_the_value():
    """Two files carry this rule. They disagreeing is how one gets fixed and the other
    quietly keeps shipping the old value."""
    a = (_BACKEND / "app" / "data"
         / "supplier_strategy_defaults.json").read_text(encoding="utf-8")
    b = (_BACKEND / "app" / "data"
         / "supplier_corrections_30jul.json").read_text(encoding="utf-8")
    for src, nm in ((a, "strategy"), (b, "corrections")):
        check(f"{nm} uses the code", '"CORPORATION"' in src)
        check(f"{nm} has no display-case leftover", '"Corporation"' not in src)


# ── Delivery Method ─────────────────────────────────────────────────────────
def test_delivery_method_prefers_email():
    r = rule_for("Delivery Method")
    check("email column", run(r, "", {"Remittance Email": "a@b.com"}) == "EMAIL")
    check("fax column", run(r, "", {"Remittance Fax": "555"}) == "FAX")
    check("both -> EMAIL, which is what 'preference to EMAIL' means",
          run(r, "", {"Remittance Email": "a@b.com", "Remittance Fax": "555"}) == "EMAIL")
    check("neither -> blank, not a guess", run(r, "", {}) == "")


def test_delivery_method_is_marked_for_every_sheet():
    check("flagged", rule_for("Delivery Method").get("applies_to_all_sheets") is True)


# ── Supplier Site ───────────────────────────────────────────────────────────
def test_supplier_site_is_country_code_dash_city():
    r = rule_for("Supplier Site")
    check("US-Chicago", run(r, "", {"Country Code": "US", "City": "Chicago"})
          == "US-Chicago")


def test_supplier_site_never_ships_a_bare_separator():
    """The guard added after 8,561 rows once shipped a literal '-' into a required
    unique key because neither input column existed."""
    r = rule_for("Supplier Site")
    check("falls back", run(r, "KEEP", {"unrelated": "x"}) == "KEEP")


# ── Parent Supplier: the self-join ──────────────────────────────────────────
def test_parent_supplier_resolves_a_name_from_another_row():
    r = rule_for("Parent Supplier")
    ctx = {"self_index": {"Internal Id->Name": {"1001": "Acme Holdings"}}}
    check("name, not id",
          run(r, "", {"Parent Vendor Id": "1001"}, ctx) == "Acme Holdings")


def test_a_parent_missing_from_the_extract_ships_blank_not_the_id():
    """An id where a NAME belongs looks populated and is wrong — the same failure as
    Remit-to Supplier holding 'Y'."""
    r = rule_for("Parent Supplier")
    ctx = {"self_index": {"Internal Id->Name": {"1001": "Acme"}}}
    check("blank", run(r, "", {"Parent Vendor Id": "9999"}, ctx) == "")
    check("no parent at all -> blank", run(r, "", {"Parent Vendor Id": ""}, ctx) == "")


def test_no_index_yields_blank_rather_than_the_raw_id():
    """Preview paths do not build the index. Returning the id there would put an id
    in front of the analyst as though it were the answer."""
    r = rule_for("Parent Supplier")
    check("blank", run(r, "", {"Parent Vendor Id": "1001"}, {}) == "")


# ── The blanks ──────────────────────────────────────────────────────────────
def test_every_field_the_analyst_said_to_blank_is_recorded():
    blanks = {r["target_field"] for r in doc()["rules"] if r["action"] == "blank"}
    check("all six", blanks == {"Batch ID", "Supplier Name New", "Inactive Date",
                                "Tax Reporting Name", "Procurement BU",
                                "Liability Distribution"}, f"got {sorted(blanks)}")


def test_the_deliberate_blanks_say_they_are_deliberate():
    """Procurement BU and Liability Distribution are blank on instruction, not by
    oversight, and the file has to carry that or the next reader 'fixes' them."""
    for f in ("Procurement BU", "Liability Distribution"):
        check(f"{f} explains itself", "on instruction" in (rule_for(f).get("note") or ""))


def test_business_relationship_constant():
    r = rule_for("Business Relationship")
    check("SPEND_AUTHORIZED", r["value"] == "SPEND_AUTHORIZED")


def test_the_unresolved_items_are_carried_as_questions():
    qs = " ".join(doc()["_open_questions"]).lower()
    check("Supplier Name not populating is recorded", "supplier name" in qs)
    check("the parent columns need confirming", "parent vendor id" in qs)


# ── Precedence: a human decision is never overwritten ───────────────────────
def _eligible_src():
    src = (_BACKEND / "app" / "services" / "learning_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "apply_learned_to_conversion")
    inner = next(n for n in ast.walk(fn)
                 if isinstance(n, ast.FunctionDef) and n.name == "_eligible")
    return ast.unparse(inner)


def test_force_no_longer_overrides_every_approved_mapping():
    body = _eligible_src()
    check("the approver is checked", "approved_by" in body, f"got:\n{body}")
    check("and only the engine's own approvals are eligible",
          "learning-engine" in body, f"got:\n{body}")


def test_a_suggested_mapping_is_still_eligible():
    body = _eligible_src()
    check("suggested still applies", "'suggested'" in body or '"suggested"' in body)


def test_the_propagation_helper_exists_and_protects_human_decisions():
    """'Any rule or correction I make ... available for all current and future
    conversions' — future was already covered; this is the current ones."""
    src = (_BACKEND / "app" / "services" / "learning_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "propagate_learning_to_open_conversions"), None)
    check("helper exists", fn is not None)
    body = ast.unparse(fn)
    check("skips overridden", "overridden" in body)
    check("skips anything a person approved", "learning-engine" in body)
    check("stales the affected outputs rather than regenerating them",
          "stale" in body)


def test_approve_calls_the_propagation():
    src = (_BACKEND / "app" / "routers" / "mapping.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for name in ("approve_mapping", "update_mapping"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == name)
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        check(f"{name} propagates", "propagate_learning_to_open_conversions" in calls,
              f"got {sorted(calls)}")


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
