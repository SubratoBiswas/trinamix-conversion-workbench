"""BOM Import: one column order for both formats, and NO END terminator.

From ``BOM_Import_FBDI_Sequence_Mapping 1.xlsx`` (handed over 04-Aug-2026). Four
interface tabs, each listing its columns twice — once as the Oracle FBDI worksheet
shows them, once as the generated CSV carries them. Unlike Customer, where three of
fifteen interfaces disagree, all four BOM tabs agree column for column, so a single
list serves both formats:

    EGP_STRUCTURES_INTERFACE       74 columns
    EGP_COMPONENTS_INTERFACE      103 columns
    EGP_SUB_COMPS_INTERFACE        65 columns
    EGP_REF_DESGS_INTERFACE        65 columns

The claim these tests exist to protect is the SECOND one: no BOM tab carries an END
column. The supplier package has always appended an END record terminator, BOM
reuses that machinery, and inheriting it would hand Oracle a field it does not
expect on these four interfaces. That is a defect you cannot see by opening the
file — which is the same reason the order itself is data rather than a constant.

These CSVs are HEADERLESS. Column POSITION is the only thing carrying meaning, so a
list that is right about the names and wrong about the order loads silently into
the wrong fields.

Pure: stdlib + pandas + the layout module. No database.
"""
import io
import json
import os
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd                                              # noqa: E402
from app.services.supplier_fbdi_layout import (                  # noqa: E402
    apply_bom_layout, apply_supplier_layout, bom_appends_end, bom_col_order,
    bom_csv_name_for, bom_sheet_order, is_bom_sheet,
)

_BACKEND = Path(__file__).resolve().parent.parent
_DOC = json.loads((_BACKEND / "app" / "data"
                   / "bom_fbdi_column_order.json").read_text(encoding="utf-8"))
INTERFACES = {
    "EGP_STRUCTURES_INTERFACE": (74, "EgpStructuresInterface.csv"),
    "EGP_COMPONENTS_INTERFACE": (103, "EgpComponentsInterface.csv"),
    "EGP_SUB_COMPS_INTERFACE": (65, "EgpSubCompsInterface.csv"),
    "EGP_REF_DESGS_INTERFACE": (65, "EgpRefDesgsInterface.csv"),
}


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _code_only(path: Path) -> str:
    """Source with comments blanked out, everything else byte-for-byte intact.

    Three source-reading tests failed in one session because a comment quoted the
    expression the test was looking for. Blanking by token position rather than by
    regex keeps a '#' inside a string literal — and the line numbers — intact.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    src = "\n".join(lines) + "\n"
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        (srow, scol), (_, ecol) = tok.start, tok.end
        ln = lines[srow - 1]
        lines[srow - 1] = ln[:scol] + " " * (ecol - scol) + ln[ecol:]
    return "\n".join(lines)


def test_all_four_interfaces_are_present_with_the_counts_the_analyst_gave():
    check("four interfaces", len(_DOC["order"]) == 4, f"got {len(_DOC['order'])}")
    for iface, (count, _csv) in INTERFACES.items():
        got = len(_DOC["order"][iface])
        check(f"{iface} has {count} columns", got == count, f"got {got}")


def test_the_spec_says_there_is_no_end_column():
    """The flag is data, not a constant, because it is the value most likely to be
    got wrong by inheritance from the supplier package."""
    check("append_end_column is false", _DOC["append_end_column"] is False)
    check("and the accessor agrees", bom_appends_end() is False)


def test_no_interface_lists_an_end_column():
    """Belt and braces on the flag: if a tab really did carry a terminator it would
    show up here as a column named END, and the flag would be the thing that is
    wrong."""
    for iface, cols in _DOC["order"].items():
        offenders = [c for c in cols if str(c).strip().upper() == "END"]
        check(f"{iface} has no END column", not offenders, f"got {offenders}")


def test_the_end_terminator_is_not_inherited_from_the_supplier_package():
    """The whole point. The same frame through the two layouts: supplier gains an
    END column, BOM does not. A BOM package that inherited END would add a field
    Oracle does not expect, and nothing downstream would say so."""
    order = bom_sheet_order("EGP_STRUCTURES_INTERFACE")
    df = pd.DataFrame([{c: "v" for c in order}], columns=order)
    bom = apply_bom_layout(df.copy(), "EGP_STRUCTURES_INTERFACE", True)
    check("BOM writes no terminator", "END" not in list(bom.columns),
          f"got {list(bom.columns)[-3:]}")
    sup = apply_supplier_layout(df.copy(), "POZ_SUPPLIERS_INT", True)
    check("while supplier still does", list(sup.columns)[-1] == "END")


def test_the_default_is_the_guard_not_the_caller():
    """A caller that forgets the flag must still get BOM's answer, not supplier's —
    the default reads the spec file rather than hard-coding either behaviour."""
    order = bom_sheet_order("EGP_COMPONENTS_INTERFACE")
    df = pd.DataFrame([{c: "v" for c in order}], columns=order)
    out = apply_bom_layout(df, "EGP_COMPONENTS_INTERFACE", True)
    check("no terminator by default", "END" not in list(out.columns))


def test_an_explicit_flag_still_wins():
    """The parameter is not decorative. If the spec is ever superseded by a
    workbook that does carry a terminator, the machinery is already there."""
    order = bom_sheet_order("EGP_SUB_COMPS_INTERFACE")
    df = pd.DataFrame([{c: "v" for c in order}], columns=order)
    out = apply_bom_layout(df, "EGP_SUB_COMPS_INTERFACE", True, with_end=True)
    check("END is last when asked for", list(out.columns)[-1] == "END")
    check("and it is on the data row", out.iloc[0]["END"] == "END")


def test_the_reorder_actually_moves_the_data():
    """The point is not the list, it is the frame. Build a row in REVERSED order
    where every value names its own column, apply the layout, and confirm both that
    the order is now the spec's and that every value travelled with its header."""
    for iface in INTERFACES:
        order = bom_sheet_order(iface)
        scrambled = list(reversed(order))
        df = pd.DataFrame([{c: f"<{c}>" for c in scrambled}], columns=scrambled)
        out = apply_bom_layout(df, iface, True)
        check(f"{iface} is in the spec order", list(out.columns) == order,
              f"got {list(out.columns)[:4]}")
        for c in order:
            check(f"{iface}: {c} kept its value", out.iloc[0][c] == f"<{c}>")


