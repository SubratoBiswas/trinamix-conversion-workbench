"""Supplier / Customer / BOM FBDI output layout + file-naming — the STORE half.

WHY THIS EXISTS
---------------
The analyst-driven column reorder, the END record-terminator, the Customer load
scope, and the Oracle zip/CSV file names are pure functions of a frame and a spec.
They were extracted from ``output_service`` so they could be unit tested without
the Beanie/Mongo/pydantic stack; Phase 3 slice 2 took the DECISIONS the rest of the
way into the domain (``app.domain.fbdi.layout``), which is pure and never touches
disk. This module keeps the other half — it reads and caches the four bundled spec
files and hands the loaded specs to the domain. The public API and every signature
are unchanged; only the seam moved.

Driven by bundled data files, one per object, because the three objects genuinely
disagree about the rules and a shared spec would have to lie about one of them:
  data/supplier_fbdi_column_order.json  — per-interface CSV column sequence
                                          (ConvNXP_All.xlsm "Supplier Import" tab)
  data/supplier_fbdi_file_names.json    — zip name + per-sheet CSV names (Tejaswi)
  data/customer_fbdi_column_order.json  — worksheet order AND CSV order, which
                                          differ on 3 of 15 interfaces; END appended
  data/bom_fbdi_column_order.json       — ONE order serving both formats on all 4
                                          interfaces; NO END terminator

The generator calls these; the reorder is applied to an interface sheet's frame,
END is appended as the last column on every row, and (in ``output_service``) the
CSVs are written HEADERLESS and packaged in the correctly-named zip.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.domain.fbdi import layout as _L
# Re-export the pure helpers so existing ``from …supplier_fbdi_layout import
# norm_hdr`` call sites keep working, and so this module's own loaders below key
# their caches with the exact same normalisation the domain uses.
from app.domain.fbdi.layout import norm_hdr, safe_sheet_name  # noqa: F401

_DATA = Path(__file__).resolve().parent.parent / "data"
_ORDER_FILE = _DATA / "supplier_fbdi_column_order.json"
_NAMES_FILE = _DATA / "supplier_fbdi_file_names.json"
_CUSTOMER_FILE = _DATA / "customer_fbdi_column_order.json"
_BOM_FILE = _DATA / "bom_fbdi_column_order.json"

_order_cache: dict | None = None
_names_cache: dict | None = None
_customer_cache: dict | None = None
_bom_cache: dict | None = None


# --- spec loaders (the store) -------------------------------------------------

def customer_layout() -> dict:
    """The Customer Import layout, keyed by normalised interface-table name.

    Oracle's Customer Import workbook and the CSVs its macro generates DO NOT share
    a column order, and the mismatch is silent: the column COUNT is identical, so a
    file built in worksheet order looks perfectly well formed and loads every value
    one or more columns out of place. Three of the fifteen interfaces are affected.
    The CSVs are written HEADERLESS, so position is the only thing carrying meaning.
    This is exactly the class of defect that cannot be caught by reading the output
    — which is why the order is data, and tested.
    """
    global _customer_cache
    if _customer_cache is None:
        try:
            doc = json.loads(_CUSTOMER_FILE.read_text(encoding="utf-8"))
            _customer_cache = {norm_hdr(k): v for k, v in (doc.get("sheets") or {}).items()}
            _customer_cache["__sequence__"] = doc.get("load_sequence") or []
            _customer_cache["__scope__"] = doc.get("load_scope") or doc.get("load_sequence") or []
            _customer_cache["__excluded__"] = doc.get("excluded_interfaces") or []
        except Exception:  # noqa: BLE001 — a missing file just disables the reorder
            _customer_cache = {}
    return _customer_cache


def bom_layout() -> dict:
    """The BOM Import layout — column order, Oracle CSV names, and the END flag.

    From ``BOM_Import_FBDI_Sequence_Mapping 1.xlsx``. Each of the four tabs lists the
    FBDI worksheet order and the CSV order side by side, and on all four they are
    identical — so ONE list is correct for both formats. A missing or unreadable file
    disables the reorder rather than raising.
    """
    global _bom_cache
    if _bom_cache is None:
        try:
            doc = json.loads(_BOM_FILE.read_text(encoding="utf-8"))
            _bom_cache = {
                "order": {norm_hdr(k): v for k, v in (doc.get("order") or {}).items()},
                "csv": {norm_hdr(k): v for k, v in (doc.get("csv_file_names") or {}).items()},
                "with_end": bool(doc.get("append_end_column", False)),
            }
        except Exception:  # noqa: BLE001 — a missing file just disables the reorder
            _bom_cache = {"order": {}, "csv": {}, "with_end": False}
    return _bom_cache


def supplier_col_order() -> dict:
    """{normalized interface sheet name -> ordered list of CSV headers}."""
    global _order_cache
    if _order_cache is None:
        try:
            doc = json.loads(_ORDER_FILE.read_text(encoding="utf-8"))
            _order_cache = doc.get("order", doc) or {}
        except Exception:  # noqa: BLE001 — missing/invalid file just disables reorder
            _order_cache = {}
    return _order_cache


def supplier_file_names() -> dict:
    global _names_cache
    if _names_cache is None:
        try:
            _names_cache = json.loads(_NAMES_FILE.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            _names_cache = {}
    return _names_cache


# --- public facades (unchanged signatures) → pure domain policy ---------------

def customer_load_sequence() -> list:
    """The 15 Customer interfaces in the order Oracle loads them — parents first."""
    return _L.customer_load_sequence(customer_layout())


def customer_sheet_spec(sheet_name: str) -> dict | None:
    """The layout entry for one Customer interface sheet, or None."""
    return _L.customer_sheet_spec(customer_layout(), sheet_name)


def customer_in_load_scope(sheet_name: str) -> bool:
    """Is this interface one of the 15 NextPower actually loads? Unknown sheet = IN."""
    return _L.customer_in_load_scope(customer_layout(), sheet_name)


def customer_csv_name_for(sheet_name: str) -> str | None:
    """Oracle's CSV base-name for a Customer interface — HzImpPartiesT and friends."""
    return _L.customer_csv_name_for(customer_layout(), sheet_name)


