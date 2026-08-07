"""Primary / Identifying flag is first-per-customer, set per sheet (REC-09 / REC-23).

"Primary Indicator" is one column NAME shared across four Customer sheets, so it
cannot be a per-field transform rule — the wide frame carries a single value for
all of them. The flag is therefore set in the per-sheet reshape, where each sheet
already has its own rows and the customer key: Y on the first BILLING row per
entityid (deterministically MIN internalid), N on every other site row. Shipping
rows and later billing rows are N; contact sheets are left untouched.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import customer_merge as cm            # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _site_frame():
    # Two customers, each with several billing rows and a shipping row.
    # NT-1: billing internalids 300, 100, 200 (MIN=100) + shipping 900
    # NT-2: billing internalids 500, 400 (MIN=400) + shipping 800
    rows = [
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "300",
         "Purpose": "BILL_TO", "Part Site Use Type": "BILL_TO", "Primary Indicator": "",
         "Identifying Address": ""},
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "100",
         "Purpose": "BILL_TO", "Part Site Use Type": "BILL_TO", "Primary Indicator": "",
         "Identifying Address": ""},
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "200",
         "Purpose": "BILL_TO", "Part Site Use Type": "BILL_TO", "Primary Indicator": "",
         "Identifying Address": ""},
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "900",
         "Purpose": "SHIP_TO", "Part Site Use Type": "SHIP_TO", "Primary Indicator": "",
         "Identifying Address": ""},
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-2", cm.INTERNALID_COL: "500",
         "Purpose": "BILL_TO", "Part Site Use Type": "BILL_TO", "Primary Indicator": "",
         "Identifying Address": ""},
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-2", cm.INTERNALID_COL: "400",
         "Purpose": "BILL_TO", "Part Site Use Type": "BILL_TO", "Primary Indicator": "",
         "Identifying Address": ""},
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-2", cm.INTERNALID_COL: "800",
         "Purpose": "SHIP_TO", "Part Site Use Type": "SHIP_TO", "Primary Indicator": "",
         "Identifying Address": ""},
    ]
    return pd.DataFrame(rows)


def _flags(sheet):
    sub = cm.sheet_rows(_site_frame(), sheet).reset_index(drop=True)
    return sub, dict(zip(sub[cm.INTERNALID_COL], sub["Primary Indicator"])) \
        if "Primary Indicator" in sub.columns else (sub, {})


def test_acctsiteuses_primary_first_billing_per_customer():
    sub = cm.sheet_rows(_site_frame(), "HZ_IMP_ACCTSITEUSES_T").reset_index(drop=True)
    f = dict(zip(sub[cm.INTERNALID_COL], sub["Primary Indicator"]))
    check("NT-1 MIN billing (100) = Y", f["100"] == "Y", f)
    check("NT-1 other billing (300) = N", f["300"] == "N", f)
    check("NT-1 other billing (200) = N", f["200"] == "N", f)
    check("NT-1 shipping (900) = N", f["900"] == "N", f)
    check("NT-2 MIN billing (400) = Y", f["400"] == "Y", f)
    check("NT-2 other billing (500) = N", f["500"] == "N", f)
    check("NT-2 shipping (800) = N", f["800"] == "N", f)
    check("exactly two Y total", list(f.values()).count("Y") == 2, f)


def test_partysiteuses_uses_its_own_use_type_column():
    sub = cm.sheet_rows(_site_frame(), "HZ_IMP_PARTYSITEUSES_T").reset_index(drop=True)
    f = dict(zip(sub[cm.INTERNALID_COL], sub["Primary Indicator"]))
    check("NT-1 first billing Y", f["100"] == "Y", f)
    check("NT-2 first billing Y", f["400"] == "Y", f)
    check("shipping rows N", f["900"] == "N" and f["800"] == "N", f)
    check("exactly two Y", list(f.values()).count("Y") == 2, f)


def test_partysites_identifying_address_no_use_type_all_sites():
    # PARTYSITES has no use-type column, so every site row is eligible; the
    # identifying address is the MIN internalid per customer across ALL sites.
    sub = cm.sheet_rows(_site_frame(), "HZ_IMP_PARTYSITES_T").reset_index(drop=True)
    f = dict(zip(sub[cm.INTERNALID_COL], sub["Identifying Address"]))
    check("NT-1 identifying = MIN of all sites (100)", f["100"] == "Y", f)
    check("NT-2 identifying = MIN of all sites (400)", f["400"] == "Y", f)
    check("exactly two Y", list(f.values()).count("Y") == 2, f)


def test_contact_sheet_is_untouched():
    # ACCTCONTACTS is not a first-flag sheet; its Primary must stay blank (REC-46).
    df = _site_frame().copy()
    df[cm.GRAIN_COL] = "contact"
    sub = cm.sheet_rows(df, "HZ_IMP_ACCTCONTACTS_T")
    check("contact Primary Indicator left blank",
          set(sub["Primary Indicator"].astype(str)) == {""}, list(sub["Primary Indicator"]))


def test_missing_internalid_is_a_noop():
    df = _site_frame().drop(columns=[cm.INTERNALID_COL])
    sub = cm.sheet_rows(df, "HZ_IMP_ACCTSITEUSES_T")
    check("no internalid -> flag untouched (all blank)",
          set(sub["Primary Indicator"].astype(str)) == {""}, list(sub["Primary Indicator"]))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nPrimary/Identifying flag: first billing row per customer = Y, per sheet.")
