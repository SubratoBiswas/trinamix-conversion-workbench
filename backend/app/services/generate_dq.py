"""Generate-time data quality: cleansing + validation on the MERGED output frame.

Runs after multi-source converge/de-dup and before the file is written. Applies
cleansing rules (a safe universal whitespace trim, plus any custom cleansing rules
for the object+client), then validates the frame (built-in FBDI checks from
``app.validation.engine`` plus custom validation rules) and returns an issues
report. Hard data-validity errors set an advisory ``blocked`` flag.

Pure + dependency-light (pandas + the pure validation engine + plain rule dicts),
so it is unit-testable without the Beanie/Mongo stack. Custom rules are passed in
by the caller (loaded from the rule store).
"""
from __future__ import annotations

import re
from typing import Any, Optional

import pandas as pd

from app.validation.engine import run_validation_checks

# Validation issue types that indicate a genuine DATA-validity failure (as opposed
# to a mapping-completeness gap like a missing-required field). Only these trip the
# advisory download block, so an object with unmapped-but-defaultable required
# fields isn't blocked outright.
HARD_ERROR_TYPES = {
    "Value Not In Value Set",
    "Exceeds Max Length",
    "Invalid Number Format",
    "Negative Value Not Allowed",
    "Invalid Date Format",
    "Min Greater Than Max",
}


def apply_cleansing(df: pd.DataFrame, rules: Optional[list] = None) -> tuple[pd.DataFrame, list]:
    """Cleanse the merged converted frame. Always trims leading/trailing whitespace,
    then applies each custom cleansing rule for a named field. Returns (df, fixes)
    where fixes = [{field, rule, count}]."""
    fixes: list = []
    if df is None or len(df) == 0:
        return df, fixes
    df = df.copy()
    # Universal safe cleanse: strip whitespace on text columns.
    for c in df.columns:
        if df[c].dtype == object:
            before = df[c]
            stripped = before.map(lambda v: v.strip() if isinstance(v, str) else v)
            n = int((stripped.astype(str) != before.astype(str)).sum())
            if n:
                fixes.append({"field": str(c), "rule": "TRIM", "count": n})
            df[c] = stripped

    for r in (rules or []):
        f = r.get("field") or r.get("target_field")
        rt = (r.get("rule_type") or "").upper()
        params = r.get("params") or {}
        if not f or f not in df.columns:
            continue
        col = df[f].astype(str)
        if rt in ("UPPERCASE", "UPPER"):
            new = col.str.upper()
        elif rt in ("LOWERCASE", "LOWER"):
            new = col.str.lower()
        elif rt in ("TITLECASE", "TITLE"):
            new = col.str.title()
        elif rt == "TRIM":
            new = col.str.strip()
        elif rt in ("REMOVE_SPECIAL", "ALNUM"):
            new = col.map(lambda v: re.sub(r"[^A-Za-z0-9 ]", "", v))
        elif rt == "REPLACE":
            new = col.str.replace(str(params.get("from", "")), str(params.get("to", "")), regex=False)
        elif rt in ("DEFAULT_IF_BLANK", "DEFAULT"):
            dv = str(params.get("value", ""))
            new = col.map(lambda v: dv if str(v).strip() == "" else v)
        elif rt in ("PAD_LEFT", "ZPAD"):
            width = int(params.get("width", 0) or 0)
            ch = str(params.get("char", "0"))[:1] or "0"
            new = col.map(lambda v: v.rjust(width, ch) if v.strip() and len(v) < width else v)
        else:
            continue
        n = int((new != col).sum())
        if n:
            fixes.append({"field": str(f), "rule": rt, "count": n})
        df[f] = new
    return df, fixes


def _custom_validation(df: pd.DataFrame, r: dict, sample_rows: int) -> list:
    f = r.get("field") or r.get("target_field")
    rt = (r.get("rule_type") or "").upper()
    params = r.get("params") or {}
    sev = (r.get("severity") or "error").lower()
    if not f or f not in df.columns:
        return []
    s = df[f].head(sample_rows).astype(str)
    issues: list = []

    def _flag(mask, itype, msg, fix):
        cnt = int(mask.sum())
        if cnt:
            issues.append({"category": "validation", "field_name": str(f), "issue_type": itype,
                           "severity": sev, "message": f"{cnt} row(s): {msg}",
                           "suggested_fix": fix, "auto_fixable": False, "impacted_count": cnt})

    if rt == "REQUIRED":
        _flag(s.str.strip() == "", "Missing Required Field", f"'{f}' is empty", "Map a source column or set a default.")
    elif rt in ("MAX_LENGTH", "MAXLEN"):
        ml = int(params.get("max_length", params.get("value", 0)) or 0)
        if ml:
            _flag(s.str.len() > ml, "Exceeds Max Length", f"'{f}' longer than {ml}", f"Truncate to {ml} chars.")
    elif rt in ("REGEX", "PATTERN"):
        pat = str(params.get("pattern", ""))
        if pat:
            nb = s.str.strip() != ""
            bad = nb & ~s.str.match(pat)
            _flag(bad, "Pattern Mismatch", f"'{f}' does not match {pat}", "Correct the value format.")
    elif rt in ("VALUE_IN_SET", "LOV", "VALUESET"):
        allowed = {str(x).strip().lower() for x in (params.get("values") or [])}
        if allowed:
            nb = s.str.strip() != ""
            bad = nb & ~s.str.strip().str.lower().isin(allowed)
            _flag(bad, "Value Not In Value Set", f"'{f}' has values outside the allowed set", "Map to an allowed code.")
    elif rt in ("NOT_NEGATIVE", "NONNEGATIVE"):
        num = pd.to_numeric(s, errors="coerce")
        _flag(num < 0, "Negative Value Not Allowed", f"'{f}' has negative values", "Provide a non-negative value.")
    elif rt in ("NUMERIC", "NUMBER"):
        nb = s.str.strip() != ""
        bad = nb & pd.to_numeric(s, errors="coerce").isna()
        _flag(bad, "Invalid Number Format", f"'{f}' has non-numeric values", "Provide a numeric value.")
    return issues