def apply_customer_layout(sdf: "pd.DataFrame", sheet_name: str, is_customer: bool,
                          for_csv: bool = True, with_end: bool | None = None) -> "pd.DataFrame":
    """Reorder one Customer interface sheet to Oracle's own column sequence.

    ``for_csv`` picks the CSV order the loader reads vs the worksheet order a human
    sees (they differ on three of fifteen interfaces). ``with_end`` appends the END
    terminator, defaulting to ``for_csv``.
    """
    return _L.apply_customer_layout(sdf, sheet_name, is_customer, customer_layout(),
                                    for_csv=for_csv, with_end_col=with_end)


def bom_col_order() -> dict:
    """{normalised interface sheet name -> ordered list of headers}."""
    return bom_layout()["order"]


def bom_sheet_order(sheet_name: str) -> list | None:
    """The column list for one BOM interface, or None if the spec does not name it."""
    return _L.bom_sheet_order(bom_layout(), sheet_name)


def is_bom_sheet(sheet_name: str) -> bool:
    """Is this one of the four interfaces the BOM spec names? The reliable BOM signal."""
    return _L.is_bom_sheet(bom_layout(), sheet_name)


def bom_appends_end() -> bool:
    """Does the BOM package write the END record terminator? The spec says no."""
    return _L.bom_appends_end(bom_layout())


def bom_csv_name_for(sheet_name: str) -> str | None:
    """Oracle's CSV file name for a BOM interface — ``EgpStructuresInterface.csv``."""
    return _L.bom_csv_name_for(bom_layout(), sheet_name)


def apply_bom_layout(sdf: "pd.DataFrame", sheet_name: str, is_bom: bool,
                     with_end: bool | None = None) -> "pd.DataFrame":
    """Reorder one BOM interface sheet to the Oracle column sequence. ``with_end``
    defaults to what the spec says (False)."""
    return _L.apply_bom_layout(sdf, sheet_name, is_bom, bom_layout(), with_end_col=with_end)


def zip_name_for(primary_sheet_name: str) -> str | None:
    """Oracle zip base-name for a supplier entity, keyed by its primary sheet."""
    return _L.zip_name_for(supplier_file_names(), primary_sheet_name)


def csv_name_for(sheet_name: str) -> str:
    """Oracle CSV base-name for an interface sheet (falls back to a safe sheet name)."""
    return _L.csv_name_for(supplier_file_names(), sheet_name)


def apply_supplier_layout(sdf: "pd.DataFrame", sheet_name: str, is_supplier: bool,
                          with_end: bool = True, batch_id_first: bool = False) -> "pd.DataFrame":
    """Supplier only: reorder a primary interface sheet's columns to the analyst tab
    sequence, blank the Oracle "…-Obsoleted" columns, optionally move Batch ID to
    column 1, and append the END terminator. No-op for non-supplier."""
    return _L.apply_supplier_layout(sdf, sheet_name, is_supplier, supplier_col_order(),
                                    with_end_col=with_end, batch_id_first=batch_id_first)