def test_column_by_column_against_the_json():
    """The handoff asks for exactly this before anyone loads a generated file:
    verify the columns position by position rather than by count."""
    for iface, (count, _csv) in INTERFACES.items():
        order = bom_sheet_order(iface)
        check(f"{iface} accessor matches the file", order == _DOC["order"][iface])
        for i, col in enumerate(_DOC["order"][iface]):
            check(f"{iface} #{i} is {col}", order[i] == col, f"got {order[i]}")


def test_oracle_csv_file_names():
    """Oracle matches the file inside the zip by NAME. A correctly ordered CSV
    called EGP_STRUCTURES_INTERFACE.csv is simply not read."""
    for iface, (_count, csv) in INTERFACES.items():
        check(f"{iface} -> {csv}", bom_csv_name_for(iface) == csv,
              f"got {bom_csv_name_for(iface)}")


def test_the_file_names_are_not_prefixed_by_the_generator():
    """The Customer package numbers its files so the load order survives a
    directory listing. BOM must not copy that: the spec's names are complete file
    names, and a prefix in front of one is a name Oracle does not match."""
    for _iface, (_count, csv) in INTERFACES.items():
        check(f"{csv} carries its own extension", csv.endswith(".csv"))
    out = _code_only(_BACKEND / "app" / "services" / "output_service.py")
    check("written verbatim", "zf.writestr(_bname or" in out)


def test_a_non_bom_object_is_untouched():
    df = pd.DataFrame([{"B": 1, "A": 2}])
    out = apply_bom_layout(df, "EGP_STRUCTURES_INTERFACE", False)
    check("no-op", list(out.columns) == ["B", "A"])


def test_an_unknown_sheet_is_untouched():
    df = pd.DataFrame([{"B": 1, "A": 2}])
    out = apply_bom_layout(df, "SOME_OTHER_TABLE", True)
    check("no-op", list(out.columns) == ["B", "A"])


def test_a_column_the_spec_does_not_list_is_kept():
    """A template that has gained a column must still round-trip — dropping it
    silently would be a worse failure than the one being fixed."""
    order = bom_sheet_order("EGP_REF_DESGS_INTERFACE")
    cols = order + ["Some New Oracle Column"]
    df = pd.DataFrame([{c: 1 for c in cols}], columns=cols)
    out = apply_bom_layout(df, "EGP_REF_DESGS_INTERFACE", True)
    check("nothing dropped", len(out.columns) == len(cols), f"got {len(out.columns)}")
    check("the stranger is appended", list(out.columns)[-1] == "Some New Oracle Column")


