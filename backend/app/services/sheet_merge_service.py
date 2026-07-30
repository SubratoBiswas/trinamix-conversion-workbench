"""One workbook, several sheets: is it several sources, or one input split up?

CW #1. A two-sheet Customer workbook produced two conversions and only the first
sheet's columns became source columns. The report has carried it as "Partial — needs a
product decision" because the two readings need opposite handling and the file cannot
tell you which it is:

  * SEVERAL SOURCES (eBOS suppliers on one tab, NetSuite suppliers on another): one
    dataset per sheet, one conversion each, converged at output by the existing
    survivorship merge. Row grains match; the rows are peers.
  * ONE INPUT SPLIT UP (Customer on one tab, Address on another): one conversion whose
    source columns are the UNION of the sheets. Row grains differ — 5,489 customers
    against 22,505 addresses — so the sheets have to be JOINED, not stacked.

On 30-Jul the analyst settled it: ask, and if it is one input across several sheets,
merge them into a single conversion.

WHY A JOIN KEY IS REQUIRED AND NOT GUESSED
------------------------------------------
Merging two entity sheets means a column-wise join. Concatenating them side by side in
row order would silently attach customer 1's address to customer 2 — a corruption that
looks like clean data and is invisible in the output. So the key is DETECTED, scored,
and if nothing scores well enough the merge is refused with the reason. A refusal costs
a conversation; a bad join costs a cutover.

Pure (pandas + stdlib): no DB, no network, no upload plumbing.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import pandas as pd

_NORM = re.compile(r"[^a-z0-9]+")

MODE_PER_SHEET = "per_sheet"        # several sources -> a conversion each, merged later
MODE_ONE = "one_conversion"         # one input split up -> join into a single dataset


def _n(s: Any) -> str:
    return _NORM.sub("", str(s or "").lower())


def _values(series: pd.Series) -> set:
    out = set()
    for v in series:
        s = str("" if v is None else v).strip()
        if s and s.lower() not in ("nan", "none", "null"):
            out.add(s)
    return out


def detect_join_keys(frames: dict[str, pd.DataFrame], *,
                     min_overlap: float = 0.5) -> list[dict]:
    """Columns that could join these sheets, best first.

    A candidate must appear in at least two sheets AND its values must actually
    overlap: a column called ``id`` in both sheets that shares none of its values is
    two different ids, and joining on it produces a frame of nulls. Scored on the
    overlap and on how close to unique the column is in its most granular sheet,
    because a key that repeats on both sides multiplies rows.
    """
    if not frames or len(frames) < 2:
        return []
    by_col: dict[str, dict[str, pd.Series]] = {}
    for sheet, df in frames.items():
        if df is None:
            continue
        for c in df.columns:
            by_col.setdefault(_n(c), {})[sheet] = df[c]

    out: list[dict] = []
    for key, per_sheet in by_col.items():
        if len(per_sheet) < 2:
            continue
        sheets = list(per_sheet)
        vals = {s: _values(per_sheet[s]) for s in sheets}
        if any(not v for v in vals.values()):
            continue
        pairs = [(a, b) for i, a in enumerate(sheets) for b in sheets[i + 1:]]
        overlaps = []
        for a, b in pairs:
            smaller = min(len(vals[a]), len(vals[b]))
            if not smaller:
                continue
            overlaps.append(len(vals[a] & vals[b]) / smaller)
        if not overlaps:
            continue
        overlap = sum(overlaps) / len(overlaps)
        ratios = [len(vals[s]) / max(1, len(per_sheet[s])) for s in sheets]
        # BEST sheet for scoring — a key that is unique somewhere is a good key, and
        # the sheet where it is unique is the parent. MIN is reported alongside because
        # that is what shows the relationship: entityid unique on Customer and
        # repeating on Address IS one-to-many, and a caller checking whether the join
        # can multiply rows needs to see it rather than infer it from an average.
        uniqueness = max(ratios, default=0.0)
        out.append({
            "column": next(iter(per_sheet.values())).name,
            "normalised": key,
            "sheets": sheets,
            "value_overlap": round(overlap, 3),
            "uniqueness": round(uniqueness, 3),
            "min_uniqueness": round(min(ratios, default=0.0), 3),
            "one_to_many": min(ratios, default=1.0) < 1.0,
            # Overlap is what makes a join correct; uniqueness only breaks ties, since
            # a legitimate one-to-many key (a customer id on an address sheet) is not
            # unique there and must not be penalised out of contention.
            "score": round(overlap * 0.75 + uniqueness * 0.25, 3),
            "usable": overlap >= min_overlap,
        })
    out.sort(key=lambda x: -x["score"])
    return out


def merge_sheets(frames: dict[str, pd.DataFrame], join_key: Optional[str] = None,
                 *, primary: Optional[str] = None) -> tuple[pd.DataFrame, dict]:
    """Join the sheets of one workbook into a single frame.

    ``(frame, report)``. The report says which key was used, which sheet led, and how
    many rows matched — a join that matched almost nothing is a wrong key, and the
    caller must be able to see that rather than ship the result.

    Colliding column names are prefixed with the sheet name, so "City" from the
    address tab does not overwrite "City" from the customer tab. Losing one of them
    silently is how a mapping ends up pointed at the wrong column.
    """
    live = {s: df for s, df in (frames or {}).items()
            if df is not None and len(df.columns)}
    if not live:
        return pd.DataFrame(), {"joined": 0, "error": "no readable sheets"}
    if len(live) == 1:
        sheet, df = next(iter(live.items()))
        return df.copy(), {"joined": 1, "primary": sheet, "join_key": None,
                           "rows": int(len(df)),
                           "note": "single sheet — nothing to join"}

    # The primary sheet leads the join: the one with the most rows, since the finer
    # grain is what the output has to preserve (22,505 addresses, not 5,489 parties).
    primary = primary if primary in live else max(live, key=lambda s: len(live[s]))

    if not join_key:
        cands = [c for c in detect_join_keys(live) if c["usable"]]
        if not cands:
            return pd.DataFrame(), {
                "joined": 0, "primary": primary, "join_key": None,
                "error": "No column joins these sheets — no shared column has "
                         "overlapping values. Merging them in row order would attach "
                         "one record's data to another, so this needs the join column "
                         "naming explicitly, or the sheets kept as separate sources.",
                "candidates": detect_join_keys(live)[:5],
            }
        join_key = cands[0]["column"]

    def _col(df: pd.DataFrame) -> Optional[str]:
        return next((c for c in df.columns if _n(c) == _n(join_key)), None)

    base = live[primary].copy()
    base_key = _col(base)
    if base_key is None:
        return pd.DataFrame(), {
            "joined": 0, "primary": primary, "join_key": join_key,
            "error": f"The primary sheet {primary!r} has no column {join_key!r}."}
    base["__k"] = base[base_key].astype(str).str.strip()
    report_sheets = [{"sheet": primary, "rows": int(len(base)), "role": "primary"}]

    for sheet, df in live.items():
        if sheet == primary:
            continue
        k = _col(df)
        if k is None:
            report_sheets.append({"sheet": sheet, "rows": int(len(df)),
                                  "role": "skipped — no join column"})
            continue
        right = df.copy()
        right["__k"] = right[k].astype(str).str.strip()
        # One row per key on the right, or the join multiplies the left. Keeping the
        # first is a real limitation, so it is reported rather than assumed harmless.
        dupes = int(right["__k"].duplicated().sum())
        right = right.drop_duplicates("__k")
        rename = {c: (c if _n(c) not in {_n(x) for x in base.columns} or _n(c) == _n(join_key)
                      else f"{sheet}.{c}")
                  for c in right.columns if c != "__k"}
        right = right.rename(columns=rename).drop(columns=[k] if k in right.columns
                                                 and _n(k) == _n(join_key) else [])
        before = len(base)
        base = base.merge(right, on="__k", how="left", suffixes=("", f"_{_n(sheet)}"))
        matched = int(base[[c for c in right.columns if c != "__k"][0]].notna().sum()) \
            if len([c for c in right.columns if c != "__k"]) else 0
        report_sheets.append({
            "sheet": sheet, "rows": int(len(df)), "role": "joined",
            "matched_rows": matched, "match_rate": round(matched / max(1, before), 3),
            "collapsed_duplicate_keys": dupes,
        })

    base = base.drop(columns=["__k"])
    joined = [s for s in report_sheets if s["role"] == "joined"]
    weak = [s for s in joined if s.get("match_rate", 1) < 0.5]
    return base, {
        "joined": len(joined) + 1,
        "primary": primary,
        "join_key": join_key,
        "rows": int(len(base)),
        "columns": int(len(base.columns)),
        "sheets": report_sheets,
        # A key that matched under half the rows is almost certainly the wrong key.
        # Said out loud, because the resulting frame looks perfectly well-formed.
        "warning": ("" if not weak else
                    "Joined on " + repr(join_key) + " but " +
                    ", ".join(f"{s['sheet']} matched only "
                              f"{int(s['match_rate'] * 100)}% of rows"
                              for s in weak) +
                    " — check that this is the right join column."),
    }


def describe_choice(sheets: list[dict], candidates: list[dict]) -> dict:
    """What to ask the analyst, with the evidence for each answer.

    The prompt is not "pick one" — it comes with what the file suggests, because the
    analyst is the only one who knows whether two tabs are two systems or two entities,
    and the row grains and the shared columns are real evidence either way.
    """
    usable = [c for c in candidates if c.get("usable")]
    same_grain = len({s.get("rows") for s in sheets if s.get("rows")}) == 1
    if usable and not same_grain:
        lean = MODE_ONE
        why = (f"The sheets share {usable[0]['column']!r} with "
               f"{int(usable[0]['value_overlap'] * 100)}% overlapping values and have "
               f"different row counts — that looks like one input split across sheets "
               f"(e.g. Customer and Address).")
    elif same_grain and not usable:
        lean = MODE_PER_SHEET
        why = ("The sheets have the same row count and share no joinable column — that "
               "looks like the same kind of record from different sources.")
    else:
        lean = None
        why = ("The file is ambiguous: "
               + ("a shared column exists" if usable else "no shared column was found")
               + " and the row counts "
               + ("match" if same_grain else "differ") + ".")
    return {
        "question": "Does this workbook hold several SOURCES of the same record, or "
                    "ONE input split across sheets?",
        "options": [
            {"mode": MODE_PER_SHEET,
             "label": "Several sources — one conversion per sheet",
             "detail": "Each sheet becomes its own dataset and conversion; the outputs "
                       "converge at generation through the existing survivorship merge."},
            {"mode": MODE_ONE,
             "label": "One input across several sheets — merge into a single conversion",
             "detail": "The sheets are joined on a shared key into one dataset, so the "
                       "mapper sees the union of their columns."},
        ],
        "suggested": lean,
        "why": why,
        "join_key_candidates": candidates[:5],
        "needs_join_key": lean == MODE_ONE and not usable,
    }
