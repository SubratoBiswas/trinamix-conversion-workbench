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

# A curated sheet this conversion does not own. The Supplier bundle spans six
# interface tables and a conversion's template normally declares ONE of them, so
# five of the six curated sheets are simply another conversion's file. Calling those
# "missing" is what made the gate fire on every healthy Supplier conversion — it is
# a scope statement, not a data problem, so it reports and never blocks.
SHEET_NOT_OWNED = "not_applicable"


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


def load_sheet_aliases(target_object: str) -> dict[str, list[str]]:
    """``{curated sheet name: [other names for the same sheet]}``.

    Two vocabularies are in play for the same thing: a template names its sheets
    after the Oracle INTERFACE TABLE (``POZ_SUPPLIERS_INT``) and the analyst's
    required-field list names them after the WORKBOOK TAB (``Supplier Import``).
    Matching on one alone recognises no sheet at all.
    """
    obj = _n(target_object)
    for p in sorted(_DATA.glob("*_required_fields.json")):
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                       # noqa: BLE001
            continue
        if _n(blob.get("target_object")) == obj:
            return {str(k): [str(x) for x in (v or [])]
                    for k, v in (blob.get("sheet_aliases") or {}).items()}
    return {}


def names_for(sheet: str, aliases: dict[str, list[str]] | None) -> list[str]:
    """Every spelling of one curated sheet: itself plus its aliases, either way round.

    Reverse lookup matters because the caller may hold the interface-table name and
    the curated key may be the tab name, or the other way about.
    """
    aliases = aliases or {}
    out = [sheet, *aliases.get(sheet, [])]
    key = _n(sheet)
    for curated, alts in aliases.items():
        if key == _n(curated) or any(key == _n(a) for a in alts):
            out.extend([curated, *alts])
    seen, uniq = set(), []
    for name in out:
        if name and _n(name) not in seen:
            seen.add(_n(name))
            uniq.append(name)
    return uniq


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


def check_sheets(frames: dict[str, pd.DataFrame], required_by_sheet: dict,
                 *, owned_sheets: Iterable[str] | None = None,
                 aliases: dict[str, list[str]] | None = None) -> dict:
    """Check every curated sheet. ``frames`` is {sheet_name: frame}.

    ``owned_sheets`` — the interface sheets THIS conversion's template declares.
    A curated sheet outside that set belongs to a sibling conversion (the Supplier
    bundle spans six interface tables and one template usually declares one), so it
    is reported as not-owned and does NOT block. Omit the argument and every curated
    sheet is treated as owned, which is the strict all-or-nothing behaviour.

    ``aliases`` — other spellings of the same sheet, because a template names sheets
    after the interface table and this list names them after the workbook tab.

    A sheet that IS owned but has no frame still fails hard: "the bundle should have
    produced this and did not" is exactly what a gate exists to catch, and skipping
    it would report a clean pass.
    """
    by_norm = {_n(k): k for k in (frames or {})}
    owned_norm = {_n(s) for s in (owned_sheets or []) if str(s).strip()}
    sheets: list[dict] = []
    for sheet, fields in (required_by_sheet or {}).items():
        spellings = names_for(sheet, aliases)
        key = next((by_norm[_n(s)] for s in spellings if _n(s) in by_norm), None)
        df = (frames or {}).get(key) if key else None
        # Scope: does this conversion own the sheet at all? Any spelling counts.
        owned = (not owned_norm) or any(_n(s) in owned_norm for s in spellings)
        if df is None and not owned:
            sheets.append({
                "sheet": sheet, "sheet_generated": False, "rows": 0,
                "owned": False,
                "checks": [{"field": f, "status": SHEET_NOT_OWNED, "column": None,
                            "rows": 0, "present": 0, "blank": 0} for f in fields],
                "failed": [], "partial": [],
                "not_owned": [str(f) for f in fields],
            })
            continue
        checks = check_frame(df, fields)
        failed = [c for c in checks
                  if c["status"] in (STATUS_MISSING, STATUS_EMPTY)]
        partial = [c for c in checks if c["status"] == STATUS_PARTIAL]
        sheets.append({
            "sheet": sheet,
            "matched_as": key,
            "owned": True,
            "sheet_generated": df is not None,
            "rows": int(len(df)) if df is not None else 0,
            "checks": checks,
            "failed": [c["field"] for c in failed],
            "partial": [c["field"] for c in partial],
            "not_owned": [],
        })
    hard = [(s["sheet"], f) for s in sheets for f in s["failed"]]
    soft = [(s["sheet"], f) for s in sheets for f in s["partial"]]
    skipped = [(s["sheet"], f) for s in sheets for f in s.get("not_owned", [])]
    return {
        "sheets": sheets,
        "required_total": sum(len(v) for v in (required_by_sheet or {}).values()),
        "failed_count": len(hard),
        "partial_count": len(soft),
        # Fields belonging to sheets this conversion does not produce. Counted and
        # named so the report can say "checked 1 of 6 sheets" instead of implying it
        # checked all six — the other five are a sibling conversion's gate to pass.
        "not_owned_count": len(skipped),
        "sheets_checked": sum(1 for s in sheets if s.get("owned")),
        "sheets_curated": len(sheets),
        # The gate. Anything absent or wholly empty rejects on every row, so the
        # file must not be handed over. A PARTIAL gap does not block: some Oracle
        # sheets legitimately carry optional child rows, and blocking on those
        # would make the gate unusable and get it switched off.
        "blocked": bool(hard),
        "failures": [{"sheet": s, "field": f} for s, f in hard],
        "partials": [{"sheet": s, "field": f} for s, f in soft],
        "not_owned": [{"sheet": s, "field": f} for s, f in skipped],
    }


def explain(result: dict) -> str:
    """One-line, human summary for the popup title."""
    n = result.get("failed_count", 0)
    checked = result.get("sheets_checked")
    curated = result.get("sheets_curated")
    scope = ""
    if checked is not None and curated and checked < curated:
        # Never imply a wider check than actually ran.
        scope = (f" Checked {checked} of {curated} curated sheet(s); the rest belong "
                 f"to other conversions in this bundle.")
    if not n:
        p = result.get("partial_count", 0)
        head = (f"All required fields are populated ({p} field(s) partially filled)."
                if p else "All required fields are populated.")
        return head + scope
    first = result["failures"][0]
    more = f" and {n - 1} more" if n > 1 else ""
    return (f"{n} required field(s) have no value — "
            f"{first['sheet']} · {first['field']}{more}. "
            "Oracle rejects every row without these." + scope)