def test_the_sheet_test_is_what_identifies_a_bom_object():
    """An object name cannot carry this on its own: the same object travels under
    BOM, Bill of Materials and Item Structure, and a bare substring test on 'bom'
    also fires on ordinary words."""
    for iface in INTERFACES:
        check(f"{iface} is recognised", is_bom_sheet(iface) is True)
    check("a supplier interface is not", is_bom_sheet("POZ_SUPPLIERS_INT") is False)
    check("nor is an item interface", is_bom_sheet("EGP_SYSTEM_ITEMS_INTERFACE") is False)


def test_the_generator_uses_all_of_this():
    """Seam. A layout spec nothing calls is the inert-feature failure again — the
    one that has already cost customer_sheet_scope, blank_sheets and SELF_LOOKUP.
    This reads the CALLER, so the wiring cannot rot while the data stays right.
    """
    out = _code_only(_BACKEND / "app" / "services" / "output_service.py")
    check("the layout is imported", "apply_bom_layout as _bom_layout" in out)
    check("a BOM object is detected", "_is_bom = any(_is_bom_sheet(s.sheet_name)" in out)
    check("the reorder is applied to each sheet",
          "sdf = _apply_bom_layout(sdf, s.sheet_name)" in out)
    check("Oracle's file names are used", "_bname = _bom_csv_name(s.sheet_name)" in out)
    check("and the package is written", 'name = f"BOMImport_{ts}.zip"' in out)
    check("the package needs a sheet the spec names",
          "if _is_bom and any(_is_bom_sheet(s.sheet_name) for s in sheets_with_fields):" in out)


def test_the_generator_passes_the_end_flag_explicitly():
    """START_HERE names this as the thing to get right, so it is asserted on the
    caller and not only on the default. The supplier call in the same file still
    asks for its terminator, which is what makes the BOM call meaningful.
    """
    out = _code_only(_BACKEND / "app" / "services" / "output_service.py")
    check("BOM asks for no terminator",
          "_bom_layout(sdf, sheet_name, _is_bom, with_end=False)" in out)
    check("and nothing asks BOM for one", "_bom_layout(sdf, sheet_name, _is_bom, with_end=True)"
          not in out)


def test_no_path_reaches_a_bom_csv_without_the_layout():
    """The single-file fallback is a real route out of the generator. It applied the
    supplier layout and nothing else, so a BOM conversion that did not take the
    package branch would have shipped in template order."""
    out = _code_only(_BACKEND / "app" / "services" / "output_service.py")
    check("the fallback applies it too", "fdf = _apply_bom_layout(fdf, _sname)" in out)


def test_the_spec_records_its_provenance_and_both_analyst_claims():
    """Provenance. Both claims were verified against the workbook rather than
    assumed, and the file says so — which is what makes it safe to act on."""
    check("the source workbook is named", "BOM_Import_FBDI_Sequence_Mapping" in _DOC["_source"])
    check("it is dated", _DOC["_effective_date"] == "2026-08-04")
    claims = " ".join(_DOC["_analyst"]).lower()
    check("the same-order claim is recorded", "sequence are same" in claims)
    check("the no-END claim is recorded", "no end column" in claims)
    check("and why position matters", "position" in _DOC["_why_this_matters"].lower())


def test_the_orders_are_free_of_duplicates():
    """A duplicated header would silently drop a column during the reorder: the
    second occurrence matches the same frame column, and the real one falls through
    to the appended tail in the wrong position."""
    for iface, cols in _DOC["order"].items():
        check(f"{iface} has no repeated header", len(set(cols)) == len(cols),
              f"{len(cols) - len(set(cols))} repeated")


def test_every_named_interface_has_a_csv_name():
    """Order without a file name is half a deliverable — Oracle would not read it."""
    for iface in _DOC["order"]:
        check(f"{iface} has a file name", bool(bom_csv_name_for(iface)))
    check("and there are no orphan names",
          set(_DOC["csv_file_names"]) == set(_DOC["order"]))


def test_bom_col_order_is_keyed_for_matching():
    """Sheet names arrive spelled however the template spells them, so the lookup is
    normalised — the raw JSON key must not be the lookup key."""
    check("lookup by normalised name", "egpstructuresinterface" in bom_col_order())
    check("spacing and case do not matter",
          bom_sheet_order("egp structures interface") == _DOC["order"]["EGP_STRUCTURES_INTERFACE"])


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall BOM FBDI sequence checks passed")
