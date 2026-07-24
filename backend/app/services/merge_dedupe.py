"""Multi-source converge + de-duplicate (pure, dependency-light).

When a module/target object is fed by several source files, each source is
converted individually and the converted frames are merged here into one output.
De-duplication honours SOURCE PRIORITY: frames are concatenated in priority order
(first = highest) and the first occurrence wins.

Kept free of the Beanie/Mongo stack so the merge/dedup rules can be unit tested.
The natural-key registry (REFERENCE_KEY_FIELDS) is passed in by the caller.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def key_col_for(df0: pd.DataFrame, target_object: Optional[str], key_registry: dict) -> Optional[str]:
    """The business-key column to de-dupe a merged frame on, or None.

    Resolves the object family (Supplier/Item/Customer/…) to its key field(s) and
    returns the matching output column ONLY when that key is ~unique within a single
    source (i.e. a master/entity record). Child interfaces (many rows per entity,
    e.g. Supplier Site) return None so they get exact-row de-dup instead of being
    wrongly collapsed to one row per entity."""
    o = (target_object or "").strip().lower()
    keys: list = []
    for fam, flds in (key_registry or {}).items():
        if fam.lower() in o or o in fam.lower():
            keys = flds
            break
    if not keys:
        return None
    norm = {_norm(c): c for c in df0.columns}
    for k in keys:
        c = norm.get(_norm(k))
        if c is None:
            continue
        s = df0[c].astype(str).str.strip()
        nb = int((s != "").sum())
        if nb > 0 and s[s != ""].nunique() >= 0.9 * nb:
            return c
    return None


def merge_dedupe(frames: list, target_object: Optional[str], key_registry: dict,
                 survivorship: bool = True) -> pd.DataFrame:
    """Concatenate per-source frames in PRIORITY order, drop exact full-row
    duplicates (same record in >1 source), then — if a master business key is
    present — collapse to one row per key. With ``survivorship`` (default) the kept
    row is a GOLDEN RECORD: each field takes the first NON-BLANK value across the
    sources in priority order, so a blank in the top source is filled from a lower
    one. Without survivorship it's plain keep-first. Blank-key rows are all kept.
    A single frame is returned unchanged (aside from index reset)."""
    frames = [f for f in frames if f is not None]
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        return frames[0].reset_index(drop=True)
    key_col = key_col_for(frames[0], target_object, key_registry)
    merged = pd.concat(frames, ignore_index=True).drop_duplicates(keep="first")
    if key_col is not None and key_col in merged.columns:
        s = merged[key_col].astype(str).str.strip()
        blank = s == ""
        keyed = merged[~blank]
        if survivorship:
            keyed = _survive(keyed, key_col)
        else:
            keyed = keyed.drop_duplicates(subset=[key_col], keep="first")
        merged = pd.concat([keyed, merged[blank]]).sort_index()
    return merged.reset_index(drop=True)


def _survive(df: pd.DataFrame, key_col: str) -> pd.DataFrame:
    """Golden-record survivorship: one row per key where each field is the first
    NON-BLANK value in priority (row) order. Rows arrive already ordered by source
    priority, so the top source wins per field, back-filled from lower sources.

    VECTORISED: blank/whitespace cells -> NaN, then groupby.first() (which skips
    NaN) gives the first non-blank per column per key in one pass. This is O(1) pandas
    ops instead of a Python callable per column per group — critical for wide objects
    (Item 1,365 cols / Customer 1,254 cols) where the naive agg was the bottleneck."""
    if df.empty:
        return df
    import numpy as np
    # Only string blanks need masking; regex replace is a no-op on numeric columns.
    work = df.replace(r"^\s*$", np.nan, regex=True)
    grouped = work.groupby(key_col, sort=False, as_index=False).first()
    return grouped.fillna("")
