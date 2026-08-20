"""The FBDI layout policy (Phase 3, slice 2), pinned as pure decisions.

``supplier_fbdi_layout`` was split into a STORE (reads the four JSON spec files and
caches them) and a POLICY (``app.domain.fbdi.layout`` — reorders a frame, names a
file, decides load scope). The policy takes the loaded spec as an argument and
touches no disk, so every rule below runs against a hand-built spec with no data
directory in sight.

The load-bearing invariant is the reorder: these CSVs ship HEADERLESS, so column
POSITION is the only thing carrying meaning. A list that is right about the names
and wrong about the order loads silently into the wrong fields — the exact defect
that cannot be caught by reading the output.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.domain.fbdi import layout as L


def _frame(cols, n=2):
    return pd.DataFrame({c: [f"{c}#{r}" for r in range(n)] for c in cols})


# --- normalisation -----------------------------------------------------------

def test_norm_hdr_reconciles_cosmetic_header_differences():
    assert L.norm_hdr("Supplier Name*") == L.norm_hdr("Supplier Name *") == "suppliername"
    assert L.norm_hdr(None) == "none"          # str(None) -> "none"; matches original


def test_safe_sheet_name_falls_back_to_sheet():
    assert L.safe_sheet_name("HZ_IMP_PARTIES_T") == "HZ_IMP_PARTIES_T"
    assert L.safe_sheet_name("a b/c") == "a_b_c"
    assert L.safe_sheet_name("") == "sheet"


# --- reorder (the position-critical core) ------------------------------------

def test_reorder_follows_the_spec_order():
    df = _frame(["Gamma", "Alpha", "Beta"])
    out = L.reorder_to(df, ["Alpha", "Beta", "Gamma"])
    assert list(out.columns) == ["Alpha", "Beta", "Gamma"]


def test_reorder_keeps_unlisted_columns_appended_never_dropped():
    df = _frame(["Alpha", "Extra", "Beta"])
    out = L.reorder_to(df, ["Beta", "Alpha"])
    # listed first in spec order, then the unlisted one in its original relative place
    assert list(out.columns) == ["Beta", "Alpha", "Extra"]


def test_reorder_gives_a_twice_listed_name_two_positions():
    # The LOCATIONS defect: Oracle lists "Address Line 1" at two columns; the frame
    # carries it ONCE. The second position must become an EMPTY column, not vanish —
    # a missing column shifts the whole tail one to the left.
    df = _frame(["Address Line 1", "City"])
    out = L.reorder_to(df, ["Address Line 1", "City", "Address Line 1"])
    assert list(out.columns) == ["Address Line 1", "City", "Address Line 1"]
    # first carries the data, the duplicate position is blank on every row
    assert list(out.iloc[:, 0]) == ["Address Line 1#0", "Address Line 1#1"]
    assert list(out.iloc[:, 2]) == ["", ""]


def test_reorder_inserts_empty_for_a_name_the_frame_lacks():
    df = _frame(["Alpha"])
    out = L.reorder_to(df, ["Alpha", "Missing"])
    assert list(out.columns) == ["Alpha", "Missing"]
    assert list(out["Missing"]) == ["", ""]


# --- END terminator ----------------------------------------------------------

def test_with_end_appends_once_and_is_idempotent():
    df = _frame(["Alpha"])
    once = L.with_end(df)
    assert list(once.columns) == ["Alpha", "END"] and list(once["END"]) == ["END", "END"]
    twice = L.with_end(once)
    assert list(twice.columns) == ["Alpha", "END"]      # not two END columns


# --- supplier ----------------------------------------------------------------

def test_apply_supplier_layout_reorders_blanks_obsolete_and_appends_end():
    order = {"suppliers": ["Supplier Name", "Batch ID"]}
    df = _frame(["Batch ID", "Supplier Name", "Tax Number-Obsoleted"])
    out = L.apply_supplier_layout(df, "Suppliers", True, order, with_end_col=True)
    assert list(out.columns) == ["Supplier Name", "Batch ID", "Tax Number-Obsoleted", "END"]
    assert list(out["Tax Number-Obsoleted"]) == ["", ""]      # obsolete blanked
    assert list(out["END"]) == ["END", "END"]


def test_apply_supplier_layout_batch_id_first_and_no_end():
    order = {"suppliers": ["Supplier Name", "Batch ID"]}
    df = _frame(["Supplier Name", "Batch ID"])
    out = L.apply_supplier_layout(df, "Suppliers", True, order,
                                  with_end_col=False, batch_id_first=True)
    assert list(out.columns)[0] == "Batch ID"
    assert "END" not in out.columns


def test_apply_supplier_layout_noop_when_not_supplier():
    df = _frame(["B", "A"])
    out = L.apply_supplier_layout(df, "Suppliers", False, {"suppliers": ["A", "B"]})
    assert list(out.columns) == ["B", "A"]      # untouched


# --- customer ----------------------------------------------------------------

def _cust_spec():
    return {
        "hzimppartiest": {"csv_order": ["B", "A"], "fbdi_order": ["A", "B"], "csv": "HzImpPartiesT"},
        "__sequence__": ["HZ_IMP_PARTIES_T", "HZ_IMP_LOCATIONS_T"],
        "__scope__": ["HZ_IMP_PARTIES_T"],
        "__excluded__": [{"sheet": "HZ_IMP_ACCOUNTRELS"}],
    }


def test_apply_customer_layout_for_csv_picks_csv_order_and_adds_end():
    df = _frame(["A", "B"])
    out = L.apply_customer_layout(df, "HZ_IMP_PARTIES_T", True, _cust_spec(), for_csv=True)
    assert list(out.columns) == ["B", "A", "END"]          # csv_order + END (default for csv)


def test_apply_customer_layout_fbdi_order_has_no_end_by_default():
    df = _frame(["A", "B"])
    out = L.apply_customer_layout(df, "HZ_IMP_PARTIES_T", True, _cust_spec(), for_csv=False)
    assert list(out.columns) == ["A", "B"]                 # fbdi_order, no END


def test_apply_customer_layout_noop_when_sheet_unknown():
    df = _frame(["B", "A"])
    out = L.apply_customer_layout(df, "NOT_A_SHEET", True, _cust_spec(), for_csv=True)
    assert list(out.columns) == ["B", "A"]


def test_customer_in_load_scope_excluded_unknown_and_in():
    spec = _cust_spec()
    assert L.customer_in_load_scope(spec, "HZ_IMP_PARTIES_T") is True     # in scope
    assert L.customer_in_load_scope(spec, "HZ_IMP_ACCOUNTRELS") is False  # excluded
    assert L.customer_in_load_scope(spec, "HZ_IMP_LOCATIONS_T") is False  # known, not in scope
    assert L.customer_in_load_scope(spec, "SOMETHING_NEW") is True        # unknown -> IN
    assert L.customer_in_load_scope({}, "anything") is True               # no scope -> IN


def test_customer_csv_name_for():
    assert L.customer_csv_name_for(_cust_spec(), "HZ_IMP_PARTIES_T") == "HzImpPartiesT"
    assert L.customer_csv_name_for(_cust_spec(), "unknown") is None


# --- bom ---------------------------------------------------------------------

def _bom_spec(with_end=False):
    return {"order": {"egpstructures": ["A", "B"]},
            "csv": {"egpstructures": "EgpStructuresInterface"},
            "with_end": with_end}


def test_apply_bom_layout_reorders_and_defaults_to_no_end():
    df = _frame(["B", "A"])
    out = L.apply_bom_layout(df, "EgpStructures", True, _bom_spec())
    assert list(out.columns) == ["A", "B"]          # reordered, NO END (spec says False)


def test_apply_bom_layout_respects_spec_end_flag():
    df = _frame(["B", "A"])
    out = L.apply_bom_layout(df, "EgpStructures", True, _bom_spec(with_end=True))
    assert list(out.columns) == ["A", "B", "END"]


def test_bom_helpers_names_and_signal():
    spec = _bom_spec()
    assert L.is_bom_sheet(spec, "EgpStructures") is True
    assert L.is_bom_sheet(spec, "not_bom") is False
    assert L.bom_csv_name_for(spec, "EgpStructures") == "EgpStructuresInterface"
    assert L.bom_appends_end(spec) is False


# --- supplier naming ---------------------------------------------------------

def test_supplier_name_lookups():
    names = {"zip_by_primary_sheet": {"suppliers": "SupplierImport"},
             "csv_by_sheet": {"suppliers": "PozSuppliersInt"}}
    assert L.zip_name_for(names, "Suppliers") == "SupplierImport"
    assert L.csv_name_for(names, "Suppliers") == "PozSuppliersInt"
    # unknown sheet falls back to a safe sheet name
    assert L.csv_name_for(names, "Weird Sheet") == "Weird_Sheet"
    assert L.zip_name_for(names, "unknown") is None


# --- the facade still speaks the same public API -----------------------------

def test_store_facade_delegates_and_loads_real_specs():
    from app.services import supplier_fbdi_layout as S
    # smoke: the store loads and the public surface is intact and callable
    assert isinstance(S.customer_load_sequence(), list)
    assert isinstance(S.customer_in_load_scope("HZ_IMP_PARTIES_T"), bool)
    assert isinstance(S.is_bom_sheet("EgpStructures"), bool)
    # a supplier reorder round-trips through the store's loaded spec
    out = S.apply_supplier_layout(_frame(["Batch ID", "X"]), "Suppliers", False)
    assert list(out.columns) == ["Batch ID", "X"]      # no-op for non-supplier
