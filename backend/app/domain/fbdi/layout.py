"""FBDI output layout + file-naming — the PURE decision layer (Phase 3, slice 2).

The analyst-driven column reorder, the END record-terminator, the Customer load
scope, and the Oracle zip/CSV file names are all pure functions of a frame and a
spec. They were split out of ``output_service`` once already so they could be unit
tested without the Beanie/Mongo stack; this module takes them the rest of the way
into the domain, where the invariant is *no I/O at all*.

Every function here takes the loaded spec (a plain dict, already parsed and
normalised by the store) as an argument. The store — ``app.services.supplier_fbdi_layout``
— reads the four JSON files, caches them, and calls these functions. Nothing here
touches the disk, so the whole layout vocabulary is testable with a hand-built dict.

Spec shapes (built by the store):
  order_spec    : {norm(sheet): [header, ...]}                       supplier CSV order
  names_spec    : {"zip_by_primary_sheet": {...}, "csv_by_sheet": {...}}
  customer_spec : {norm(sheet): {csv_order, fbdi_order, csv}, __sequence__,
                   __scope__, __excluded__}
  bom_spec      : {"order": {norm(sheet): [...]}, "csv": {norm(sheet): name},
                   "with_end": bool}
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd


def norm_hdr(s: Any) -> str:
    """Normalise a header for matching: alphanumerics only, lowercased. Reconciles
    cosmetic differences ('Supplier Name*' vs 'Supplier Name *')."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def safe_sheet_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "sheet"


def reorder_to(sdf: "pd.DataFrame", order: list) -> "pd.DataFrame":
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


def with_end(sdf: "pd.DataFrame") -> "pd.DataFrame":
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


# --- Customer -----------------------------------------------------------------

def customer_load_sequence(customer_spec: dict | None) -> list:
    """The 15 Customer interfaces in the order Oracle loads them — parents first.

    Parties, then Party Sites, then the account layers, then the contact layers.
    A child row whose parent has not loaded is rejected, so the ORDER is part of
    the deliverable, not a presentation choice.
    """
    return list((customer_spec or {}).get("__sequence__") or [])


def customer_sheet_spec(customer_spec: dict | None, sheet_name: str) -> dict | None:
    """The layout entry for one Customer interface sheet, or None."""
    e = (customer_spec or {}).get(norm_hdr(safe_sheet_name(sheet_name)))
    return e if isinstance(e, dict) else None


def customer_in_load_scope(customer_spec: dict | None, sheet_name: str) -> bool:
    """Is this interface one of the 15 NextPower actually loads?

    Oracle's Customer template ships 19 interface tables; the client loads 15.
    An unknown sheet is IN scope. A spec that cannot be read must never silently
    shrink the deliverable.
    """
    customer_spec = customer_spec or {}
    scope = customer_spec.get("__scope__") or []
    if not scope:
        return True
    key = norm_hdr(safe_sheet_name(sheet_name))
    known = {norm_hdr(x) for x in customer_spec.get("__sequence__") or []}
    excluded = {norm_hdr(x.get("sheet")) for x in (customer_spec.get("__excluded__") or [])
                if isinstance(x, dict) and x.get("sheet")}
    if key in excluded:
        return False
    if key in {norm_hdr(x) for x in scope}:
        return True
    return key not in known


def apply_customer_layout(sdf: "pd.DataFrame", sheet_name: str, is_customer: bool,
                          customer_spec: dict | None, for_csv: bool = True,
                          with_end_col: bool | None = None) -> "pd.DataFrame":
    """Reorder one Customer interface sheet to Oracle's own column sequence.

    ``for_csv`` picks WHICH sequence: the CSV order the loader reads, or the
    worksheet order a human sees in the Oracle template. They are not the same on
    three of the fifteen interfaces, and shipping one where the other is expected
    is a silent corruption — same column count, every value shifted.

    ``with_end_col`` appends the ``END`` record terminator. Defaults to ``for_csv`` —
    END belongs in the CSV the loader reads and NOT in the xlsx a human opens.

    Columns the spec does not list are kept and appended, so nothing is ever
    dropped; a template that has gained a column still round-trips.
    """
    if not is_customer:
        return sdf
    spec = customer_sheet_spec(customer_spec, sheet_name)
    if not spec:
        return sdf
    if with_end_col is None:
        with_end_col = bool(for_csv)
    order = spec.get("csv_order" if for_csv else "fbdi_order") or []
    if not order:
        return sdf if not with_end_col else with_end(sdf)
    out = reorder_to(sdf, order)
    return with_end(out) if with_end_col else out


def customer_csv_name_for(customer_spec: dict | None, sheet_name: str) -> str | None:
    """Oracle's CSV base-name for a Customer interface — HzImpPartiesT and friends.

    Oracle matches the file inside the zip by NAME, so a correctly ordered CSV
    called HZ_IMP_PARTIES_T.csv is simply not read.
    """
    spec = customer_sheet_spec(customer_spec, sheet_name)
    return (spec or {}).get("csv")


# --- BOM ----------------------------------------------------------------------

