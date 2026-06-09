"""CSV / XLSX parsing & column profiling.

Profiles each column with: inferred type, null %, distinct count, sample values,
min/max for numeric/date, and a pattern summary.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd


def parse_tabular(file_path: Path | str, file_type: str | None = None) -> pd.DataFrame:
    """Parse a CSV or XLSX file into a DataFrame. All columns kept as string for
    safe profiling — type inference is done explicitly later."""
    file_path = Path(file_path)
    ftype = (file_type or file_path.suffix.lstrip(".")).lower()
    if ftype == "csv":
        # Try common encodings
        for enc in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(file_path, dtype=str, keep_default_na=False, encoding=enc)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(file_path, dtype=str, keep_default_na=False, encoding="latin-1")
    elif ftype in ("xlsx", "xls", "xlsm"):
        return pd.read_excel(file_path, dtype=str, keep_default_na=False)
    raise ValueError(f"Unsupported file type: {ftype}")


_DATE_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "YYYY-MM-DD"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "MM/DD/YYYY or DD/MM/YYYY"),
    (re.compile(r"^\d{4}/\d{2}/\d{2}$"), "YYYY/MM/DD"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "MM-DD-YYYY or DD-MM-YYYY"),
    (re.compile(r"^\d{4}\d{2}\d{2}$"), "YYYYMMDD"),
]
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d*\.\d+$")
_BOOL_VALS = {"true", "false", "yes", "no", "y", "n", "0", "1", "t", "f"}


def _classify_value(v: str) -> str:
    v = v.strip()
    if v == "":
        return "null"
    if _INT_RE.match(v):
        return "integer"
    if _FLOAT_RE.match(v):
        return "float"
    for pat, _ in _DATE_PATTERNS:
        if pat.match(v):
            return "date"
    if v.lower() in _BOOL_VALS and len(v) <= 5:
        return "boolean"
    return "string"


def _infer_column_type(values: list[str]) -> str:
    if not values:
        return "string"
    seen: dict[str, int] = {}
    for v in values:
        seen[_classify_value(v)] = seen.get(_classify_value(v), 0) + 1
    seen.pop("null", None)
    if not seen:
        return "string"
    # if integers + floats → float
    if set(seen) <= {"integer", "float"}:
        return "float" if "float" in seen else "integer"
    if set(seen) == {"date"}:
        return "date"
    if set(seen) <= {"boolean"} and sum(seen.values()) > 0:
        return "boolean"
    return "string"


def _detect_pattern(values: list[str]) -> str | None:
    """Return a short human-readable pattern hint."""
    if not values:
        return None
    sample = values[: min(50, len(values))]
    for pat, label in _DATE_PATTERNS:
        if all(pat.match(v.strip()) for v in sample if v.strip()):
            return f"Date format: {label}"
    if all(_INT_RE.match(v.strip()) for v in sample if v.strip()):
        return "All numeric integers"
    if all(re.match(r"^[A-Z]{2,5}$", v.strip()) for v in sample if v.strip()):
        return "Short uppercase code (e.g. UOM, currency)"
    if all(re.match(r"^[A-Za-z0-9\-_/]+$", v.strip()) for v in sample if v.strip()):
        return "Alphanumeric identifier"
    return None


def profile_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Return a list of column-profile dicts."""
    profiles: list[dict[str, Any]] = []
    total = len(df)
    for pos, col in enumerate(df.columns):
        series = df[col].astype(str).fillna("")
        non_null = [v for v in series.tolist() if v.strip() != ""]
        nulls = total - len(non_null)
        distinct = len(set(non_null))
        sample = []
        seen_set: set[str] = set()
        for v in non_null:
            if v not in seen_set:
                seen_set.add(v)
                sample.append(v)
                if len(sample) >= 8:
                    break
        inferred = _infer_column_type(non_null)
        min_val = max_val = None
        if inferred in ("integer", "float") and non_null:
            try:
                nums = [float(v) for v in non_null if _INT_RE.match(v) or _FLOAT_RE.match(v)]
                if nums:
                    min_val = str(min(nums))
                    max_val = str(max(nums))
            except Exception:
                pass
        elif inferred == "date" and non_null:
            try:
                vals = sorted(non_null)
                min_val = vals[0]
                max_val = vals[-1]
            except Exception:
                pass
        profiles.append(
            {
                "column_name": str(col),
                "position": pos,
                "inferred_type": inferred,
                "null_count": nulls,
                "null_percent": round((nulls / total * 100) if total else 0.0, 2),
                "distinct_count": distinct,
                "sample_values": sample,
                "min_value": min_val,
                "max_value": max_val,
                "pattern_summary": _detect_pattern(non_null),
            }
        )
    return profiles
