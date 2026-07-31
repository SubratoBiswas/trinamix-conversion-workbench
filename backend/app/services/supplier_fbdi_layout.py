"""Supplier FBDI output layout + file-naming (pure, dependency-light).

Extracted from ``output_service`` so the analyst-driven column reorder, the END
record-terminator, and the Oracle zip/CSV file names can be UNIT TESTED without
importing the Beanie/Mongo/pydantic stack (which isn't importable in CI/sandbox).

Driven by two bundled data files:
  data/supplier_fbdi_column_order.json  — per-interface CSV column sequence
                                          (ConvNXP_All.xlsm "Supplier Import" tab)
  data/supplier_fbdi_file_names.json    — zip name + per-sheet CSV names (Tejaswi)

The generator calls these; the reorder is applied to a supplier interface sheet's
frame, END is appended as the last column on every row, and (in ``output_service``)
the CSVs are written HEADERLESS and packaged in the correctly-named zip.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

_DATA = Path(__file__).resolve().parent.parent / "data"
_ORDER_FILE = _DATA / "supplier_fbdi_column_order.json"
_NAMES_FILE = _DATA / "supplier_fbdi_file_names.json"
_CUSTOMER_FILE = _DATA / "customer_fbdi_column_order.json"

_order_cache: dict | None = None
_names_cache: dict | None = None
_customer_cache: dict | None = None


def customer_layout() -> dict:
    """The Customer Import layout, keyed by normalised interface-table name.

    Oracle's Customer Import workbook and the CSVs its macro generates DO NOT
    share a column order, and the mismatch is silent: the column COUNT is
    identical, so a file built in worksheet order looks perfectly well formed and
    loads every value one or more columns out of place. Three of the fifteen
    interfaces are affected — HZ_IMP_ACCTSITES_T and HZ_IMP_ACCTSITEUSES_T move
    Account Number and Party Site Number to the end, and
    RA_CUSTOMER_PROFILES_INT_ALL moves six columns.

    The CSVs are written HEADERLESS, so position is the only thing carrying
    meaning. This is exactly the class of defect that cannot be caught by reading
    the output — which is why the order is data, and tested.
    """
    global _customer_cache
    if _customer_cache is None:
        try:
            doc = json.loads(_CUSTOMER_FILE.read_text(encoding="utf-8"))
            _customer_cache = {norm_hdr(k): v for k, v in (doc.get("sheets") or {}).items()}
            _customer_cache["__sequence__"] = doc.get("load_sequence") or []
        except Exception:  # noqa: BLE001 — a missing file just disables the reorder
            _customer_cache = {}
    return _customer_cache


def customer_load_sequence() -> list:
    """The 15 Customer interfaces in the order Oracle loads them — parents first.

    Parties, then Party Sites, then the account layers, then the contact layers.
    A child row whose parent has not loaded is rejected, so the ORDER is part of
    the deliverable, not a presentation choice.
    """
    return list(customer_layout().get("__sequence__") or [])


def customer_sheet_spec(sheet_name: str) -> dict | None:
    """The layout entry for one Customer interface sheet, or None."""
    e = customer_layout().get(norm_hdr(safe_sheet_name(sheet_name)))
    return e if isinstance(e, dict) else None


def norm_hdr(s: Any) -> str:
    """Normalise a header for matching: alphanumerics only, lowercased. Reconciles
    cosmetic differences ('Supplier Name*' vs 'Supplier Name *')."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def safe_sheet_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "sheet"


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


def zip_name_for(primary_sheet_name: str) -> str | None:
    """Oracle zip base-name for a supplier entity, keyed by its primary sheet."""
    return supplier_file_names().get("zip_by_primary_sheet", {}).get(
        norm_hdr(safe_sheet_name(primary_sheet_name)))


def csv_name_for(sheet_name: str) -> str:
    """Oracle CSV base-name for an interface sheet (falls back to a safe sheet name)."""
    return (supplier_file_names().get("csv_by_sheet", {}).get(
        norm_hdr(safe_sheet_name(sheet_name))) or safe_sheet_name(sheet_name))


