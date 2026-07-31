"""Customer Import: the CSV column order is NOT the worksheet column order.

From Customer_Import_FBDI_Sequence_Mapping_V2 1.xlsx (analyst, 30-Jul). Each of
the fifteen interface tabs lists its columns twice — once as the Oracle FBDI
worksheet shows them, once as the generated CSV carries them. On twelve tabs the
two lists agree. On three they do not:

    HZ_IMP_ACCTSITES_T            Account Number  #3 -> #46
                                  Party Site Number #8 -> #47
    HZ_IMP_ACCTSITEUSES_T         Account Number  #1 -> #43
                                  Party Site Number #2 -> #44
    RA_CUSTOMER_PROFILES_INT_ALL  Party Original System #2 -> #73, its Reference
                                  #3 -> #74, Credit Analyst #10 -> #103, Credit
                                  Review Cycle #11 -> #104, Last Review Date
                                  #12 -> #105, Next Review Date #13 -> #106

This is the worst shape a defect can take here. The column COUNT is identical, so
a file built in worksheet order is indistinguishable from a correct one by eye —
and the CSVs are HEADERLESS, so position is the only thing carrying meaning. Ship
HZ_IMP_ACCTSITEUSES_T in worksheet order and Oracle reads the Account Number as
the Account Site Source System, for every row, without raising anything.

Pure: stdlib + pandas + the layout module. No database.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd                                              # noqa: E402
from app.services.supplier_fbdi_layout import (                  # noqa: E402
    apply_customer_layout, customer_csv_name_for, customer_load_sequence,
    customer_sheet_spec,
)

_DOC = json.loads((Path(__file__).resolve().parent.parent / "app" / "data"
                   / "customer_fbdi_column_order.json").read_text(encoding="utf-8"))
REORDERING = ["HZ_IMP_ACCTSITES_T", "HZ_IMP_ACCTSITEUSES_T",
              "RA_CUSTOMER_PROFILES_INT_ALL"]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_all_fifteen_interfaces_are_present():
    check("15 interfaces", len(_DOC["sheets"]) == 15, f"got {len(_DOC['sheets'])}")
    check("15 in the load sequence", len(customer_load_sequence()) == 15)
    check("the sequence covers exactly the sheets",
          set(customer_load_sequence()) == set(_DOC["sheets"]))


def test_the_load_sequence_puts_parents_before_children():
    """Oracle rejects a child row whose parent has not loaded, so this ordering is
    part of the deliverable rather than a presentation choice."""
    seq = customer_load_sequence()
    def before(a, b):
        return seq.index(a) < seq.index(b)
    check("parties before party sites", before("HZ_IMP_PARTIES_T", "HZ_IMP_PARTYSITES_T"))
    check("party sites before their uses",
          before("HZ_IMP_PARTYSITES_T", "HZ_IMP_PARTYSITEUSES_T"))
    check("parties before accounts", before("HZ_IMP_PARTIES_T", "HZ_IMP_ACCOUNTS_T"))
    check("accounts before account sites",
          before("HZ_IMP_ACCOUNTS_T", "HZ_IMP_ACCTSITES_T"))
    check("account sites before their uses",
          before("HZ_IMP_ACCTSITES_T", "HZ_IMP_ACCTSITEUSES_T"))
    check("accounts before account contacts",
          before("HZ_IMP_ACCOUNTS_T", "HZ_IMP_ACCTCONTACTS_T"))
    check("profiles load last", seq[-1] == "RA_CUSTOMER_PROFILES_INT_ALL")


def test_exactly_three_interfaces_reorder_and_we_know_which():
    got = sorted(k for k, v in _DOC["sheets"].items() if v["reorders"])
    check("the three known ones", got == sorted(REORDERING), f"got {got}")


def test_the_twelve_others_are_byte_identical_orders():
    """If a tab does not reorder, the layout must be a no-op — quietly shuffling a
    conforming interface would be a defect introduced by the fix."""
    for name, spec in _DOC["sheets"].items():
        if name in REORDERING:
            continue
        check(f"{name} orders agree", spec["fbdi_order"] == spec["csv_order"])


def test_account_number_moves_to_the_end_on_the_two_site_interfaces():
    """The exact positions, not a rule of thumb.

    A first draft of this test asserted "lands in the last five" and failed: on
    HZ_IMP_ACCTSITES_T the pair lands at #46 and #47 of 89, after the thirty
    Descriptive Flexfield segments but BEFORE the Global Attribute block, whereas
    on HZ_IMP_ACCTSITEUSES_T they really are the final two of 45. There is no
    tidy rule — which is the argument for encoding the order as data and pinning
    the measured indices here.
    """
    EXPECTED = {
        "HZ_IMP_ACCTSITES_T":    {"Account Number": (3, 46), "Party Site Number": (8, 47)},
        "HZ_IMP_ACCTSITEUSES_T": {"Account Number": (1, 43), "Party Site Number": (2, 44)},
    }
    for name, cols in EXPECTED.items():
        spec = _DOC["sheets"][name]
        f, c = spec["fbdi_order"], spec["csv_order"]
        for col, (fi, ci) in cols.items():
            check(f"{name}: {col} sits at #{fi} in the worksheet",
                  f.index(col) == fi, f"got #{f.index(col)}")
            check(f"{name}: {col} moves to #{ci} in the CSV",
                  c.index(col) == ci, f"got #{c.index(col)}")
    # And on the smaller interface they genuinely are the last two columns.
    c = _DOC["sheets"]["HZ_IMP_ACCTSITEUSES_T"]["csv_order"]
    check("the pair closes HZ_IMP_ACCTSITEUSES_T",
          c[-2:] == ["Account Number", "Party Site Number"], f"got {c[-2:]}")


def test_the_profile_interface_moves_six_columns_to_the_end():
    spec = _DOC["sheets"]["RA_CUSTOMER_PROFILES_INT_ALL"]
    f, c = spec["fbdi_order"], spec["csv_order"]
    for col in ("Party Original System", "Party Original System Reference",
                "Credit Analyst", "Credit Review Cycle",
                "Last Review Date", "Next Review Date"):
        check(f"{col} moves later", c.index(col) > f.index(col) + 50,
              f"fbdi #{f.index(col)} -> csv #{c.index(col)}")


def test_the_reorder_actually_moves_the_data():
    """The point is not the list, it is the frame. Build a row where every value
    names its own column, reorder it, and confirm the values travelled."""
    spec = customer_sheet_spec("HZ_IMP_ACCTSITEUSES_T")
    fbdi = spec["fbdi_order"]
    df = pd.DataFrame([{c: f"<{c}>" for c in fbdi}], columns=fbdi)
    out = apply_customer_layout(df, "HZ_IMP_ACCTSITEUSES_T", True, for_csv=True)
    check("column order is now the CSV order", list(out.columns) == spec["csv_order"],
          f"got {list(out.columns)[:5]}")
    # Every cell still sits under its own name — nothing was shifted or lost.
    for c in out.columns:
        check(f"{c} kept its value", out.iloc[0][c] == f"<{c}>")
    check("no columns lost", len(out.columns) == len(fbdi))


def test_worksheet_order_is_still_available_for_the_xlsx():
    """The Excel workbook a human opens keeps Oracle's worksheet order. Only the
    CSV is reordered — that distinction is the whole point."""
    spec = customer_sheet_spec("HZ_IMP_ACCTSITES_T")
    df = pd.DataFrame([{c: 1 for c in spec["fbdi_order"]}], columns=spec["fbdi_order"])
    out = apply_customer_layout(df, "HZ_IMP_ACCTSITES_T", True, for_csv=False)
    check("xlsx keeps worksheet order", list(out.columns) == spec["fbdi_order"])


def test_a_non_customer_object_is_untouched():
    df = pd.DataFrame([{"B": 1, "A": 2}])
    out = apply_customer_layout(df, "HZ_IMP_ACCTSITES_T", False, for_csv=True)
    check("no-op", list(out.columns) == ["B", "A"])


def test_an_unknown_sheet_is_untouched():
    df = pd.DataFrame([{"B": 1, "A": 2}])
    out = apply_customer_layout(df, "SOME_OTHER_TABLE", True, for_csv=True)
    check("no-op", list(out.columns) == ["B", "A"])


def test_a_column_the_spec_does_not_list_is_kept():
    """A template that has gained a column must still round-trip — dropping it
    silently would be a worse failure than the one being fixed."""
    spec = customer_sheet_spec("HZ_IMP_PARTIES_T")
    cols = spec["csv_order"] + ["Some New Oracle Column"]
    df = pd.DataFrame([{c: 1 for c in cols}], columns=cols)
    out = apply_customer_layout(df, "HZ_IMP_PARTIES_T", True, for_csv=True)
    check("nothing dropped", len(out.columns) == len(cols))
    check("the stranger is appended", list(out.columns)[-1] == "Some New Oracle Column")


def test_oracle_csv_file_names():
    """Oracle matches files inside the zip by NAME. A correctly ordered CSV called
    HZ_IMP_PARTIES_T.csv is simply not read."""
    for iface, want in (("HZ_IMP_PARTIES_T", "HzImpPartiesT"),
                        ("HZ_IMP_ACCTSITEUSES_T", "HzImpAcctSiteUsesT"),
                        ("RA_CUSTOMER_PROFILES_INT_ALL", "RaCustomerProfilesIntAll"),
                        ("HZ_IMP_PERSONLANG", "HzImpPersonLang")):
        check(f"{iface} -> {want}", customer_csv_name_for(iface) == want,
              f"got {customer_csv_name_for(iface)}")


def test_the_field_counts_match_the_analyst_summary():
    """A cheap guard that the extraction did not lose or duplicate a column."""
    for name, spec in _DOC["sheets"].items():
        check(f"{name} field count", len(spec["csv_order"]) == spec["field_count"],
              f"{len(spec['csv_order'])} vs {spec['field_count']}")


def test_the_generator_uses_all_of_this():
    """Seam. A layout spec nothing calls is the inert-feature failure again."""
    out = (Path(__file__).resolve().parent.parent / "app" / "services"
           / "output_service.py").read_text(encoding="utf-8")
    check("the CSV reorder is applied", "_apply_customer_layout(sdf, s.sheet_name, for_csv=True)" in out)
    check("Oracle's file names are used", "_customer_csv_name(s.sheet_name)" in out)
    check("the load sequence orders the zip", "_customer_sheet_sort(" in out)
    check("and the sequence is numbered so it survives a listing",
          'f"{_i:02d}_{cbase}.csv"' in out)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall customer FBDI sequence checks passed")