def bom_sheet_order(bom_spec: dict | None, sheet_name: str) -> list | None:
    """The column list for one BOM interface, or None if the spec does not name it."""
    o = ((bom_spec or {}).get("order") or {}).get(norm_hdr(safe_sheet_name(sheet_name)))
    return list(o) if isinstance(o, list) and o else None


def is_bom_sheet(bom_spec: dict | None, sheet_name: str) -> bool:
    """Is this one of the four interfaces the BOM spec names?

    The reliable BOM signal. An object NAME cannot be trusted on its own here —
    'bom' is a substring of ordinary words, and the same object travels under BOM,
    Bill of Materials and Item Structure depending on who created it.
    """
    return bom_sheet_order(bom_spec, sheet_name) is not None


def bom_appends_end(bom_spec: dict | None) -> bool:
    """Does the BOM package write the END record terminator? The spec says no."""
    return bool((bom_spec or {}).get("with_end"))


def bom_csv_name_for(bom_spec: dict | None, sheet_name: str) -> str | None:
    """Oracle's CSV file name for a BOM interface — ``EgpStructuresInterface.csv``.

    From the workbook's Summary tab. Oracle matches the file inside the zip by
    name, so the spec's spelling is used verbatim and nothing is prefixed onto it.
    """
    return ((bom_spec or {}).get("csv") or {}).get(norm_hdr(safe_sheet_name(sheet_name))) or None


def apply_bom_layout(sdf: "pd.DataFrame", sheet_name: str, is_bom: bool,
                     bom_spec: dict | None, with_end_col: bool | None = None) -> "pd.DataFrame":
    """Reorder one BOM interface sheet to the Oracle column sequence.

    No ``for_csv`` switch, deliberately: the FBDI worksheet order and the CSV order
    are the same list on all four interfaces, so offering a choice would invent a
    distinction the source workbook does not make.

    ``with_end_col`` defaults to what the spec file says, which is ``False``. The
    supplier package appends an END record terminator to every row and this module
    is where that behaviour lives, so the default is the guard: a caller that
    forgets the flag still gets BOM's answer rather than supplier's.

    These are HEADERLESS CSVs — column POSITION is the only thing carrying meaning,
    so a list that is right about the names and wrong about the order loads
    silently into the wrong fields.
    """
    if not is_bom:
        return sdf
    if with_end_col is None:
        with_end_col = bom_appends_end(bom_spec)
    order = bom_sheet_order(bom_spec, sheet_name)
    if not order:
        return with_end(sdf) if with_end_col else sdf
    out = reorder_to(sdf, order)
    return with_end(out) if with_end_col else out


# --- Supplier -----------------------------------------------------------------

def zip_name_for(names_spec: dict | None, primary_sheet_name: str) -> str | None:
    """Oracle zip base-name for a supplier entity, keyed by its primary sheet."""
    return (names_spec or {}).get("zip_by_primary_sheet", {}).get(
        norm_hdr(safe_sheet_name(primary_sheet_name)))


def csv_name_for(names_spec: dict | None, sheet_name: str) -> str:
    """Oracle CSV base-name for an interface sheet (falls back to a safe sheet name)."""
    return ((names_spec or {}).get("csv_by_sheet", {}).get(
        norm_hdr(safe_sheet_name(sheet_name))) or safe_sheet_name(sheet_name))


def apply_supplier_layout(sdf: "pd.DataFrame", sheet_name: str, is_supplier: bool,
                          order_spec: dict | None, with_end_col: bool = True,
                          batch_id_first: bool = False) -> "pd.DataFrame":
    """Supplier only: reorder a primary interface sheet's columns to the analyst tab
    sequence (matched by normalized header; columns the tab doesn't list are kept and
    appended after, so nothing is dropped), then append an ``END`` record-terminator
    column (literal 'END' on the header + every data row). No-op for non-supplier.

    ``with_end_col=False`` keeps the reorder but omits the END column. END is a CSV
    record terminator for the FBDI loader; it does NOT belong in an Excel workbook
    a human opens, and the real Oracle template has no END column.

    ``batch_id_first=True`` moves the Batch ID column to position 1. The two
    layouts genuinely differ: in the FBDI workbook Batch ID is the FIRST column,
    while the generated CSV carries it near the END (which is the order the
    column-order file encodes).
    """
    if not is_supplier:
        return sdf
    order = (order_spec or {}).get(norm_hdr(safe_sheet_name(sheet_name)))
    sdf = reorder_to(sdf, order) if order else sdf.copy()
    # PROC-07: Oracle keeps "…-Obsoleted" columns in the supplier templates for
    # backward compatibility; populating them is at best ignored, at worst rejected.
    for _c in list(sdf.columns):
        if "obsolete" in norm_hdr(_c):
            sdf[_c] = ""
    if batch_id_first:
        # Header spelling varies across the templates (Batch_id / Batch ID /
        # BatchId), so match on the normalised header rather than an exact string.
        bcol = next((c for c in sdf.columns if norm_hdr(c) == "batchid"), None)
        if bcol is not None and list(sdf.columns).index(bcol) != 0:
            sdf = sdf[[bcol] + [c for c in sdf.columns if c != bcol]].copy()
    if with_end_col:
        sdf["END"] = "END"
    return sdf
