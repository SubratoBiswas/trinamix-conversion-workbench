"""Check the built output against the column rules Oracle states in its template.

WHAT THIS ANSWERS
-----------------
"Is anything in my data going to be rejected because of what the template says about
this column?" — a mandatory column with no value, a value longer than VARCHAR2(n), a
number too big for NUMBER(18), a date not in YYYY/MM/DD, a value outside the codes
Oracle lists, or a column Oracle explicitly says not to populate.

Every rule comes from ``services/template_comments.py``, which transcribes the comment
Oracle puts on each header cell. Nothing is inferred, which is what makes it safe to
show as a definite finding rather than a suggestion.

WHY IT IS SEPARATE FROM THE DQ VALIDATOR
----------------------------------------
``validation/engine.py`` walks ROWS and emits one issue per offending row: right for a
reject report, useless for a review screen — 8,000 rows of "row N: too long" is not a
finding, it is a wall. This aggregates by COLUMN: one row per rule with a count and a
few real examples, which is the shape the Cleansing tab can actually show and the
analyst can act on. Both read the same mined metadata, so they cannot disagree about
what the rule IS.

Pure (pandas + stdlib): no DB, no network.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Optional

import pandas as pd

_NORM = re.compile(r"[^a-z0-9]+")
_BLANKS = {"", "nan", "none", "null", "na", "<na>", "nat"}

# Severity: does Oracle reject the row, or merely warn/round?
SEV_ERROR = "error"
SEV_WARNING = "warning"

RULE_MANDATORY = "mandatory"
RULE_MAX_LENGTH = "max_length"
RULE_NUMERIC = "numeric"
RULE_PRECISION = "precision"
RULE_SCALE = "scale"
RULE_DATE_FORMAT = "date_format"
RULE_VALUE_SET = "value_set"
RULE_DO_NOT_POPULATE = "do_not_populate"

_BLOCKING = {RULE_MANDATORY}


def _n(s: Any) -> str:
    return _NORM.sub("", str(s).lower()) if s is not None else ""


def _blank(v: Any) -> bool:
    return str("" if v is None else v).strip().lower() in _BLANKS


def _is_num(s: str) -> bool:
    try:
        float(str(s).replace(",", "").strip())
        return True
    except (TypeError, ValueError):
        return False


def _py_date_format(mask: Optional[str]) -> str:
    m = str(mask or "YYYY/MM/DD").upper()
    return (m.replace("YYYY", "%Y").replace("YY", "%y")
             .replace("MM", "%m").replace("DD", "%d")
             .replace("HH24", "%H").replace("HH", "%H")
             .replace("MI", "%M").replace("SS", "%S"))


def _examples(values: Iterable[Any], limit: int = 5) -> list[str]:
    out: list[str] = []
    for v in values:
        s = str(v)
        if s not in out:
            out.append(s[:120])
        if len(out) >= limit:
            break
    return out


def check_column(series: pd.Series, field: dict) -> list[dict]:
    """Every rule violated by one column, aggregated. ``[]`` when it is clean."""
    findings: list[dict] = []
    if series is None:
        return findings
    rows = int(len(series))
    if rows == 0:
        return findings

    name = str(field.get("field_name") or "")
    db_col = field.get("db_column")
    text = series.astype(str)
    blank_mask = series.map(_blank)
    n_blank = int(blank_mask.sum())
    filled = text[~blank_mask]

    def add(rule, severity, count, message, fix, examples=(), **extra):
        if not count:
            return
        findings.append({
            "field": name, "db_column": db_col, "rule": rule,
            "severity": severity, "count": int(count), "rows": rows,
            "message": message, "suggested_fix": fix,
            "examples": list(examples),
            "blocking": rule in _BLOCKING and severity == SEV_ERROR,
            **extra,
        })

    # ── Mandatory. The header '*' and the comment's NOT NULL both land in
    # `required`; either way Oracle rejects a row with no value. ──
    if field.get("required") and n_blank:
        add(RULE_MANDATORY, SEV_ERROR, n_blank,
            f"{name} is mandatory but {n_blank} of {rows} row(s) have no value.",
            "Map a source column, set a default, or exclude the rows. Oracle rejects "
            "every row where this is empty.")

    # ── Oracle says leave it alone. ──
    if field.get("do_not_populate") and len(filled):
        add(RULE_DO_NOT_POPULATE, SEV_WARNING, len(filled),
            f"The template says {name} is not used and no value should be provided, "
            f"but {len(filled)} row(s) have one.",
            "Mark the field Not applicable so it ships blank, or confirm with the "
            "functional team that this interface column is genuinely in use.",
            _examples(filled))

    if not len(filled):
        return findings

    # ── VARCHAR2(n) ──
    ml = field.get("max_length")
    if ml:
        over = filled[filled.str.len() > int(ml)]
        add(RULE_MAX_LENGTH, SEV_ERROR, len(over),
            f"{len(over)} value(s) are longer than the column's {ml} characters "
            f"(longest {int(filled.str.len().max())}).",
            f"Shorten or abbreviate to {ml} characters — a cleansing rule can do it.",
            _examples(over), limit=int(ml))

    dt = str(field.get("data_type") or "").lower()

    # ── NUMBER ──
    if "number" in dt or "integer" in dt or "decimal" in dt:
        bad = filled[~filled.map(_is_num)]
        add(RULE_NUMERIC, SEV_ERROR, len(bad),
            f"{len(bad)} value(s) are not numeric, but the column is a number.",
            "Clean the non-numeric characters or re-check which source column feeds "
            "this field.", _examples(bad))
        prec, scale = field.get("precision"), field.get("scale")
        if prec:
            nums = filled[filled.map(_is_num)]
            ints = nums.str.replace(",", "", regex=False).str.lstrip("+-") \
                       .str.split(".").str[0].str.lstrip("0")
            too_big = nums[ints.str.len() > int(prec) - int(scale or 0)]
            add(RULE_PRECISION, SEV_ERROR, len(too_big),
                f"{len(too_big)} value(s) need more digits than NUMBER({prec}"
                f"{',' + str(scale) if scale else ''}) holds.",
                "Oracle rejects the row. A number this long is usually a mis-mapped "
                "source column.", _examples(too_big), precision=int(prec))
            if scale is not None:
                decs = nums.str.split(".").str[1].fillna("").str.rstrip("0")
                over_scale = nums[decs.str.len() > int(scale)]
                add(RULE_SCALE, SEV_WARNING, len(over_scale),
                    f"{len(over_scale)} value(s) have more than {scale} decimal "
                    f"place(s); Oracle rounds them.",
                    "Round at source if the rounding is material.",
                    _examples(over_scale), scale=int(scale))

    # ── DATE ──
    if dt.startswith("date") or dt == "timestamp":
        mask = field.get("format_mask") or "YYYY/MM/DD"
        py = _py_date_format(mask)

        def _ok(s: str) -> bool:
            try:
                datetime.strptime(str(s).strip(), py)
                return True
            except (ValueError, TypeError):
                return False

        bad = filled[~filled.map(_ok)]
        add(RULE_DATE_FORMAT, SEV_ERROR, len(bad),
            f"{len(bad)} date(s) are not in {mask}.",
            f"Reformat to {mask} with a DATE_FORMAT rule before loading.",
            _examples(bad), format_mask=mask)

    # ── Value set ──
    allowed = [str(a.get("code") if isinstance(a, dict) else a)
               for a in (field.get("allowed_values") or [])]
    allowed = [a for a in allowed if a.strip()]
    if allowed:
        ok = {a.strip().lower() for a in allowed}
        bad = filled[~filled.str.strip().str.lower().isin(ok)]
        add(RULE_VALUE_SET, SEV_ERROR, len(bad),
            f"{len(bad)} value(s) are outside the codes Oracle accepts "
            f"({', '.join(allowed[:8])}{'…' if len(allowed) > 8 else ''}).",
            "Add a value crosswalk to an accepted code, or correct the source value.",
            _examples(bad), allowed_values=allowed[:40])
    return findings


def check_frame(df: pd.DataFrame, fields: list[dict]) -> dict:
    """Every column rule violated by one finished sheet frame.

    Fields are matched to columns on a normalised name, because the frame may carry
    Oracle's header label (with its '*' marker) while the field record holds the
    clean name.
    """
    if df is None or not len(getattr(df, "columns", [])):
        return {"findings": [], "rows": 0, "columns_checked": 0,
                "columns_with_rules": 0, "blocked": False}
    by_norm = {_n(c): c for c in df.columns}
    findings: list[dict] = []
    checked = with_rules = 0
    for f in fields or []:
        col = by_norm.get(_n(f.get("field_name")))
        if col is None:
            continue
        checked += 1
        if _has_rule(f):
            with_rules += 1
        findings += check_column(df[col], f)
    findings.sort(key=lambda x: (0 if x["blocking"] else 1,
                                 0 if x["severity"] == SEV_ERROR else 1,
                                 -x["count"]))
    return {
        "findings": findings,
        "rows": int(len(df)),
        "columns_checked": checked,
        # Honest denominator: a column Oracle said nothing about was not "checked
        # and clean", it was not checkable. Saying otherwise is how a template with
        # no comments at all (the bundled Item workbook) would read as fully verified.
        "columns_with_rules": with_rules,
        "blocked": any(f["blocking"] for f in findings),
    }


def _has_rule(f: dict) -> bool:
    return bool(f.get("required") or f.get("max_length") or f.get("precision")
                or f.get("allowed_values") or f.get("do_not_populate")
                or str(f.get("data_type") or "").lower().startswith("date"))


def summarize(per_sheet: dict[str, dict]) -> dict:
    """Roll several sheets' results into the shape the review screen shows."""
    findings, sheets = [], []
    rows_checked = cols_checked = cols_with_rules = 0
    for sheet, res in (per_sheet or {}).items():
        for f in res.get("findings", []):
            findings.append({**f, "sheet": sheet})
        rows_checked = max(rows_checked, res.get("rows", 0))
        cols_checked += res.get("columns_checked", 0)
        cols_with_rules += res.get("columns_with_rules", 0)
        sheets.append({
            "sheet": sheet, "rows": res.get("rows", 0),
            "columns_checked": res.get("columns_checked", 0),
            "columns_with_rules": res.get("columns_with_rules", 0),
            "findings": len(res.get("findings", [])),
            "blocked": bool(res.get("blocked")),
        })
    findings.sort(key=lambda x: (0 if x["blocking"] else 1,
                                 0 if x["severity"] == SEV_ERROR else 1,
                                 -x["count"]))
    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    errors = [f for f in findings if f["severity"] == SEV_ERROR]
    return {
        "findings": findings,
        "sheets": sheets,
        "rows_checked": rows_checked,
        "columns_checked": cols_checked,
        "columns_with_rules": cols_with_rules,
        "error_count": len(errors),
        "warning_count": len(findings) - len(errors),
        "by_rule": [{"rule": k, "count": v}
                    for k, v in sorted(by_rule.items(), key=lambda kv: -kv[1])],
        "blocked": any(f["blocking"] for f in findings),
        "message": _message(findings, cols_with_rules, cols_checked),
    }


def _message(findings: list[dict], with_rules: int, checked: int) -> str:
    if not checked:
        return "No output columns to check yet — generate the file first."
    if not with_rules:
        return (f"None of the {checked} output column(s) carry a rule from the template. "
                "This template has no header comments, so Oracle publishes nothing to "
                "check against — upload the Oracle workbook for this object to get them.")
    scope = f"Checked {with_rules} column rule(s) taken from the template's own comments"
    if not findings:
        return f"{scope}. Nothing violates them."
    blocking = [f for f in findings if f["blocking"]]
    errs = [f for f in findings if f["severity"] == SEV_ERROR]
    head = (f"{len(blocking)} mandatory column(s) have missing values"
            if blocking else f"{len(errs)} column(s) hold values Oracle will reject"
            if errs else f"{len(findings)} column(s) need a look")
    return f"{scope}. {head}."
