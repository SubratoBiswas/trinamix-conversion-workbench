"""Delivery Method: the fax branch is FAX, and this is the statement that wins.

THE QUESTION, AND WHY IT WAS LEFT OPEN
--------------------------------------
The 04-Aug instruction assigned EMAIL in BOTH branches — "if remittance fax is not
blank but remittance-email is blank then the delivery method is EMAIL". It was
written that way twice, so it was implemented exactly as written and the doubt
was filed as an ``_open_question`` rather than quietly corrected. That was the
right call at the time: guessing at what somebody meant is how a rule ends up
saying something nobody decided.

WHAT SETTLED IT — ORACLE'S OWN TEMPLATE, NOT AN OPINION
-------------------------------------------------------
Read out of the bundled ``1_SupplierImport_POZ_SUPPLIERS_INT.xlsm`` header
comments, which are Oracle's published database truth for each column:

    Delivery Method    REMIT_ADVICE_DELIVERY_METHOD, VARCHAR2(30)
                       "Valid values are EMAIL, EMAILPDF, FAX or PRINTED."
    Remittance E-mail  REMIT_ADVICE_EMAIL, VARCHAR2(255)
                       "Value must be provided when Delivery Method is EMAIL
                        or EMAILPDF."

The fax branch fires precisely when Remittance E-mail is BLANK. Setting EMAIL
there produces, on every one of those rows, the combination Oracle documents as
not allowed: a delivery method that requires an address, with no address. FAX is
a value Oracle accepts, so the 28-Jul rule was never proposing anything exotic.

Confirmed by Subrato, 05-Aug. A NEW DATED FILE, not an edit: the 04-Aug file is
left byte-for-byte as it was, because it is the record of what was decided that
day and precedence in this store is expressed by date.

These tests assert three separate things, because in this codebase a rule can be
right and still not arrive:
  * the file says FAX,
  * ``strategy_overlay`` READS the file — an unregistered dated file is inert,
    which is CODEBASE_GUIDE §7.1 and has cost four features already,
  * and the 05-Aug statement is the one that wins over 04-Aug.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import strategy_overlay                        # noqa: E402
from app.transformations.engine import apply_rule                # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent
_DATA = _BACKEND / "app" / "data"
_FILE = "supplier_corrections_05aug.json"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _doc(name=_FILE) -> dict:
    return json.loads((_DATA / name).read_text(encoding="utf-8"))


def _rule(doc, field="Delivery Method") -> dict:
    for r in doc.get("rules", []):
        if r.get("target_field") == field:
            return r
    raise AssertionError(f"no rule for {field!r} in that document")


def _run(rule, row):
    return apply_rule(rule["rule_type"], rule["rule_config"], "", row, {})


# ---------------------------------------------------------------------------
# What the rule does
# ---------------------------------------------------------------------------
def test_a_fax_only_row_ships_fax():
    """The whole point of the correction."""
    r = _rule(_doc())
    for column in ("Remittance Fax", "Remittance fax", "remittance_fax"):
        check(f"{column} -> FAX", _run(r, {column: "555-0100"}) == "FAX",
              f"got {_run(r, {column: '555-0100'})!r}")


def test_an_email_row_still_ships_email():
    """Unchanged from 04-Aug and from 28-Jul. A correction that quietly moved
    this too would be a second change riding on the first."""
    r = _rule(_doc())
    for column in ("Remittance E-Mail", "Remittance Email", "Remittance E-mail",
                   "remittance_email"):
        check(f"{column} -> EMAIL", _run(r, {column: "ap@acme.com"}) == "EMAIL")


def test_a_row_with_both_still_prefers_email():
    """'Preference to EMAIL', 28-Jul. The e-mail branches are tested first, and
    reordering them would change this answer without changing any value."""
    r = _rule(_doc())
    check("both -> EMAIL",
          _run(r, {"Remittance E-mail": "ap@acme.com", "Remittance Fax": "555"}) == "EMAIL")


def test_a_row_with_neither_ships_blank_not_a_guess():
    """PRINTED is the other value Oracle accepts and nobody chose it. A default
    that invents a delivery method is a decision the analyst did not make."""
    r = _rule(_doc())
    check("neither -> blank", _run(r, {}) == "")
    check("and the default is spelled blank", r["rule_config"].get("default") == "")


def test_every_value_it_can_emit_is_one_oracle_accepts():
    """EMAIL, EMAILPDF, FAX, PRINTED — from the template's own header comment.
    A typo here is not caught anywhere downstream; the load simply rejects."""
    valid = {"EMAIL", "EMAILPDF", "FAX", "PRINTED", ""}
    emitted = {b["then"] for b in _rule(_doc())["rule_config"]["branches"]}
    emitted.add(_rule(_doc())["rule_config"].get("default", ""))
    check("no invented codes", emitted <= valid, f"got {sorted(emitted - valid)}")


def test_it_still_reaches_every_sheet_that_carries_the_column():
    """Supplier, Supplier Address and Supplier Site all have Delivery Method.
    Losing the flag would fix the header sheet and leave the other two on 04-Aug."""
    check("applies_to_all_sheets", _rule(_doc()).get("applies_to_all_sheets") is True)


# ---------------------------------------------------------------------------
# Does anything READ it?
# ---------------------------------------------------------------------------
def test_the_new_dated_file_is_registered_with_the_overlay():
    """§7.1. A dated file nobody loads is a correction that never happened — the
    exact shape that cost customer_sheet_scope, blank_sheets and SELF_LOOKUP."""
    check("listed in _EXTRA_FILES", _FILE in strategy_overlay._EXTRA_FILES,
          f"got {strategy_overlay._EXTRA_FILES}")
    check("and the file is on disk", (_DATA / _FILE).exists())


def test_the_overlay_resolves_delivery_method_to_the_05aug_rule():
    """The end that matters: ask the overlay what it would do to a fax-only row
    for a Supplier, and get FAX. Reads through the same accessor generation uses,
    so a file that loads but is filed under the wrong object still fails here."""
    strategy_overlay._cache = None
    strategy_overlay._blank_cache = None
    strategy_overlay._wild_cache = None
    strategy_overlay._wild_blank_cache = None

    directive = strategy_overlay.directive_for("Supplier", "Delivery Method")
    check("the overlay has a directive", directive is not None)
    rule = directive.get("rule") or {}
    check("it is a CASE_WHEN", rule.get("rule_type") == "CASE_WHEN", f"got {directive}")
    branches = (rule.get("config") or {}).get("branches") or []
    check("with branches", branches, f"got {rule}")
    fax = [b for b in branches if "fax" in str(b.get("if_column", "")).lower()]
    check("it has fax branches", fax)
    check("and every one of them says FAX",
          all(b.get("then") == "FAX" for b in fax),
          f"got {[b.get('then') for b in fax]}")
    # The date the overlay resolved to, not just the values it happened to carry.
    # Identical values from an older file would satisfy everything above.
    as_of = directive.get("as_of")
    check("and it resolved to the 05-Aug statement",
          getattr(as_of, "date", lambda: None)() is not None
          and str(as_of)[:10] == "2026-08-05", f"as_of={as_of!r}")


def test_the_05aug_statement_is_the_newest_one_about_this_field():
    """One dated store: newest wins. If 04-Aug ever carried a later date than
    05-Aug, the overlay would answer EMAIL and every test above would still pass
    on its own file."""
    new = _doc()
    old = _doc("supplier_corrections_04aug.json")
    check("05-Aug is dated", new["_effective_date"] == "2026-08-05",
          f"got {new['_effective_date']!r}")
    check("and is later than the file it supersedes",
          new["_effective_date"] > old["_effective_date"],
          f"{new['_effective_date']} vs {old['_effective_date']}")
    check("and says which file it supersedes",
          "supplier_corrections_04aug.json" in new.get("_supersedes", ""))


def test_the_04aug_file_was_not_edited_to_make_this_true():
    """Precedence is expressed by DATE, never by rewriting what was decided. The
    04-Aug document must still say EMAIL in both branches and still carry the
    question, or the record of how this was resolved is gone."""
    old = _rule(_doc("supplier_corrections_04aug.json"))
    fax = [b for b in old["rule_config"]["branches"]
           if "fax" in str(b.get("if_column", "")).lower()]
    check("04-Aug still has fax branches", fax)
    check("and they still read EMAIL, as written that day",
          all(b.get("then") == "EMAIL" for b in fax),
          f"got {[b.get('then') for b in fax]} — the 04-Aug file was edited")
    check("and it still carries its open question",
          "_open_question" in _doc("supplier_corrections_04aug.json"))


# ---------------------------------------------------------------------------
# The two customer questions closed on the same evidence
# ---------------------------------------------------------------------------
def test_the_resolved_customer_questions_carry_their_evidence():
    """Moved out of _open_questions rather than deleted. A question that was
    answered and a question nobody asked look identical once the text is gone,
    and the second one gets asked again."""
    doc = _doc("customer_mapping_03aug.json")
    resolved = " ".join(doc.get("_resolved_questions", []))
    check("there are resolved questions", doc.get("_resolved_questions"))
    check("Phone Line Type is one of them", "Phone Line Type" in resolved)
    check("Identifying Address is the other", "Identifying Address" in resolved)
    check("each keeps the original wording", resolved.count("ORIGINAL QUESTION:") == 2)
    check("and names Oracle's own column",
          "IDENTIFYING_ADDRESS_FLAG" in resolved and "PHONE_LINE_TYPE" in resolved)
    still_open = " ".join(doc.get("_open_questions", []))
    check("neither is still listed as open",
          "Phone Line Type opens" not in still_open
          and "Identifying Address: the workbook" not in still_open)


def test_closing_a_question_changed_no_rule():
    """The customer rules are the deliverable; the question list is bookkeeping.
    Answering a question must not move a mapping."""
    doc = _doc("customer_mapping_03aug.json")
    check("all 54 rules are still there", len(doc["rules"]) == 54,
          f"got {len(doc['rules'])}")
    check("the effective date is untouched", doc["_effective_date"] == "2026-08-03")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall delivery-method checks passed")