def validate_frame(df: pd.DataFrame, target_fields: list, custom_rules: Optional[list] = None,
                   sample_rows: int = 20000) -> list:
    """Built-in FBDI validation + custom validation rules on the merged frame."""
    rows = df.head(sample_rows).fillna("").to_dict("records") if df is not None else []
    issues = run_validation_checks(rows, target_fields)
    for r in (custom_rules or []):
        issues += _custom_validation(df, r, sample_rows)
    return issues


# Plain-English guidance per issue type — what Oracle will do + how to fix it.
_EXPLAIN: dict[str, dict] = {
    "Missing Required Field": {
        "meaning": "Oracle rejects a row when a required column is empty.",
        "fix": "Map a source column to this field or set a default value before loading."},
    "Value Not In Value Set": {
        "meaning": "The value isn't a code in Oracle's lookup (LOV) for this field, so the row fails validation.",
        "fix": "Add a value mapping / crosswalk to the accepted code, or correct the source value."},
    "Exceeds Max Length": {
        "meaning": "The value is longer than the column allows; Oracle truncates or rejects it.",
        "fix": "Trim or abbreviate the value to the field's max length (add a cleansing rule)."},
    "Invalid Number Format": {
        "meaning": "A numeric column contains non-numeric text; the load will error.",
        "fix": "Clean non-numeric characters or remap the source column."},
    "Negative Value Not Allowed": {
        "meaning": "A field that must be non-negative has negative values.",
        "fix": "Correct the source or add a rule to reject/zero negatives."},
    "Invalid Date Format": {
        "meaning": "A date isn't in a form Oracle accepts (YYYY/MM/DD).",
        "fix": "Reformat the date (a DATE_FORMAT transform) before loading."},
    "Min Greater Than Max": {
        "meaning": "A min/max pair is inverted (min > max), which Oracle rejects.",
        "fix": "Swap or correct the two values at source."},
    "Pattern Mismatch": {
        "meaning": "The value doesn't match the expected format (e.g. email/phone).",
        "fix": "Correct the format or relax the pattern rule."},
}


def explain_report(report: dict) -> dict:
    """Attach plain-English 'what Oracle will reject and how to fix' guidance to the
    report, grouped by issue type with impacted counts. Deterministic + reliable
    (no external call); an AI layer can enrich this later."""
    groups: dict[str, dict] = {}
    for i in report.get("top_issues", []):
        t = i.get("issue_type") or "Other"
        g = groups.setdefault(t, {"issue_type": t, "count": 0, "example_field": i.get("field_name")})
        g["count"] += int(i.get("impacted_count", 1) or 1)
    explained = []
    for t, g in groups.items():
        info = _EXPLAIN.get(t, {"meaning": "Review this issue before loading.", "fix": "Inspect the flagged rows."})
        explained.append({**g, "meaning": info["meaning"], "fix": info["fix"]})
    explained.sort(key=lambda x: -x["count"])
    return {**report, "explanations": explained}


def build_report(issues: list, cleansing_fixes: list) -> dict:
    """Summarise into a DQ report: severity counts, hard-error count, advisory
    ``blocked`` flag, cleansing fixes applied, and the top issues."""
    errors = [i for i in issues if (i.get("severity") or "").lower() == "error"]
    warnings = [i for i in issues if (i.get("severity") or "").lower() == "warning"]
    hard = [i for i in errors if i.get("issue_type") in HARD_ERROR_TYPES]
    return {
        "error_count": len(errors),
        "warning_count": len(warnings),
        "hard_error_count": len(hard),
        "blocked": bool(hard),
        "cleansing_fixes": cleansing_fixes,
        "cleansing_fix_count": int(sum(f.get("count", 0) for f in cleansing_fixes)),
        "top_issues": issues[:50],
    }
