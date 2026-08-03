""""In all sheets, except..." had a mechanism and no data.

CW_Issues 2 (Tejaswini, 29-Jul):
  row 13  id          -> Party Original System Reference, all sheets EXCEPT HZ_IMP_CLASSIFICS_T
  row 14  entityid    -> Account Number AND Customer Account Source System Reference,
                         all sheets EXCEPT RA_CUST_PAY_METHOD_INT_ALL,
                         RA_CUSTOMER_BANKS_INT_ALL, HZ_IMP_ACCOUNTRELS
  row 15  internalid  -> Account Site Source System Reference, all sheets EXCEPT
                         RA_CUSTOMER_BANKS_INT_ALL, RA_CUST_PAY_METHOD_INT_ALL,
                         RA_CUSTOMER_PROFILES_INT_ALL, HZ_IMP_ACCTCONTACTS_T
  row 26  Receipt Method and Start Date on RA_CUSTOMER_BANKS_INT_ALL -> not applicable

``LearnedMapping.sheets`` / ``exclude_sheets`` and ``sheet_allowed`` had existed for
a while and were correct. NOT ONE seeded learning in the entire catalog used them —
checked, the count was zero. So the feature was shipped and inert, and every one of
these instructions was applied to ALL sheets, the named exclusions included.

That is the failure mode this codebase keeps repeating: a capability lands, passes
its tests against hand-made inputs, and never once meets real data. These tests
therefore assert the DATA and its effect through the real ``sheet_allowed``, not the
mechanism in isolation.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.learning_service import sheet_allowed          # noqa: E402

_DOC = json.loads((Path(__file__).resolve().parent.parent / "app" / "data"
                   / "customer_sheet_scope.json").read_text(encoding="utf-8"))


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


class L:
    def __init__(self, sheets=None, exclude_sheets=None):
        self.sheets = sheets or []
        self.exclude_sheets = exclude_sheets or []


def mapping_for(col, tgt):
    for m in _DOC["mappings"]:
        if m["source_column"] == col and m["target_field"] == tgt:
            return L(m.get("sheets"), m.get("exclude_sheets"))
    raise AssertionError(f"no seeded scope for {col} -> {tgt}")


def suppression_for(tgt):
    for s in _DOC["suppressions"]:
        if s["target_field"] == tgt:
            return L(s.get("sheets"), s.get("exclude_sheets"))
    raise AssertionError(f"no seeded suppression for {tgt}")


def test_row13_id_skips_only_the_classifications_sheet():
    lm = mapping_for("id", "Party Original System Reference")
    check("excluded where named", sheet_allowed(lm, "HZ_IMP_CLASSIFICS_T") is False)
    for s in ("HZ_IMP_PARTIES_T", "HZ_IMP_ACCOUNTS_T", "HZ_IMP_PARTY_SITES_T"):
        check(f"allowed on {s}", sheet_allowed(lm, s) is True)


def test_row14_entityid_feeds_two_targets_with_the_same_three_exclusions():
    excluded = ["RA_CUST_PAY_METHOD_INT_ALL", "RA_CUSTOMER_BANKS_INT_ALL",
                "HZ_IMP_ACCOUNTRELS"]
    targets = ["Account Number", "Customer Account Source System Reference"]
    for tgt in targets:
        lm = mapping_for("entityid", tgt)
        for s in excluded:
            check(f"{tgt} excluded on {s}", sheet_allowed(lm, s) is False)
        check(f"{tgt} allowed on HZ_IMP_ACCOUNTS_T",
              sheet_allowed(lm, "HZ_IMP_ACCOUNTS_T") is True)
    check("entityid really does feed two targets",
          len([m for m in _DOC["mappings"] if m["source_column"] == "entityid"]) == 2)


def test_row15_internalid_skips_four_sheets():
    lm = mapping_for("internalid", "Account Site Source System Reference")
    for s in ("RA_CUSTOMER_BANKS_INT_ALL", "RA_CUST_PAY_METHOD_INT_ALL",
              "RA_CUSTOMER_PROFILES_INT_ALL", "HZ_IMP_ACCTCONTACTS_T"):
        check(f"excluded on {s}", sheet_allowed(lm, s) is False)
    check("allowed on the party sites sheet",
          sheet_allowed(lm, "HZ_IMP_PARTY_SITES_T") is True)


def test_row26_suppressions_are_confined_to_the_banks_sheet():
    """Receipt Method and Start Date exist on other sheets. A name-keyed
    suppression with no scope would blank them everywhere — which is exactly the
    "one approval reaches all 19 sheets" problem, pointed the other way."""
    for tgt in ("Receipt Method", "Start Date"):
        lm = suppression_for(tgt)
        check(f"{tgt} suppressed on the banks sheet",
              sheet_allowed(lm, "RA_CUSTOMER_BANKS_INT_ALL") is True)
        for s in ("HZ_IMP_PARTIES_T", "RA_CUSTOMER_PROFILES_INT_ALL"):
            check(f"{tgt} untouched on {s}", sheet_allowed(lm, s) is False)


def test_an_unknown_sheet_does_not_silently_gain_an_allow_listed_rule():
    """sheet_allowed refuses when it cannot show the sheet is on a stated
    allow-list, and allows when the learning only EXCLUDES. Both matter here: the
    mappings exclude, the suppressions allow-list."""
    check("exclusion-only rule allows an unknown sheet",
          sheet_allowed(mapping_for("id", "Party Original System Reference"), None) is True)
    check("allow-list rule refuses an unknown sheet",
          sheet_allowed(suppression_for("Receipt Method"), None) is False)


def test_the_seeder_and_its_wiring_exist():
    """Seam: data with no seeder is the same inert feature one layer up."""
    root = Path(__file__).resolve().parent.parent
    seed = (root / "app" / "services" / "catalog_seed_service.py").read_text(encoding="utf-8")
    check("seeder exists", "async def seed_customer_sheet_scope(" in seed)
    check("it writes the scope", '"sheets": list(m.get("sheets") or [])' in seed)
    store = (root / "app" / "services" / "mapping_store.py").read_text(encoding="utf-8")
    check("it records through the one store", "record_learning(" in seed)
    check("which honours tombstones", "include_deleted=True" in store)
    check("and updates rather than duplicating",
          "if action in (UPDATE, REFRESH):" in store)
    check("the seeder carries the document's own date", "effective_date=eff" in seed)
    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    check("it runs at startup", "seed_customer_sheet_scope()" in main)
    api = (root / "app" / "routers" / "learned.py").read_text(encoding="utf-8")
    check("and on demand", "reseed-customer-sheet-scope" in api)


def test_every_issue_row_is_accounted_for():
    issues = {m.get("issue") for m in _DOC["mappings"]}
    issues |= {s.get("issue") for s in _DOC["suppressions"]}
    for row in (13, 14, 15, 26):
        check(f"row {row} is represented",
              any(f"row {row}" in (i or "") for i in issues), f"got {sorted(issues)}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall customer sheet-scope checks passed")
