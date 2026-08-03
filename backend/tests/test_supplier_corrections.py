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
    """The index key follows the rule's OWN column names, so the test reads them
    from the correction rather than hard-coding a spelling. The extract says
    "Internal ID"; the analyst wrote "Internal Id"; the config now carries the
    extract's spelling and _build_self_index matches columns case-insensitively so
    neither choice can silently cost the whole lookup."""
    r = rule_for("Parent Supplier")
    cfg = r["rule_config"] if "rule_config" in r else r["config"]
    key = f'{cfg["match_column"]}->{cfg["value_column"]}'
    ctx = {"self_index": {key: {"1001": "Acme Holdings"}}}
    check("name, not id",
          run(r, "", {"Parent Vendor Id": "1001"}, ctx) == "Acme Holdings")


def test_a_parent_missing_from_the_extract_ships_blank_not_the_id():
    """An id where a NAME belongs looks populated and is wrong — the same failure as
    Remit-to Supplier holding 'Y'."""
    r = rule_for("Parent Supplier")
    cfg = r["rule_config"] if "rule_config" in r else r["config"]
    ctx = {"self_index": {f'{cfg["match_column"]}->{cfg["value_column"]}':
                          {"1001": "Acme"}}}
    check("blank", run(r, "", {"Parent Vendor Id": "9999"}, ctx) == "")
    check("no parent at all -> blank", run(r, "", {"Parent Vendor Id": ""}, ctx) == "")


def test_no_index_yields_blank_rather_than_the_raw_id():
    """Preview paths do not build the index. Returning the id there would put an id
    in front of the analyst as though it were the answer."""
    r = rule_for("Parent Supplier")
    check("blank", run(r, "", {"Parent Vendor Id": "1001"}, {}) == "")



def test_something_actually_builds_the_self_index():
    """SELF_LOOKUP had NEVER returned a value in production, and the tests above
    could not see it: they hand-build the index the rule reads. Nothing in the
    codebase built one, so Parent Supplier returned its default on every row of
    every real run — 0 of 3,872 suppliers. This drives the real builder."""
    import pandas as pd
    from app.services.output_service import _build_self_index, _self_lookup_configs

    cfgs = _self_lookup_configs({}, "Supplier")
    check("the overlay offers a SELF_LOOKUP config", len(cfgs) == 1, f"got {cfgs}")

    src = pd.DataFrame({
        # The extract's spelling, which differs from the analyst's by one letter.
        "Internal ID": ["101", "102"],
        "Name": ["Acme Corp", "Beta Ltd"],
        "Parent Vendor Id": ["", "101"],
    })
    idx = _build_self_index(src, cfgs)
    check("an index was built", bool(idx), f"got {idx}")
    r = rule_for("Parent Supplier")
    got = run(r, "", {"Parent Vendor Id": "101"}, {"self_index": idx})
    check("the child resolves to its parent's NAME", got == "Acme Corp", f"got {got!r}")


def test_the_index_matches_column_names_case_insensitively():
    import pandas as pd
    from app.services.output_service import _build_self_index
    src = pd.DataFrame({"internal id": ["7"], "NAME": ["Gamma"]})
    idx = _build_self_index(src, [{"match_column": "Internal ID", "value_column": "Name"}])
    check("resolved despite the casing",
          idx.get("Internal ID->Name", {}).get("7") == "Gamma", f"got {idx}")


def test_the_overlay_declares_the_columns_its_rules_read():
    """The generator prunes the frame to the columns something claims. Overlay
    rules claimed nothing, so Supplier Site shipped empty on 8,561 rows even though
    its CONCAT names two columns that exist in the extract."""
    from app.services.strategy_overlay import referenced_columns
    site = referenced_columns("Supplier Site")
    for c in ("Country Code", "City"):
        check(f"Supplier Site declares {c}", c in site, f"got {sorted(site)}")
    sup = referenced_columns("Supplier")
    for c in ("Parent Vendor Id", "Internal ID", "Name"):
        check(f"Supplier declares {c}", c in sup, f"got {sorted(sup)}")


