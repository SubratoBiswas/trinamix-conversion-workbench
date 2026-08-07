"""Each BOM interface gets the grain its validation rule requires.

NEXTPOWER BOM validation feedback (05-Aug). A flat extract has one row per
component line; the four interfaces each need a different grain, and before this
the generator shipped every source row onto every tab. This pins the reshape:
dedup per the doc's uniqueness key, the substitute filter, numeric Item Sequence.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.bom_structure_service import (                  # noqa: E402
    reshape_for_sheet, missing_mandatory,
)


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# A tiny structure: parent 1.1.1.1 with three components, one of which has a
# substitute and a reference designator; plus a duplicate line to prove dedup.
def _finalized(kind: str) -> pd.DataFrame:
    rows = [
        # parent, child, sub, refdes, findno, qty
        ("1.1.1.1", "2001", "9001", "R1", "", "8"),
        ("1.1.1.1", "2002", "",     "",   "", "1"),
        ("1.1.1.1", "2003", "",     "R3", "", "2"),
        ("1.1.1.1", "2001", "9001", "R1", "", "8"),   # exact duplicate line
        ("2.2.2.2", "2001", "",     "",   "50", "4"),  # a second structure
    ]
    d = pd.DataFrame(rows, columns=["parent", "child", "sub", "ref", "findno", "qty"])
    out = pd.DataFrame()
    out["Transaction Type"] = ["SYNC"] * len(d)
    out["Batch Number"] = ["NP_BOM"] * len(d)
    out["Structure Name"] = ["Primary"] * len(d)
    out["Organization Code"] = ["IMO"] * len(d)
    if kind == "structures":
        out["Item Name"] = d["parent"]
        out["Effective Date"] = ["2026/08/05"] * len(d)
    else:
        out["Structure Item Name"] = d["parent"]
        out["Component Item Name"] = d["child"]
    if kind == "components":
        out["Item Sequence"] = d["findno"]
        out["Quantity"] = d["qty"]
    if kind == "substitutes":
        out["Substitute Item Name"] = d["sub"]
        out["Substitute Quantity"] = ["1"] * len(d)
    if kind == "reference_designators":
        out["Reference Designator"] = d["ref"]
    return out


def test_structures_is_one_row_per_parent():
    r = reshape_for_sheet(_finalized("structures"), "EGP_STRUCTURES_INTERFACE")
    check("two distinct parents", len(r) == 2, f"got {len(r)}")
    check("the parent items are the two structures",
          set(r["Item Name"]) == {"1.1.1.1", "2.2.2.2"}, f"got {list(r['Item Name'])}")


def test_components_dedups_and_numbers_the_sequence():
    r = reshape_for_sheet(_finalized("components"), "EGP_COMPONENTS_INTERFACE")
    # 1.1.1.1 has 3 distinct children (2001/2002/2003, the dup dropped); 2.2.2.2 has 1.
    check("duplicate component line dropped", len(r) == 4, f"got {len(r)}")
    first = r[r["Structure Item Name"] == "1.1.1.1"]
    check("first structure sequenced 10/20/30",
          first["Item Sequence"].tolist() == ["10", "20", "30"],
          f"got {first['Item Sequence'].tolist()}")
    # Per-parent RESET (Jithendran, 07-Aug): each parent restarts at 10 — the source
    # Find_number (50) is regenerated, not carried through, so the sequence is
    # per-parent and not continuous across the file.
    second = r[r["Structure Item Name"] == "2.2.2.2"]
    check("second structure restarts at 10 (source 50 regenerated)",
          second["Item Sequence"].tolist() == ["10"],
          f"got {second['Item Sequence'].tolist()}")


def test_substitutes_only_lines_with_a_substitute():
    r = reshape_for_sheet(_finalized("substitutes"), "EGP_SUB_COMPS_INTERFACE")
    check("only the one substitute line, deduped", len(r) == 1, f"got {len(r)}")
    check("it is the 9001 substitute",
          r["Substitute Item Name"].tolist() == ["9001"],
          f"got {r['Substitute Item Name'].tolist()}")


def test_reference_designators_only_lines_with_a_designator():
    r = reshape_for_sheet(_finalized("reference_designators"), "EGP_REF_DESGS_INTERFACE")
    check("only the two lines that carry a designator", len(r) == 2, f"got {len(r)}")
    check("they are R1 and R3",
          set(r["Reference Designator"]) == {"R1", "R3"},
          f"got {list(r['Reference Designator'])}")


def test_an_unrecognised_sheet_passes_through():
    df = pd.DataFrame({"x": [1, 1, 2]})
    check("not a BOM tab -> unchanged",
          reshape_for_sheet(df, "SOME_OTHER_SHEET").equals(df))


def test_missing_mandatory_reports_a_blank_required_column():
    d = _finalized("structures")
    d["Item Name"] = ""   # blank out a mandatory column
    miss = missing_mandatory(d, "EGP_STRUCTURES_INTERFACE")
    check("Item Name reported missing", "Item Name" in miss, f"got {miss}")
    check("a populated mandatory column is not reported",
          "Structure Name" not in miss, f"got {miss}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nBOM reshape holds")
