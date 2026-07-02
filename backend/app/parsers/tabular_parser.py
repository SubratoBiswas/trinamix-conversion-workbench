"""CSV / XLSX parsing & column profiling.

Profiles each column with: inferred type, null %, distinct count, sample values,
min/max for numeric/date, and a pattern summary.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd


def _looks_html(head: bytes) -> bool:
    s = head[:1024].lstrip().lower()
    return (s.startswith(b"<!doctype html") or s.startswith(b"<html")
            or s.startswith(b"<table") or (b"<table" in s and b"<tr" in s)
            or (s.startswith(b"<?xml") and b"spreadsheet" in s))


_MIN_STYLES = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    b'<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
    b'<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
    b'<borders count="1"><border/></borders>'
    b'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    b'<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
    b'<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    b'</styleSheet>'
)


def _repair_xlsx(raw: bytes) -> bytes:
    """Rebuild the xlsx zip, swapping a broken xl/styles.xml for a minimal valid
    one. Fixes 'could not read stylesheet ... invalid XML' exports."""
    import io
    import zipfile
    src = zipfile.ZipFile(io.BytesIO(raw))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/styles.xml":
                data = _MIN_STYLES
            out.writestr(item.filename, data)
    return buf.getvalue()


def _promote_header(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Given a header-less frame, pick the most likely header row (the row with
    the most non-empty cells within the first 15) and drop the rows above it —
    handles report titles / metadata rows sitting above the real header."""
    df = raw_df.fillna("")
    n = len(df)
    if n == 0:
        return df.astype(str)
    best_row, best_filled = 0, -1
    for i in range(min(15, n)):
        filled = sum(1 for v in df.iloc[i] if str(v).strip() != "")
        if filled > best_filled:
            best_filled, best_row = filled, i
    header, seen = [], {}
    for j, v in enumerate(df.iloc[best_row]):
        name = str(v).strip() or f"col_{j + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        header.append(name)
    body = df.iloc[best_row + 1:].reset_index(drop=True)
    body.columns = header
    return body.astype(str)


def _read_html_table(file_path: Path) -> pd.DataFrame:
    """Some 'xlsx'/'xls'/'csv' exports (Anaplan, legacy tools) are actually HTML
    tables. Read the largest table."""
    tables = pd.read_html(str(file_path))  # requires lxml
    if not tables:
        raise ValueError("No HTML tables found in file")
    return max(tables, key=lambda d: d.shape[0] * max(1, d.shape[1])).astype(str)


def _read_excel_robust(file_path: Path, raw: bytes) -> pd.DataFrame:
    import io
    try:
        xl = pd.ExcelFile(io.BytesIO(raw), engine="openpyxl")
    except Exception:
        xl = pd.ExcelFile(io.BytesIO(_repair_xlsx(raw)), engine="openpyxl")
    best_df, best_cells = None, -1
    for name in xl.sheet_names:
        try:
            d = xl.parse(name, header=None, dtype=str)
        except Exception:
            continue
        cells = int(d.notna().to_numpy().sum())
        if cells > best_cells:
            best_cells, best_df = cells, d
    if best_df is None:
        raise ValueError("Workbook has no readable sheets")
    return _promote_header(best_df)


def _read_csv_robust(file_path: Path, raw: bytes) -> pd.DataFrame:
    import io
    encs = []
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encs = ["utf-16"]
    elif raw[:3] == b"\xef\xbb\xbf":
        encs = ["utf-8-sig"]
    encs += ["utf-8", "cp1252", "latin-1"]
    last_err = None
    for enc in encs:
        try:
            df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False,
                             encoding=enc, engine="python", sep=None, on_bad_lines="skip")
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
        if df.shape[1] <= 1:  # sniffing collapsed columns — try explicit delimiters
            for sep in ("\t", ";", "|", ","):
                try:
                    d2 = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False,
                                     encoding=enc, sep=sep, engine="python", on_bad_lines="skip")
                    if d2.shape[1] > df.shape[1]:
                        df = d2
                except Exception:  # noqa: BLE001
                    pass
        return df.astype(str)
    raise ValueError(f"Could not read this file as CSV/text ({last_err or 'unknown encoding'}).")


def parse_tabular(file_path: Path | str, file_type: str | None = None) -> pd.DataFrame:
    """Robust CSV / XLSX / HTML parser. Detects the real format by magic bytes
    (the extension can lie), repairs corrupt xlsx stylesheets, unwraps HTML tables
    exported as .xls(x)/.csv, sniffs CSV encoding (incl. UTF-16 / BOM) and
    delimiter, picks the largest sheet, and detects the header row below titles."""
    file_path = Path(file_path)
    raw = file_path.read_bytes()
    head = raw[:1024]
    if raw[:4] == b"PK\x03\x04":               # real OOXML/xlsx (zip signature)
        return _read_excel_robust(file_path, raw)
    if _looks_html(head):                        # HTML masquerading as a spreadsheet
        try:
            return _read_html_table(file_path)
        except Exception:  # noqa: BLE001 — fall through to text parsing
            pass
    ftype = (file_type or file_path.suffix.lstrip(".")).lower()
    if ftype in ("xlsx", "xls", "xlsm"):
        try:
            return _read_excel_robust(file_path, raw)
        except Exception:  # noqa: BLE001 — last resort: try as delimited text
            return _read_csv_robust(file_path, raw)
    return _read_csv_robust(file_path, raw)


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
