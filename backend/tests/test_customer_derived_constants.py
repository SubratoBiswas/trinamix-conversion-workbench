"""The Consolidated Customer mapping (06-Aug) derived constants must reach the file.

The mapping document lists ~20 "Oracle Derived" defaults for Customer. Most were
already in customer_mapping_03aug.json (Payment Terms, Role Type, the NETSUITE source
systems, Insert Update Indicator scoped to profiles, Party Type/Number rules). These
eleven were missing, so the fields shipped blank; they are added as dated constants
so the write-time overlay fills them AND the seed records them in the store (Learning
Centre) for every existing and future NextPower Customer conversion.

Pure: reads the shipped JSON through the real strategy overlay. No database.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.services.strategy_overlay as so                     # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _reset():
    so._cache = so._blank_cache = so._wild_cache = so._wild_blank_cache = None


NEW_CONSTANTS = {
    "Party Usage Code": "CUSTOMER",
    "Account Type": "External",
    "Customer Profile Class": "DEFAULT",
    "Collector Name": "Default Collector",
    "Include in Credit Check": "Y",
    "Currency": "USD",
    "Credit Hold": "N",
    "Organization ID": "-1",
    "Responsibility Type": "BILL_TO",
    "Subject Party Type": "PERSON",
    "Object Party Type": "ORGANIZATION",
}


def test_every_new_derived_constant_is_applied_by_the_overlay():
    _reset()
    for field, value in NEW_CONSTANTS.items():
        d = so.directive_for("Customer", field)
        check(f"{field} = {value}", d is not None and d.get("constant") == value,
              f"got {d}")


def test_the_already_present_constants_are_untouched():
    """Regression guard: adding rows must not disturb the ones that were already
    correct (and validated in the live file)."""
    _reset()
    for field, value in (("Payment Terms", "IMMEDIATE"), ("Role Type", "CONTACT"),
                         ("Relationship Code", "CONTACT_OF"),
                         ("Party Original System", "NETSUITE")):
        d = so.directive_for("Customer", field)
        check(f"{field} still {value}", d is not None and d.get("constant") == value,
              f"got {d}")


def test_insert_update_indicator_is_durably_i_on_profiles_blank_elsewhere():
    """Issue 3, made durable for every conversion (same client+object+sheet): TWO
    per-sheet store rules — I on RA_CUSTOMER_PROFILES_INT_ALL, and a keep-blank on
    every OTHER sheet — so no conversion can ship I outside profiles, and a stale I
    left on another sheet is overridden. Both are store-only (the overlay is
    object-keyed and cannot express per-sheet)."""
    import json
    doc = json.loads((_BACKEND / "app" / "data" / "customer_mapping_03aug.json")
                     .read_text(encoding="utf-8"))
    iui = [r for r in doc["rules"] if r.get("target_field") == "Insert Update Indicator"]
    check("two Insert Update Indicator rules", len(iui) == 2, f"got {len(iui)}")
    const = next((r for r in iui if r.get("action") == "constant"), None)
    blank = next((r for r in iui if r.get("action") == "blank"), None)
    check("I is scoped to profiles only",
          const and const.get("value") == "I"
          and const.get("sheets") == ["RA_CUSTOMER_PROFILES_INT_ALL"], f"got {const}")
    check("blank is scoped to every sheet EXCEPT profiles",
          blank and blank.get("exclude_sheets") == ["RA_CUSTOMER_PROFILES_INT_ALL"],
          f"got {blank}")


def test_the_two_iui_rules_resolve_per_sheet():
    """The behaviour the two rules must produce, through the real resolver: profiles
    gets I, any other sheet gets blank — and a same-dated stale I loses the tie."""
    from datetime import datetime
    from app.services.mapping_store import resolve, SUPPRESS, DEFAULT_VALUE

    class Row:
        def __init__(s, **k):
            for a in ("id", "kind", "target_field", "target_object", "original_value",
                      "resolved_value", "rule_type", "rule_config", "client_id",
                      "source_erp", "effective_date", "captured_at", "captured_from",
                      "captured_by", "sheets", "exclude_sheets", "is_deleted"):
                setattr(s, a, k.get(a))
            s.sheets = s.sheets or []
            s.exclude_sheets = s.exclude_sheets or []
            s.is_deleted = bool(s.is_deleted)

    eff = datetime(2026, 8, 3)
    i_on = Row(kind="example_default", target_field="Insert Update Indicator",
               resolved_value="I", rule_config={"default_value": "I"},
               effective_date=eff, sheets=["RA_CUSTOMER_PROFILES_INT_ALL"])
    blank = Row(kind="suppress_field", target_field="Insert Update Indicator",
                resolved_value="", rule_type="suppress", effective_date=eff,
                exclude_sheets=["RA_CUSTOMER_PROFILES_INT_ALL"])
    stale_i = Row(kind="example_default", target_field="Insert Update Indicator",
                  resolved_value="I", rule_config={"default_value": "I"},
                  effective_date=eff)          # unscoped, same date — the leak
    prof = resolve([i_on, blank, stale_i], target_field="Insert Update Indicator",
                   sheet="RA_CUSTOMER_PROFILES_INT_ALL")
    check("profiles gets I", (prof.decision, prof.value) == (DEFAULT_VALUE, "I"),
          f"got {prof.decision}={prof.value!r}")
    for sh in ("HZ_IMP_PARTIES_T", "HZ_IMP_ACCOUNTS_T"):
        w = resolve([i_on, blank, stale_i], target_field="Insert Update Indicator",
                    sheet=sh)
        check(f"{sh} is blank (stale I loses the tie)", w.decision == SUPPRESS,
              f"got {w.decision}={w.value!r}")


def test_the_blank_set_did_not_grow():
    """Only Batch Identifier is blanked for Customer; a stray constant must not have
    been miswritten as a blank."""
    _reset()
    check("Customer blanks stay {batch identifier}",
          set(so.blank_fields("Customer")) == {"batch identifier"},
          f"got {sorted(so.blank_fields('Customer'))}")


def test_the_seed_records_constants_as_default_values():
    """The store side: the seed maps action 'constant' to a DEFAULT_VALUE decision so
    the new rows show in the Learning Centre and propagate."""
    src = (_BACKEND / "app" / "services" / "catalog_seed_service.py").read_text(encoding="utf-8")
    body = src.split("async def seed_customer_mapping_03aug(")[1].split("\nasync def ")[0]
    check("constant -> DEFAULT_VALUE", '"constant": mapping_store.DEFAULT_VALUE' in body)
    check("it records each rule", "record_decision(" in body)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nCustomer derived constants reach the overlay and the store")