def test_the_newer_all_sheets_rule_beats_an_older_sheet_specific_one():
    """"Whichever is latest" (analyst, 30-Jul). The 13-Jul strategy carries a
    Supplier Site Delivery Method rule reading "Remittance E-Mail"; the 30-Jul
    correction says apply the EMAIL/FAX rule to ALL sheets. Preferring the more
    precise rule alone let the older one shadow the newer instruction."""
    from app.services.strategy_overlay import directive_for
    d = directive_for("Supplier Site", "Delivery Method")
    check("a rule resolves", d and "rule" in d, f"got {d}")
    cols = {b.get("if_column") for b in d["rule"]["config"]["branches"]}
    check("it is the 30-Jul rule", "Email" in cols and "Fax" in cols, f"got {sorted(cols)}")
    check("and it is dated 30-Jul", str(d.get("as_of", ""))[:10] == "2026-07-30",
          f"got {d.get('as_of')}")



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
    check("the approver is checked",
          "decided_by_a_person" in body or "approved_by" in body, f"got:\n{body}")
    check("and their word stands only while it is the later one",
          "approved_at" in body and "effective_date" in body, f"got:\n{body}")
    check("and a row the engine wrote is refreshed rather than defended",
          "decided_by_a_person" in body, f"got:\n{body}")
    store = (_BACKEND / "app" / "services" / "mapping_store.py").read_text(encoding="utf-8")
    check("the engine's own marker is what tells the two apart",
          'ENGINE = "learning-engine"' in store)


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


def test_applies_to_all_sheets_is_actually_read():
    """It was dead data. The analyst had been writing the flag; nothing read it.

    Two rules said "all sheets" and reached exactly one:
      * Batch ID  — "Blank on ALL sheets. A batch identifier the loader assigns is
        not ours to invent." Filed under Supplier, so Supplier Site / Address /
        Site Assignment / Contact / Bank all kept shipping 900001 — and kept
        SAYING 900001 on screen, which is how the analyst found it.
      * Delivery Method — the CASE_WHEN never reached Supplier Site, which is
        where the column actually lives.
    """
    from app.services.strategy_overlay import blank_fields, directive_for

    doc = json.loads((_BACKEND / "app" / "data"
                      / "supplier_corrections_30jul.json").read_text(encoding="utf-8"))
    batch = [r for r in doc["rules"]
             if str(r.get("target_field", "")).strip().lower() == "batch id"]
    check("the Batch ID correction exists", len(batch) == 1, f"got {len(batch)}")
    check("and it is marked all-sheets", batch[0].get("applies_to_all_sheets") is True,
          "the note says 'Blank on ALL sheets' — the flag has to say so too")

    sheets = ["Supplier", "Supplier Site", "Supplier Address",
              "Supplier Site Assignment", "Supplier Contact", "Supplier Bank"]
    for obj in sheets:
        check(f"Batch ID is blank on {obj}",
              "batch id" in {str(x).lower() for x in blank_fields(obj)})
    check("Delivery Method reaches Supplier Site",
          bool(directive_for("Supplier Site", "Delivery Method")))


def test_all_sheets_matches_by_prefix_not_by_wildcard():
    """A rule filed under Supplier must not blank Customer's Batch ID.

    Customer FBDI has a Batch ID column too, and the same argument would apply to
    it — but the analyst was talking about the supplier bundle, and silently
    extending an instruction past what was said is its own kind of bug.
    """
    from app.services.strategy_overlay import blank_fields, directive_for
    for obj in ("Customer", "Customer Site", "Item", "Employee"):
        check(f"{obj} is untouched by the supplier all-sheets rule",
              "batch id" not in {str(x).lower() for x in blank_fields(obj)})
        check(f"{obj} gets no Delivery Method rule",
              directive_for(obj, "Delivery Method") is None)


def test_a_sheet_specific_rule_still_beats_the_bundle_wide_one():
    """Exact match is looked up first, so a per-sheet override remains possible."""
    from app.services import strategy_overlay as so
    so._load()
    for obj, fields in (so._cache or {}).items():
        for fld in fields:
            check(f"exact rule wins for {obj}.{fld}",
                  so.directive_for(obj, fld) is fields[fld])
        break  # one object's worth is enough to prove the ordering



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
