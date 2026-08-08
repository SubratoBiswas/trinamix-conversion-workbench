"""Supplier FBDI output layout + file-naming (pure, dependency-light).

Extracted from ``output_service`` so the analyst-driven column reorder, the END
record-terminator, and the Oracle zip/CSV file names can be UNIT TESTED without
importing the Beanie/Mongo/pydantic stack (which isn't importable in CI/sandbox).

Driven by bundled data files, one per object, because the three objects genuinely
disagree about the rules and a shared spec would have to lie about one of them:
  data/supplier_fbdi_column_order.json  — per-interface CSV column sequence
                                          (ConvNXP_All.xlsm "Supplier Import" tab)
  data/supplier_fbdi_file_names.json    — zip name + per-sheet CSV names (Tejaswi)
  data/customer_fbdi_column_order.json  — worksheet order AND CSV order, which
                                          differ on 3 of 15 interfaces; END appended
  data/bom_fbdi_column_order.json       — ONE order serving both formats on all 4
                                          interfaces; NO END terminator

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
_BOM_FILE = _DATA / "bom_fbdi_column_order.json"

_order_cache: dict | None = None
_names_cache: dict | None = None
_customer_cache: dict | None = None
_bom_cache: dict | None = None


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
            _customer_cache["__scope__"] = doc.get("load_scope") or doc.get("load_sequence") or []
            _customer_cache["__excluded__"] = doc.get("excluded_interfaces") or []
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


def customer_in_load_scope(sheet_name: str) -> bool:
    """Is this interface one of the 15 NextPower actually loads?

    Oracle's Customer template ships 19 interface tables; the client loads 15.
    Tejaswini, 31-Jul: "they are working on 15 files only, mentioned in the sheet,
    so we do not have to generate all of the 19 FBDI output files."

    The four left out — HZ_IMP_ACCOUNTRELS, HZ_IMP_CLASSIFICS_T,
    RA_CUST_PAY_METHOD_INT_ALL and RA_CUSTOMER_BANKS_INT_ALL — are the SAME four the
    analyst had been naming one at a time as per-field exclusions ("in all sheets
    except HZ_IMP_CLASSIFICS_T", "…except RA_CUST_PAY_METHOD_INT_ALL,
    RA_CUSTOMER_BANKS_INT_ALL, HZ_IMP_ACCOUNTRELS"). Those exclusions were an analyst
    working around a file set that was too big. Stated once, as scope, they stop
    having to be restated per field.

    Scope is DATA (``load_scope``), not a constant here: putting an interface back is
    a one-line edit to the spec file, which matters because the same issue list also
    asks about a default on HZ_IMP_ACCOUNTRELS.

    An unknown sheet is IN scope. A spec that cannot be read must never silently
    shrink the deliverable.
    """
    scope = customer_layout().get("__scope__") or []
    if not scope:
        return True
    key = norm_hdr(safe_sheet_name(sheet_name))
    known = {norm_hdr(x) for x in customer_layout().get("__sequence__") or []}
    excluded = {norm_hdr(x.get("sheet")) for x in (customer_layout().get("__excluded__") or [])
                if isinstance(x, dict) and x.get("sheet")}
    if key in excluded:
        return False
    if key in {norm_hdr(x) for x in scope}:
        return True
    return key not in known


def norm_hdr(s: Any) -> str:
    """Normalise a header for matching: alphanumerics only, lowercased. Reconciles
    cosmetic differences ('Supplier Name*' vs 'Supplier Name *')."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def safe_sheet_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "sheet"


def _reorder_to(sdf: "pd.DataFrame", order: list) -> "pd.DataFrame":
    """Reindex a frame's columns to ``order``, matched on the normalised header.

    The one piece of logic all three object layouts share, kept in one place so the
    third caller cannot quietly drift from the first two. Columns the spec does not
    name are kept and appended in their existing relative order: a template that has
    gained a column still round-trips, because dropping one silently would be a
    worse failure than the misordering this exists to fix.

    A NAME THE SPEC LISTS TWICE GETS TWO POSITIONS.
    Oracle's ``HZ_IMP_LOCATIONS_T`` prints "Address Line 1" at columns 6 AND 22 —
    its own template does, row 4, and the spec transcribes it faithfully at 87
    wide. The frame reaching this function is built through ``_dedup``, so it
    carries the name ONCE. Matching by name and skipping repeats therefore emitted
    86 columns, and every one of the 66 fields after position 21 shipped one place
    to the left — same plausible-looking file, every value in the neighbouring
    field. Locations is one of the fifteen interfaces NextPower loads.

    So a spec name with no frame column left to give it gets an EMPTY column at
    that position rather than nothing. An empty cell is what an unmapped column
    already ships; a missing one shifts the whole tail. Locations is the only
    interface in any of the three specs where this fires — supplier's five and
    BOM's four have no repeated name — so everywhere else this is a no-op.
    """
    import pandas as pd  # local: the module is imported by spec-only tests too

    available: dict = {}
    for c in sdf.columns:
        available.setdefault(norm_hdr(c), []).append(c)
    taken: set = set()
    pieces: list = []
    labels: list = []
    for h in order:
        pool = available.get(norm_hdr(h)) or []
        col = next((c for c in pool if c not in taken), None)
        if col is not None:
            taken.add(col)
            pieces.append(sdf[col])
            labels.append(col)
        else:
            pieces.append(pd.Series([""] * len(sdf), index=sdf.index, dtype=object))
            labels.append(h)
    for c in sdf.columns:
        if c not in taken:
            taken.add(c)
            pieces.append(sdf[c])
            labels.append(c)
    out = pd.concat(pieces, axis=1) if pieces else sdf.copy()
    out.columns = labels
    return out


def bom_layout() -> dict:
    """The BOM Import layout — column order, Oracle CSV names, and the END flag.

    From ``BOM_Import_FBDI_Sequence_Mapping 1.xlsx``, handed over 04-Aug-2026. Each
    of the four tabs lists the FBDI worksheet order and the CSV order side by side,
    and on all four they are identical — compared position by position rather than
    assumed — so ONE list is correct for both formats. That is the opposite of
    Customer, where three of fifteen interfaces disagree, and the disagreement is
    the whole reason each object carries its own spec instead of sharing one.

    A missing or unreadable file disables the reorder rather than raising, matching
    the other two vocabularies: a spec that cannot be read must never take the
    deliverable down with it.
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


def bom_col_order() -> dict:
    """{normalised interface sheet name -> ordered list of headers}."""
    return bom_layout()["order"]


def bom_sheet_order(sheet_name: str) -> list | None:
    """The column list for one BOM interface, or None if the spec does not name it."""
    o = bom_col_order().get(norm_hdr(safe_sheet_name(sheet_name)))
    return list(o) if isinstance(o, list) and o else None


def is_bom_sheet(sheet_name: str) -> bool:
    """Is this one of the four interfaces the BOM spec names?

    The reliable BOM signal. An object NAME cannot be trusted on its own here —
    'bom' is a substring of ordinary words, and the same object travels under BOM,
    Bill of Materials and Item Structure depending on who created it.
    """
    return bom_sheet_order(sheet_name) is not None


def bom_appends_end() -> bool:
    """Does the BOM package write the END record terminator? The spec says no.

    Kept as DATA rather than a constant because this is the single value most
    likely to be got wrong by inheritance: the supplier package has always appended
    END and BOM reuses that machinery. The analyst checked all four tabs, none
    carries an END column, and a terminator here would hand Oracle an extra field
    it does not expect on these interfaces.
    """
    return bool(bom_layout()["with_end"])


def bom_csv_name_for(sheet_name: str) -> str | None:
    """Oracle's CSV file name for a BOM interface — ``EgpStructuresInterface.csv``.

    From the workbook's Summary tab. Oracle matches the file inside the zip by
    name, so the spec's spelling is used verbatim and nothing is prefixed onto it.
    """
    return bom_layout()["csv"].get(norm_hdr(safe_sheet_name(sheet_name))) or None


def apply_bom_layout(sdf: "pd.DataFrame", sheet_name: str, is_bom: bool,
                     with_end: bool | None = None) -> "pd.DataFrame":
    """Reorder one BOM interface sheet to the Oracle column sequence.

    No ``for_csv`` switch, deliberately: the FBDI worksheet order and the CSV order
    are the same list on all four interfaces, so offering a choice would invent a
    distinction the source workbook does not make.

    ``with_end`` defaults to what the spec file says, which is ``False``. The
    supplier package appends an END record terminator to every row and this module
    is where that behaviour lives, so the default is the guard: a caller that
    forgets the flag still gets BOM's answer rather than supplier's.

    These are HEADERLESS CSVs — column POSITION is the only thing carrying meaning,
    so a list that is right about the names and wrong about the order loads
    silently into the wrong fields.
    """
    if not is_bom:
        return sdf
    if with_end is None:
        with_end = bom_appends_end()
    order = bom_sheet_order(sheet_name)
    if not order:
        return _with_end(sdf) if with_end else sdf
    out = _reorder_to(sdf, order)
    return _with_end(out) if with_end else out


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
                          for_csv: bool = True, with_end: bool | None = None) -> "pd.DataFrame":
    """Reorder one Customer interface sheet to Oracle's own column sequence.

    ``for_csv`` picks WHICH sequence: the CSV order the loader reads, or the
    worksheet order a human sees in the Oracle template. They are not the same on
    three of the fifteen interfaces, and shipping one where the other is expected
    is a silent corruption — same column count, every value shifted.

    ``with_end`` appends the ``END`` record terminator, which is what V2_2 of the
    sequence workbook added: every one of the 15 CSV columns now ends with an
    explicit END row. The supplier package has always written it; the customer
    package never did, so Customer CSVs shipped without a terminator. Defaults to
    ``for_csv`` — END belongs in the CSV the loader reads and NOT in the xlsx a
    human opens, because the Oracle template has no END column.

    Columns the spec does not list are kept and appended, so nothing is ever
    dropped; a template that has gained a column still round-trips.
    """
    if not is_customer:
        return sdf
    spec = customer_sheet_spec(sheet_name)
    if not spec:
        return sdf
    if with_end is None:
        with_end = bool(for_csv)
    order = spec.get("csv_order" if for_csv else "fbdi_order") or []
    if not order:
        return sdf if not with_end else _with_end(sdf)
    out = _reorder_to(sdf, order)
    return _with_end(out) if with_end else out


def _with_end(sdf: "pd.DataFrame") -> "pd.DataFrame":
    """Append the FBDI record terminator as a trailing 'END' column.

    Same shape the supplier package uses. Idempotent: a frame that already carries
    an END column is returned unchanged, so a double-applied layout cannot produce
    two terminators.
    """
    if any(str(c).strip().upper() == "END" for c in sdf.columns):
        return sdf
    out = sdf.copy()
    out["END"] = "END"
    return out


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
    sdf = _reorder_to(sdf, order) if order else sdf.copy()
    # PROC-07: Oracle keeps "…-Obsoleted" columns in the supplier templates for
    # backward compatibility; populating them is at best ignored, at worst rejected.
    # The strategy default ("blank every column containing Obsolete") wasn't firing on
    # the NetSuite mapping (it shipped Tax Registration Number-Obsoleted on 966 rows),
    # so enforce it deterministically here where columns carry their Oracle header
    # names. Fans out to every supplier project / source on regenerate.
    for _c in list(sdf.columns):
        if "obsolete" in norm_hdr(_c):
            sdf[_c] = ""
    if batch_id_first:
        # Header spelling varies across the templates (Batch_id / Batch ID /
        # BatchId), so match on the normalised header rather than an exact string.
        bcol = next((c for c in sdf.columns if norm_hdr(c) == "batchid"), None)
        if bcol is not None and list(sdf.columns).index(bcol) != 0:
            sdf = sdf[[bcol] + [c for c in sdf.columns if c != bcol]].copy()
    if with_end:
        sdf["END"] = "END"
    return sdf
