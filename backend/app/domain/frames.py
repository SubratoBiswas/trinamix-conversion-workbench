"""Frame-level FBDI output formatting (Phase 2, slice 3). Relocated VERBATIM out of
app.services.output_service: date normalisation to yyyy/mm/dd, null-sentinel blanking,
SYSDATE-token resolution, supplier-email masking, and the small column/sheet-name
utilities. Pure — pandas + re + datetime + the domain date parsers; no I/O, no service
imports. output_service and the fusion/operations routers import these back under their
historical names, so call sites are unchanged."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pandas as pd

from app.domain.dates.fbdi_date import (
    OUT_DATE_FORMAT as FBDI_DATE_FORMAT,
    parse_with_formats as _parse_with_formats,
    INPUT_FORMATS as _DATE_INPUT_FORMATS_SRC,
    INPUT_FORMATS_DAYFIRST as _DATE_INPUT_FORMATS_DAYFIRST_SRC,
)

# Tokens that mean "today", not a literal string. An analyst who sets a date
# column's constant to SYSDATE means Oracle's current date, not the seven letters
# "SYSDATE" — which is what shipped: the BOM Effective Date column carried the text
# SYSDATE on every row. Resolved to today in the output spelling.
_TODAY_TOKENS = {"sysdate", "today", "now", "current_date", "currentdate",
                 "system date", "systemdate", "getdate()", "current date"}


def to_fbdi_date(v: Any, dayfirst: bool = False) -> Any:
    """One cell → ``yyyy/mm/dd``, or the value untouched if it is not a date.

    Untouched is deliberate: a column that turns out to hold free text must not be
    mangled, and an unparseable date is more useful in the reject report as the
    analyst's original string than as a blank. The exception is a SYSDATE-style
    token, which is an INSTRUCTION ("use today"), not free text, and is resolved.

    ``dayfirst`` reads the ambiguous ``xx-xx-YYYY`` / ``xx/xx/YYYY`` spellings day-first
    (a source whose dates are DD-MM-YYYY). Default False keeps the historic month-first
    reading for every other source.
    """
    if v is None or str(v).strip() == "":
        return v
    s = str(v).strip()
    if s.lower() in _TODAY_TOKENS:
        return datetime.utcnow().strftime(FBDI_DATE_FORMAT)
    # Fractional seconds ("2020-01-15 00:00:00.000") — strptime has no optional
    # group for them, so drop the fraction before matching.
    core = re.sub(r"\.\d+$", "", s)
    _dt = _parse_with_formats(
        core, _DATE_INPUT_FORMATS_DAYFIRST_SRC if dayfirst else _DATE_INPUT_FORMATS_SRC)
    return _dt.strftime(FBDI_DATE_FORMAT) if _dt else v


def format_date_columns(df: pd.DataFrame, fields: list, dayfirst: bool = False) -> pd.DataFrame:
    """Reformat any date/Date columns to ``yyyy/mm/dd`` (see FBDI_DATE_FORMAT).

    Matched on a NORMALISED name (case and punctuation folded), because the frame's
    headers and the template's field names routinely disagree on both: the EBS path
    runs ``_normalize_columns`` first, so ``EffectiveStartDate`` arrives as
    ``EFFECTIVE_START_DATE``. The previous exact ``col in date_field_names`` test
    therefore matched nothing on that path and shipped ``2020-01-15`` unconverted —
    every dated row a mismatch. Found by tests/test_ebs_output.py, which encoded
    the intent from the start; the implementation never met it.
    """
    date_field_names = {
        re.sub(r"[^a-z0-9]", "", (f.field_name or "").lower())
        for f in fields
        if (f.data_type or "").lower() in ("date", "datetime")
    }
    date_field_names.discard("")
    for col in df.columns:
        if re.sub(r"[^a-z0-9]", "", str(col).lower()) in date_field_names:
            df[col] = df[col].apply(lambda _v: to_fbdi_date(_v, dayfirst))
    return df


# Sentinel strings that legacy/SQL exports (SyteLine, NetSuite saved searches,
# etc.) write for "no value". Loaded verbatim into Oracle they'd become the
# literal text "NULL"/"N/A" instead of an empty cell, so blank them at generate.
_NULL_SENTINELS = {"null", "(null)", "#n/a", "n/a", "nan", "none", "\\n"}

# Person-name columns legitimately carry a value that collides with a null
# sentinel: a NetSuite contact whose first name is literally "None" (internalid
# 4025141 in the NextPower Customer extract, lastname "."). Analysts keep that
# verbatim rather than emptying it, so the "none" token is NOT treated as null on
# these columns. Every harder sentinel (NULL, N/A, NaN, \N) still blanks, and this
# is an exact-name set so "Party Site Name" and other …Name fields are untouched.
_PERSON_NAME_COLS = {
    "person first name", "person middle name", "person last name",
    "person second last name", "person last name prefix", "person name suffix",
}
_NAME_SENTINEL_KEEP = {"none"}


def blank_null_sentinels(df: pd.DataFrame) -> pd.DataFrame:
    """Replace whole-cell null sentinels (case-insensitive) with empty strings.
    Whole-cell match only, so a real value like a description containing the word
    is never touched. Person-name columns keep a literal "None" (see
    ``_PERSON_NAME_COLS``); all other sentinels still blank there."""
    for col in df.columns:
        s = df[col]
        if s.dtype != object:
            continue
        col_key = str(col).strip().lower().rstrip("*").strip()
        sentinels = (_NULL_SENTINELS - _NAME_SENTINEL_KEEP
                     if col_key in _PERSON_NAME_COLS else _NULL_SENTINELS)
        mask = s.astype(str).str.strip().str.lower().isin(sentinels)
        if mask.any():
            df.loc[mask, col] = ""
    return df


def resolve_today_tokens(df: pd.DataFrame) -> pd.DataFrame:
    """Whole-cell SYSDATE/TODAY/NOW → today's date, in EVERY column, type-blind.

    ``to_fbdi_date`` already resolves these tokens, but only on columns
    ``format_date_columns`` recognises as date-typed. A template that types a date
    field as *Character* slips past that filter: the BOM ``Effective Date`` column is
    declared Character, its default is the literal ``SYSDATE``, and it is filled by
    ``_apply_control_defaults`` — AFTER the date pass — so every one of the 5,000+
    rows shipped the seven letters "SYSDATE" instead of a date, which Oracle rejects.

    A cell equal to one of these tokens is an INSTRUCTION ("use today"), never valid
    output, whatever the column's declared type — so this is the type-independent
    backstop, run on the finished frame after every default/decision has landed.
    Whole-cell, token-set match only (same shape and safety as
    ``blank_null_sentinels``), so a real value that merely contains the word is
    untouched.
    """
    today = datetime.utcnow().strftime(FBDI_DATE_FORMAT)
    for col in df.columns:
        s = df[col]
        if s.dtype != object:
            continue
        mask = s.astype(str).str.strip().str.lower().isin(_TODAY_TOKENS)
        if mask.any():
            df.loc[mask, col] = today
    return df


def dedup(cols: list[str]) -> list[str]:
    seen: set[str] = set()
    return [c for c in cols if not (c in seen or seen.add(c))]


# Supplier email masking: on a test / migration supplier load, real e-mail
# addresses in the file can make Oracle fire supplier/contact notifications. We
# neutralise them by prefixing "xx" (so "ap@x.com" -> "xxap@x.com", an invalid
# address that won't route). Applied to any email-named column of a Supplier
# object at generate. Idempotent (won't double-prefix) and skips blanks.
_SUPPLIER_EMAIL_PREFIX = "xx"


def mask_supplier_emails(df: pd.DataFrame, prefix: str = _SUPPLIER_EMAIL_PREFIX) -> pd.DataFrame:
    for col in df.columns:
        key = re.sub(r"[^a-z0-9]", "", str(col).lower())
        if "email" not in key:
            continue
        s = df[col].astype(str)
        mask = s.str.strip().ne("") & ~s.str.strip().str.lower().isin(_NULL_SENTINELS) \
            & ~s.str.startswith(prefix)
        if mask.any():
            df.loc[mask, col] = prefix + s[mask]
    return df


def safe_sheet_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "sheet"


# ── Column / header helpers ──────────────────────────────────────────────────────
# Oracle descriptive-flexfield (DFF) attribute columns are never populated (NextPower
# does not use DFFs; a value there risks failing DFF validation on load).
_ATTR_RE = re.compile(r"^(global[_ ]?)?attribute([_ ]?(category|date|number|timestamp|char))?[_ ]?\d*$")


def is_attribute_column(name: str | None) -> bool:
    """True for any Oracle descriptive-flexfield (DFF) attribute column.

    Covers ATTRIBUTE1..30, ATTRIBUTE_CATEGORY, ATTRIBUTE_DATE/NUMBER/TIMESTAMP/CHAR n
    and the GLOBAL_ATTRIBUTE* variants, in any spacing/casing the templates use.
    NextPower does not use DFFs; populating them risks failing DFF validation on load.
    """
    n = re.sub(r"[^a-z0-9_ ]", "", str(name or "").strip().lower())
    return bool(_ATTR_RE.match(n.replace(" ", "_")))


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column headers to UPPER_UNDERSCORE (Oracle FBDI format)."""
    df.columns = [c.strip().upper().replace(" ", "_").replace("-", "_") for c in df.columns]
    return df


def header_label(f) -> str:
    """The header text to write for a field. Prefer the raw header captured at
    parse time (which carries Oracle's exact '*' required markers, e.g.
    'Import Action *', 'Supplier Name*'); fall back to appending a trailing '*'
    for required fields when only the cleaned name is stored (older templates)."""
    raw = (getattr(f, "display_name", None) or "").strip()
    if "*" in raw:
        return raw
    base = (f.field_name or "").strip()
    if getattr(f, "required", False) and base and "*" not in base:
        return base + " *"
    return base or raw
