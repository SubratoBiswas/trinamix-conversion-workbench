"""Required-field gate: refuse to generate a file Oracle will reject on row 1.

WHY A GATE AND NOT A WARNING
----------------------------
The existing DQ report already counts missing-required fields, but it is
advisory — generation proceeds and the reject surfaces hours later in Fusion, or
at cutover. A required field with no value is not a quality observation, it is a
guaranteed load failure for every row, and the cheapest place to say so is before
the file is built.

WHAT COUNTS AS SATISFIED
------------------------
A required field passes when the shipped column actually holds a value. That is
deliberately checked on the OUTPUT, not on the mapping: a field can be mapped to
a source column that exists but is empty, mapped to a column that is missing from
this extract, or satisfied by a control default with no mapping at all. Only
looking at the finished frame gets all three right — and section 10.1 is the
standing lesson that a rule which looks applied in the UI is often absent from
the file.

Pure (pandas + stdlib) so the rules are unit-testable without DB or network.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

_DATA = Path(__file__).resolve().parent.parent / "data"
_NORM = re.compile(r"[^a-z0-9]+")

STATUS_OK = "ok"
STATUS_EMPTY = "empty"          # column present, no values
STATUS_PARTIAL = "partial"      # some rows blank
STATUS_MISSING = "missing"      # column not in the output at all

_BLANKS = {"", "nan", "none", "null", "na", "<na>"}


def _n(s: Any) -> str:
    return _NORM.sub("", str(s).lower()) if s is not None else ""


def _blank(v: Any) -> bool:
    return str("" if v is None else v).strip().lower() in _BLANKS


def load_required(target_object: str) -> dict:
    """Per-sheet required fields for an object. {} when none are curated."""
    obj = _n(target_object)
    for p in sorted(_DATA.glob("*_required_fields.json")):
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                       # noqa: BLE001
            continue
        if _n(blob.get("target_object")) == obj:
            return {str(k): [str(x) for x in v]
                    for k, v in (blob.get("sheets") or {}).items()}
    return {}


def check_frame(df: pd.DataFrame, required: Iterable[str]) -> list[dict]:
    """Status of each required field against one finished sheet frame."""
    if df is None:
        return [{"field": f, "status": STATUS_MISSING, "present": 0, "rows": 0,
                 "blank": 0} for f in required]
    by_norm = {_n(c): c for c in df.columns}
    rows = int(len(df))
    out: list[dict] = []
    for f in required:
        col = by_norm.get(_n(f))
        if col is None:
            out.append({"field": f, "status": STATUS_MISSING, "column": None,
                        "rows": rows, "present": 0, "blank": rows})
            continue
        s = df[col]
        blank = int(sum(1 for v in s if _blank(v)))
        present = rows - blank
        status = (STATUS_EMPTY if present == 0
                  else STATUS_PARTIAL if blank else STATUS_OK)
        out.append({"field": f, "status": status, "column": str(col),
                    "rows": rows, "present": present, "blank": blank})
    return out


def check_sheets(frames: dict[str, pd.DataFrame], required_by_sheet: dict) -> dict:
    """Check every curated sheet. ``frames`` is {sheet_name: frame}.

    A sheet with no frame is reported rather than skipped — "the bundle never
    produced this sheet" is exactly the failure a required-field gate exists to
    catch, and skipping it would report a clean pass.
    """
    by_norm = {_n(k): k for k in (frames or {})}
    sheets: list[dict] = []
    for sheet, fields in (required_by_sheet or {}).items():
        key = by_norm.get(_n(sheet))
        df = (frames or {}).get(key) if key else None
        checks = check_frame(df, fields)
        failed = [c for c in checks
                  if c["status"] in (STATUS_MISSING, STATUS_EMPTY)]
        partial = [c for c in checks if c["status"] == STATUS_PARTIAL]
        sheets.append({
            "sheet": sheet,
            "sheet_generated": df is not None,
            "rows": int(len(df)) if df is not None else 0,
            "checks": checks,
            "failed": [c["field"] for c in failed],
            "partial": [c["field"] for c in partial],
        })
    hard = [(s["sheet"], f) for s in sheets for f in s["failed"]]
    soft = [(s["sheet"], f) for s in sheets for f in s["partial"]]
    return {
        "sheets": sheets,
        "required_total": sum(len(v) for v in (required_by_sheet or {}).values()),
        "failed_count": len(hard),
        "partial_count": len(soft),
        # The gate. Anything absent or wholly empty rejects on every row, so the
        # file must not be handed over. A PARTIAL gap does not block: some Oracle
        # sheets legitimately carry optional child rows, and blocking on those
        # would make the gate unusable and get it switched off.
        "blocked": bool(hard),
        "failures": [{"sheet": s, "field": f} for s, f in hard],
        "partials": [{"sheet": s, "field": f} for s, f in soft],
    }


def explain(result: dict) -> str:
    """One-line, human summary for the popup title."""
    n = result.get("failed_count", 0)
    if not n:
        p = result.get("partial_count", 0)
        return (f"All required fields are populated ({p} field(s) partially filled)."
                if p else "All required fields are populated.")
    first = result["failures"][0]
    more = f" and {n - 1} more" if n > 1 else ""
    return (f"{n} required field(s) have no value — "
            f"{first['sheet']} · {first['field']}{more}. "
            "Oracle rejects every row without these.")
