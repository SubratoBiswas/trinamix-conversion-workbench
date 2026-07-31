"""Customer Import: the CSV column order is NOT the worksheet column order.

From Customer_Import_FBDI_Sequence_Mapping_V2 2.xlsx (Tejaswi Medi, 29-Jul;
forwarded 31-Jul), which supersedes V2 1. Re-extracting V2_2 and diffing it against
what V2_1 produced gave column-for-column agreement on all fifteen interfaces and
the same three reorderings, so the second workbook is an independent confirmation
of the first rather than a correction. Its real additions are two: every CSV column
now ends with an explicit END row, and the Summary tab now names the CSV files.
Each of
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
    apply_customer_layout, customer_csv_name_for, customer_in_load_scope,
    customer_load_sequence, customer_sheet_spec,
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
    cols = [c for c in out.columns if c != "END"]
    check("column order is now the CSV order", cols == spec["csv_order"],
          f"got {cols[:5]}")
    # Every cell still sits under its own name — nothing was shifted or lost.
    for c in cols:
        check(f"{c} kept its value", out.iloc[0][c] == f"<{c}>")
    check("no columns lost", len(cols) == len(fbdi))


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
    kept = [c for c in out.columns if c != "END"]
    check("nothing dropped", len(kept) == len(cols))
    check("the stranger is appended", kept[-1] == "Some New Oracle Column")


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
    check("the CSV reorder is applied",
          "_apply_customer_layout(sdf, s.sheet_name, for_csv=True," in out)
    check("with the END terminator", "with_end=True)" in out)
    check("and only the loaded interfaces are written",
          "_customer_in_scope(s.sheet_name)" in out)
    check("Oracle's file names are used", "_customer_csv_name(s.sheet_name)" in out)
    check("the load sequence orders the zip", "_customer_sheet_sort(" in out)
    check("and the sequence is numbered so it survives a listing",
          'f"{_i:02d}_{cbase}.csv"' in out)


def test_v2_2_confirms_v2_1_rather_than_correcting_it():
    """Provenance. Two independently produced workbooks agreeing column-for-column
    is the strongest evidence available that the order is right — and if V2_2 had
    disagreed, the shipped CSVs would have been silently wrong for two days."""
    check("V2_2 is the recorded source", "V2 2.xlsx" in _DOC["_source"])
    check("and it says what it replaces", "V2 1.xlsx" in (_DOC.get("_supersedes") or ""))
    check("still the same three reorderings",
          sorted(k for k, v in _DOC["sheets"].items() if v["reorders"]) == sorted(REORDERING))


def test_every_csv_carries_the_end_terminator():
    """V2_2's substantive addition: every one of the 15 CSV columns now ends with an
    explicit END row. The supplier package has always written the terminator; the
    customer package never did, so Customer CSVs shipped without one."""
    for name, spec in _DOC["sheets"].items():
        check(f"{name} records the terminator", spec.get("csv_terminator") == "END")
    spec = customer_sheet_spec("HZ_IMP_PARTIES_T")
    df = pd.DataFrame([{c: "v" for c in spec["csv_order"]}], columns=spec["csv_order"])
    out = apply_customer_layout(df, "HZ_IMP_PARTIES_T", True, for_csv=True)
    check("END is the last column", list(out.columns)[-1] == "END")
    check("and it is on the data row", out.iloc[0]["END"] == "END")


def test_end_is_not_written_into_the_workbook_a_human_opens():
    """The Oracle template has no END column — it is a CSV record terminator."""
    spec = customer_sheet_spec("HZ_IMP_PARTIES_T")
    df = pd.DataFrame([{c: "v" for c in spec["fbdi_order"]}], columns=spec["fbdi_order"])
    out = apply_customer_layout(df, "HZ_IMP_PARTIES_T", True, for_csv=False)
    check("no END in the xlsx", "END" not in list(out.columns))


def test_the_terminator_is_not_doubled():
    """Applying the layout twice must not produce two END columns."""
    spec = customer_sheet_spec("HZ_IMP_PARTIES_T")
    df = pd.DataFrame([{c: "v" for c in spec["csv_order"]}], columns=spec["csv_order"])
    once = apply_customer_layout(df, "HZ_IMP_PARTIES_T", True, for_csv=True)
    twice = apply_customer_layout(once, "HZ_IMP_PARTIES_T", True, for_csv=True)
    check("one END", sum(1 for c in twice.columns if c == "END") == 1,
          f"got {list(twice.columns)[-3:]}")


def test_only_the_fifteen_loaded_interfaces_are_generated():
    """Tejaswini, 31-Jul: "they are working on 15 files only, mentioned in the sheet,
    so we do not have to generate all of the 19 FBDI output files."

    The four left out are the SAME four the analyst had been naming one at a time as
    per-field exclusions — "in all sheets except HZ_IMP_CLASSIFICS_T", "…except
    RA_CUST_PAY_METHOD_INT_ALL, RA_CUSTOMER_BANKS_INT_ALL, HZ_IMP_ACCOUNTRELS".
    Those exclusions were an analyst working around a file set that was too big.
    """
    for iface in customer_load_sequence():
        check(f"{iface} is generated", customer_in_load_scope(iface) is True)
    for iface in ("HZ_IMP_ACCOUNTRELS", "HZ_IMP_CLASSIFICS_T",
                  "RA_CUST_PAY_METHOD_INT_ALL", "RA_CUSTOMER_BANKS_INT_ALL"):
        check(f"{iface} is not", customer_in_load_scope(iface) is False)
    named = {x["sheet"] for x in _DOC["excluded_interfaces"]}
    check("all four are named with a reason", len(named) == 4)
    check("each carries a reason",
          all((x.get("reason") or "").strip() for x in _DOC["excluded_interfaces"]))


def test_an_unknown_interface_is_never_silently_dropped():
    """Scope narrows a deliverable, so its failure mode must be inclusion. A sheet
    the spec has never heard of is generated, not discarded."""
    check("unknown sheet stays in", customer_in_load_scope("HZ_IMP_SOMETHING_NEW") is True)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall customer FBDI sequence checks passed")