def apply_customer_layout(sdf: "pd.DataFrame", sheet_name: str, is_customer: bool,
                          for_csv: bool = True) -> "pd.DataFrame":
    """Reorder one Customer interface sheet to Oracle's own column sequence.

    ``for_csv`` picks WHICH sequence: the CSV order the loader reads, or the
    worksheet order a human sees in the Oracle template. They are not the same on
    three of the fifteen interfaces, and shipping one where the other is expected
    is a silent corruption — same column count, every value shifted.

    Columns the spec does not list are kept and appended, so nothing is ever
    dropped; a template that has gained a column still round-trips.
    """
    if not is_customer:
        return sdf
    spec = customer_sheet_spec(sheet_name)
    if not spec:
        return sdf
    order = spec.get("csv_order" if for_csv else "fbdi_order") or []
    if not order:
        return sdf
    by_norm: dict = {}
    for c in sdf.columns:
        by_norm.setdefault(norm_hdr(c), c)
    seen: set = set()
    ordered: list = []
    for h in order:
        c = by_norm.get(norm_hdr(h))
        if c is not None and c not in seen:
            ordered.append(c)
            seen.add(c)
    for c in sdf.columns:
        if c not in seen:
            ordered.append(c)
            seen.add(c)
    return sdf[ordered].copy()


def customer_csv_name_for(sheet_name: str) -> str | None:
    """Oracle's CSV base-name for a Customer interface — HzImpPartiesT and friends.

    Oracle matches the file inside the zip by NAME, so a correctly ordered CSV
    called HZ_IMP_PARTIES_T.csv is simply not read.
    """
    spec = customer_sheet_spec(sheet_name)
    return (spec or {}).get("csv")


def apply_supplier_layout(sdf: "pd.DataFrame", sheet_name: str, is_supplier: bool,
                          with_end: bool = True, batch_id_first: bool = False) -> "pd.DataFrame":
    """Supplier only: reorder a primary interface sheet's columns to the analyst tab
    sequence (matched by normalized header; columns the tab doesn't list are kept and
    appended after, so nothing is dropped), then append an ``END`` record-terminator
    column (literal 'END' on the header + every data row). No-op for non-supplier.

    ``with_end=False`` keeps the reorder but omits the END column. END is a CSV
    record terminator for the FBDI loader; it does NOT belong in an Excel workbook
    a human opens, and the real Oracle template has no END column — so the xlsx
    output must not carry it.

    ``batch_id_first=True`` moves the Batch ID column to position 1. The two
    layouts genuinely differ: in the FBDI workbook Batch ID is the FIRST column,
    while the generated CSV carries it near the END (which is the order the
    column-order file encodes). Shipping the CSV order in the FBDI made every
    later column look shifted during review — a Batch ID value showed up under
    Registry ID and was reported as a mapping bug when nothing was mis-mapped."""
    if not is_supplier:
        return sdf
    order = supplier_col_order().get(norm_hdr(safe_sheet_name(sheet_name)))
    if order:
        by_norm: dict = {}
        for c in sdf.columns:
            by_norm.setdefault(norm_hdr(c), c)
        seen: set = set()
        ordered: list = []
        for h in order:
            c = by_norm.get(norm_hdr(h))
            if c is not None and c not in seen:
                ordered.append(c)
                seen.add(c)
        for c in sdf.columns:  # keep any template column the tab didn't list
            if c not in seen:
                ordered.append(c)
                seen.add(c)
        sdf = sdf[ordered].copy()
    else:
        sdf = sdf.copy()
    if batch_id_first:
        # Header spelling varies across the templates (Batch_id / Batch ID /
        # BatchId), so match on the normalised header rather than an exact string.
        bcol = next((c for c in sdf.columns if norm_hdr(c) == "batchid"), None)
        if bcol is not None and list(sdf.columns).index(bcol) != 0:
            sdf = sdf[[bcol] + [c for c in sdf.columns if c != bcol]].copy()
    if with_end:
        sdf["END"] = "END"
    return sdf
