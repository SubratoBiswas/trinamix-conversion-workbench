"""Three defects found by measuring the 30-Jul 22:18 output, not by reading code.

The generated bundle was right about almost everything — Batch ID empty on all six
sheets, Supplier Name New empty, Procurement BU and Liability Distribution empty,
Business Relationship SPEND_AUTHORIZED, Parent Supplier resolving to names, Delivery
Method populated. Three columns were still wrong, and each was wrong in a way no
unit test was looking for.

  1. ALTERNATE NAME WAS BLANKED ON EVERY ROW — 0 of 3,872. The correction carried a
     CASE_WHEN that returned "" on its only branch AND on its default, so it could
     never do anything but blank. It had been harmless only because the overlay was
     not reaching that field; the moment the overlay started running, it destroyed
     all 1,286 legitimate alternate names. The real rule is BLANK_IF_EQUALS, which
     compares the two finished OUTPUT columns — a row-local CASE_WHEN cannot do
     this at all, because the per-row context holds SOURCE columns while "Supplier
     Name" is a TARGET field.

  2. INACTIVE DATE CARRIED "No" ON THE CONTACTS SHEET — 3,813 rows. A Yes/No value
     in a DATE column, which Oracle rejects. The Supplier, Address and Site sheets
     were correctly empty; the correction was filed against Supplier alone, so
     POZ_SUP_CONTACTS never saw it.

  3. THE SITE KEY DISAGREED WITH ITSELF ACROSS SHEETS — POZ_SUPPLIER_SITES_INT said
     "Hyderabad" while POZ_SITE_ASSIGNMENTS_INT said "BU Hyderabad" for the same
     site. 3,878 assignment rows that could not have matched a single site.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.strategy_overlay import (apply_frame_rules, blank_fields,  # noqa: E402
                                           directive_for)


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# ── 1. Alternate Name ────────────────────────────────────────────────────────
def test_alternate_name_blanks_only_the_duplicates():
    import pandas as pd
    df = pd.DataFrame({
        "Supplier Name":  ["Acme Inc", "Beta Ltd", "Gamma SA", "Delta"],
        "Alternate Name": ["Acme Inc", "Beta Trading", " ACME INC ", ""],
    })
    out = apply_frame_rules(df.copy(), "Supplier")
    got = list(out["Alternate Name"])
    check("the exact duplicate is blanked", got[0] == "", f"got {got[0]!r}")
    check("a genuine alternate survives", got[1] == "Beta Trading", f"got {got[1]!r}")
    check("a name that matches nothing survives", got[2].strip() == "ACME INC",
          f"got {got[2]!r}")


def test_no_rule_blanks_alternate_name_unconditionally():
    """The shape of the bug: a CASE_WHEN whose every path returns "". Any rule that
    cannot produce a non-empty value is a blanking rule wearing a disguise."""
    d = directive_for("Supplier", "Alternate Name")
    check("a rule exists", d and "rule" in d, f"got {d}")
    check("it is the frame comparison, not a row-local CASE_WHEN",
          d["rule"]["rule_type"] == "BLANK_IF_EQUALS", f"got {d['rule']['rule_type']}")

    # And no OTHER overlay rule anywhere can only-ever-blank.
    from app.services import strategy_overlay as so
    so._load()
    offenders = []
    for obj, fields in (so._cache or {}).items():
        for fld, dd in fields.items():
            r = (dd.get("rule") or {})
            if (r.get("rule_type") or "").upper() != "CASE_WHEN":
                continue
            cfg = r.get("config") or {}
            outs = [b.get("then") for b in (cfg.get("branches") or [])]
            outs.append(cfg.get("default"))
            if outs and not any(str(o or "").strip() for o in outs):
                offenders.append(f"{obj}.{fld}")
    check("no CASE_WHEN can only ever return blank", not offenders,
          f"offenders: {offenders}")


# ── 2. Inactive Date ─────────────────────────────────────────────────────────
def test_inactive_date_is_blank_on_every_supplier_sheet():
    """It is a DATE column; "No" is not a date on any sheet."""
    for obj in ("Supplier", "Supplier Address", "Supplier Site",
                "Supplier Site Assignment", "Supplier Contacts", "Supplier Banks"):
        check(f"blank on {obj}",
              "inactive date" in {str(x).lower() for x in blank_fields(obj)})


def test_the_contacts_sheet_was_the_one_that_leaked():
    """Named explicitly: this is the sheet that carried "No" on 3,813 rows."""
    check("POZ_SUP_CONTACTS is covered",
          "inactive date" in {str(x).lower() for x in blank_fields("Supplier Contacts")})


# ── 3. The site key ──────────────────────────────────────────────────────────
def test_the_site_key_rule_reaches_every_sheet_that_carries_it():
    """A key that differs between the sites sheet and the assignments sheet is a
    referential break, not a cosmetic one: the assignment cannot find its site."""
    # The two sheets that must AGREE: an assignment row names the site it assigns,
    # so POZ_SITE_ASSIGNMENTS_INT and POZ_SUPPLIER_SITES_INT have to spell it the
    # same way. In the 30-Jul output they said "Hyderabad" and "BU Hyderabad".
    for obj in ("Supplier Site", "Supplier Site Assignment"):
        d = directive_for(obj, "Supplier Site")
        check(f"{obj} has the site-key rule", d and "rule" in d, f"got {d}")
        check(f"{obj} uses the same rule type",
              d["rule"]["rule_type"] == "CITY_COUNTRY_KEY",
              f"got {d['rule']['rule_type']}")


def test_the_banks_sheet_is_deliberately_left_alone():
    """IBY_TEMP_EXT_PAYEES also has a Supplier Site column, and it holds numeric
    references (3600461, 4848956) rather than names. Whether those should become
    the country-city key or stay as ids is a business question about how the bank
    rows reference their site — so the rule is scoped to Supplier Site* and stops
    short of Banks rather than guessing. Asserted, so the omission is a decision on
    the record instead of something that looks like an oversight later.
    """
    check("banks is not covered by the site-key rule",
          directive_for("Supplier Banks", "Supplier Site") is None)


def test_all_three_corrections_are_marked_all_sheets():
    """Seam over the data, since the fix for two of these IS the flag."""
    import json
    from pathlib import Path
    doc = json.loads((Path(__file__).resolve().parent.parent / "app" / "data"
                      / "supplier_corrections_30jul.json").read_text(encoding="utf-8"))
    by_field = {str(r.get("target_field", "")).lower(): r for r in doc["rules"]}
    for f in ("inactive date", "supplier site", "batch id"):
        check(f"{f} applies to all sheets",
              by_field[f].get("applies_to_all_sheets") is True,
              f"got {by_field[f].get('applies_to_all_sheets')}")
    check("Alternate Name no longer carries a row-local rule",
          not by_field["alternate name"].get("rule_type"),
          f"got {by_field['alternate name'].get('rule_type')}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall 30-Jul output regression checks passed")
