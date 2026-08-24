"""BOM Item Structure — reshape the ONE source extract into the FOUR interfaces.

NEXTPOWER BOM validation feedback (docx, 05-Aug). A flat BOM extract has one row
per parent/child component line, but Oracle's four interfaces each need a
different GRAIN:

  * Structures (EGP_STRUCTURES_INTERFACE): one row per assembly. Unique on
    (Structure Name, Organization Code, Item Name) — the parent item.
  * Components (EGP_COMPONENTS_INTERFACE): one row per component line. Unique on
    (Structure Name, Organization Code, Component Item Name, Structure Item Name).
    Item Sequence must be numeric (10, 20, 30 …).
  * Substitutes (EGP_SUB_COMPS_INTERFACE): only lines that HAVE a substitute item.
    Unique on (Structure Name, Organization Code, Component Item Name, Substitute
    Item Name, Structure Item Name).
  * Reference Designators (EGP_REF_DESGS_INTERFACE): unique on (Structure Name,
    Organization Code, Component Item Name, Reference Designator, Structure Item
    Name).

Before this the generator copied every source row onto every tab — 19,911 lines
became 19,911 Structures rows where there should be a few hundred, no substitute
filter, and Item Sequence left as whatever the extract carried. This reshapes each
finalized frame to the grain its interface expects.

It runs on the FINALISED frame, whose headers are Oracle's field names (Item Name,
Structure Item Name, …), so the column-to-source mapping is not this module's
concern — the analyst's mapping put the parent/child numbers in the right columns
already. This only fixes the row grain, the filter and the sequence.

Every column is matched loosely and every step is defensive: a tab missing a key
column is passed through unchanged rather than dropped, because shipping the rows
un-deduped is recoverable and losing them is not.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").strip().lower())


# The interface each sheet name resolves to, matched on a normalised substring so
# "EGP_STRUCTURES_INTERFACE", "Structures", "BOM Structures" all land the same.
def _kind(sheet_name: str) -> Optional[str]:
    n = _norm(sheet_name)
    if "subcomp" in n or "substitut" in n:
        return "substitutes"
    if "refdesg" in n or "referencedesignator" in n or "refdesig" in n:
        return "reference_designators"
    if "component" in n:
        return "components"
    if "structure" in n:
        return "structures"
    return None


# Dedup keys, in Oracle's field-name spelling. Matched loosely against the frame.
_DEDUP_KEYS = {
    "structures": ["Structure Name", "Organization Code", "Item Name"],
    "components": ["Structure Name", "Organization Code",
                   "Component Item Name", "Structure Item Name"],
    "substitutes": ["Structure Name", "Organization Code", "Component Item Name",
                    "Substitute Item Name", "Structure Item Name"],
    "reference_designators": ["Structure Name", "Organization Code",
                              "Component Item Name", "Reference Designator",
                              "Structure Item Name"],
}

# Mandatory columns per interface, from the validation doc — used for a diagnostic,
# never to drop rows.
MANDATORY = {
    "structures": ["Transaction Type", "Batch Number", "Structure Name",
                   "Organization Code", "Item Name", "Effective Date"],
    "components": ["Transaction Type", "Batch Number", "Structure Name",
                   "Organization Code", "Component Item Name", "Structure Item Name",
                   "Item Sequence", "Quantity"],
    "substitutes": ["Transaction Type", "Batch Number", "Structure Name",
                    "Organization Code", "Component Item Name", "Structure Item Name",
                    "Substitute Item Name", "Substitute Quantity"],
    "reference_designators": ["Transaction Type", "Batch Number", "Structure Name",
                              "Organization Code", "Component Item Name",
                              "Structure Item Name", "Reference Designator"],
}


def _find_col(df: pd.DataFrame, name: str) -> Optional[str]:
    want = _norm(name)
    # Exact normalised match first, then a contains, so "Reference Designator"
    # matches a header "Reference Designators".
    exact = [c for c in df.columns if _norm(c) == want]
    if exact:
        return exact[0]
    near = [c for c in df.columns if want and want in _norm(c)]
    return near[0] if near else None


def _blank(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    return s.eq("") | s.str.lower().isin({"nan", "none", "null", "na", "<na>"})


def _renumber_item_sequence(df: pd.DataFrame) -> pd.DataFrame:
    """Item Sequence numeric, 10/20/30 restarting within EACH parent item.

    NextPower validation (Jithendran, 07-Aug): "The sequence should be generated
    based on each Parent Item, not continuously for the entire file … once the
    Parent Item is populated, group the components under that Parent Item and assign
    Item Sequence values" — A's components 10,20; B's components 10,20.

    So when there is a real per-parent key (Structure Item Name — the parent item
    number, populated by the source-agnostic BOM mapping), the sequence is REGENERATED
    per group in row order: 10, 20, 30 … regardless of whatever the extract carried.
    The old behaviour kept an already-numeric cell, which preserved the source's
    single continuous Find_number/lineNumber across the whole file — the exact bug
    reported. With only the constant Structure Name to group on (no parent key), the
    safer old behaviour is kept: fill blanks, don't renumber, so a genuinely-authored
    sequence is not clobbered when there is nothing to group it by.
    """
    if df.empty:
        return df
    seq_col = _find_col(df, "Item Sequence")
    # A REAL parent key is Structure Item Name (the parent item number). Structure
    # Name alone is the constant "Primary", which is not a per-parent grouping.
    parent_key = _find_col(df, "Structure Item Name")
    org = _find_col(df, "Organization Code")

    # BOM-01 fan-out fix: when Item Sequence was never mapped the column is absent, so
    # the renumber used to no-op and ship a blank sequence. Generate the column here
    # (per-parent 10/20/30) whenever there is a real parent key to group by, so the
    # numbering holds for ANY project without the field being mapped. Named exactly as
    # the interface field so the per-sheet reindex keeps it.
    out = df.copy()
    if seq_col is None:
        if parent_key is None:
            return df                      # nothing to number by — leave as-is
        seq_col = "Item Sequence"
        out[seq_col] = ""

    def _is_num(v: str) -> bool:
        return bool(re.fullmatch(r"\d+", str(v).strip()))

    if parent_key is not None and not out[parent_key].map(_is_blank_scalar).all():
        # Regenerate 10,20,30 within each parent, in the rows' current order.
        new_vals = out[seq_col].astype(str).tolist()
        group_cols = [c for c in (parent_key, org) if c]
        for _, idx in out.groupby(group_cols, sort=False).groups.items():
            nxt = 10
            for i in idx:
                new_vals[out.index.get_loc(i)] = str(nxt)
                nxt += 10
        out[seq_col] = new_vals
        return out

    # Fallback: no parent key to group by — fill blanks per (Structure Name, Org)
    # without renumbering existing values (the previous, conservative behaviour).
    struct = _find_col(df, "Structure Name")
    group_cols = [c for c in (struct, org) if c]
    if not group_cols:
        out["_g"] = 0
        group_cols = ["_g"]
    new_vals = out[seq_col].astype(str).tolist()
    for _, idx in out.groupby(group_cols, sort=False).groups.items():
        used = set()
        for i in idx:
            v = str(out.at[i, seq_col]).strip()
            if _is_num(v):
                used.add(int(v))
        nxt = 10
        for i in idx:
            v = str(out.at[i, seq_col]).strip()
            if _is_num(v):
                continue
            while nxt in used:
                nxt += 10
            new_vals[out.index.get_loc(i)] = str(nxt)
            used.add(nxt)
            nxt += 10
    out[seq_col] = new_vals
    if "_g" in out.columns:
        out = out.drop(columns=["_g"])
    return out


def _is_blank_scalar(v) -> bool:
    s = str(v).strip().lower()
    return s == "" or s in {"nan", "none", "null", "na", "<na>"}


def reshape_for_sheet(df: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    """Reduce a finalised BOM frame to the grain its interface expects.

    Order matters: the substitute filter runs before the dedup so a line with no
    substitute never counts toward a Substitutes key, and the sequence is assigned
    AFTER dedup so the numbers are contiguous on the rows actually shipped.
    """
    kind = _kind(sheet_name)
    if kind is None or df is None or df.empty:
        return df
    out = df

    # Keep only lines that carry the detail this tab exists for.
    #
    # Substitutes: the doc is explicit — "Only include item structures with
    # substitute item is populated." Reference Designators: the doc does not say
    # so in words, but it lists Reference Designator as a MANDATORY column, and a
    # reference-designator line with no designator both fails the load and repeats
    # its parent's component line pointlessly. Same shape as substitutes, so it is
    # filtered the same way. If you actually want every component line on the
    # Reference Designators tab, this is the one line to change.
    _detail = {"substitutes": "Substitute Item Name",
               "reference_designators": "Reference Designator"}.get(kind)
    if _detail:
        col = _find_col(out, _detail)
        if col is not None:
            out = out[~_blank(out[col])].copy()
        if out.empty:
            return out

    # One record per key.
    key_cols = [c for c in (_find_col(out, k) for k in _DEDUP_KEYS[kind]) if c]
    if key_cols:
        out = out.drop_duplicates(subset=key_cols, keep="first").reset_index(drop=True)

    # Components: contiguous numeric Item Sequence per structure.
    if kind == "components":
        out = _renumber_item_sequence(out)

    # Batch ID & Number are MANDATORY on every BOM tab (validation doc). They were
    # shipping blank: the control default never lands here (a customer "keep Batch ID
    # blank" learning sprays onto BOM), and Batch Number has no default at all. Stamp
    # both, engine-owned, so the load-batch grouping is always present. Constant value
    # so all four interfaces share one batch — confirm the number if a convention exists.
    out = _stamp_batch(out)

    return out


_BOM_BATCH_VALUE = "900001"


def _stamp_batch(df: pd.DataFrame) -> pd.DataFrame:
    """Force Batch ID / Batch Number to the load-batch constant on a BOM tab.

    Mandatory per the NEXTPOWER BOM validation doc; engine-owned so a stray
    keep-blank learning or a missing control default cannot leave them empty."""
    if df is None or df.empty:
        return df
    for _name in ("Batch ID", "Batch Number"):
        col = _find_col(df, _name)
        if col is not None:
            df[col] = _BOM_BATCH_VALUE
    return df


def missing_mandatory(df: pd.DataFrame, sheet_name: str) -> list[str]:
    """Mandatory columns that are entirely blank (or absent) on this tab.

    A diagnostic for the validation report — the shape of the doc's "Mandatory
    Col" lists. Never used to drop or block; a load-time reject is the analyst's
    to weigh, but the tool should say which required column is empty rather than
    let it surface only in Oracle.
    """
    kind = _kind(sheet_name)
    if kind is None:
        return []
    out = []
    for name in MANDATORY[kind]:
        col = _find_col(df, name)
        if col is None or df.empty or bool(_blank(df[col]).all()):
            out.append(name)
    return out
