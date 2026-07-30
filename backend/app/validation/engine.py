"""Cleansing & validation checks.

Cleansing operates on the SOURCE dataset (before mapping is finalised) and
flags general data-quality issues. Validation operates on the CONVERTED output
and confirms each row is FBDI-ready (required fields populated, types/lengths
match, dates parseable, etc.).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import pandas as pd


# -------------------- CLEANSING --------------------

def _is_numeric(v: str) -> bool:
    return bool(re.match(r"^-?\d+(?:\.\d+)?$", v.strip()))


def _to_num(v: str):
    s = (v or "").strip().replace(",", "")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# Field-name hints. Text fields that must NOT hold a bare number (Item Class Name,
# description, …); numeric fields that must NOT go negative (quantities, on-hand).
_TEXT_NAME_HINTS = ("name", "description", "desc")
_NONNEG_HINTS = ("quantity", "qty", "on hand", "onhand", "stock", "weight",
                 "volume", "count", "min order", "max order", "lot size")


def _allowed_value_set(meta: dict[str, Any]) -> set[str] | None:
    """Accepted enumerated values for a field (codes + meanings, lower-cased), or
    None when the field isn't a value-set/LOV column or its codes aren't known."""
    av = meta.get("allowed_values") or []
    if not av:
        return None
    out: set[str] = set()
    for a in av:
        for k in ("code", "meaning", "value"):
            val = a.get(k) if isinstance(a, dict) else None
            if val is not None and str(val).strip():
                out.add(str(val).strip().lower())
    return out or None


def _minmax_pairs(by_name: dict[str, dict]) -> list[tuple[str, str]]:
    """Detect (minimum_field, maximum_field) pairs sharing a base name, e.g.
    'Minimum Order Quantity' / 'Maximum Order Quantity'."""
    mins: dict[str, str] = {}
    maxs: dict[str, str] = {}
    for fname in by_name:
        n = _norm_name(fname)
        if n.startswith("minimum ") or n.startswith("min "):
            mins[n.split(" ", 1)[1]] = fname
        elif n.startswith("maximum ") or n.startswith("max "):
            maxs[n.split(" ", 1)[1]] = fname
    return [(mins[b], maxs[b]) for b in mins if b in maxs]


def _is_date(v: str) -> bool:
    s = v.strip()
    return any(re.match(p, s) for p in (
        r"^\d{4}-\d{2}-\d{2}$",
        r"^\d{2}/\d{2}/\d{4}$",
        r"^\d{4}/\d{2}/\d{2}$",
        r"^\d{8}$",
    ))


def run_cleansing_checks(
    df: pd.DataFrame,
    profiles: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a list of cleansing-issue dicts.

    A `mapping` here is a dict with at least: source_column, target_field_name,
    target_required, status, confidence.
    """
    issues: list[dict[str, Any]] = []
    profile_by_col = {p["column_name"]: p for p in profiles}
    mapped_sources = {m.get("source_column") for m in mappings if m.get("source_column")}

    # 1. Unmapped required target fields
    for m in mappings:
        if m.get("target_required") and not m.get("source_column") and m.get("status") != "approved":
            issues.append({
                "category": "cleansing",
                "issue_type": "Unmapped Required Field",
                "field_name": m.get("target_field_name"),
                "severity": "error",
                "message": f"Required FBDI field '{m.get('target_field_name')}' has no mapped source column.",
                "suggested_fix": "Pick a source column or supply a default value.",
                "auto_fixable": False,
                "impacted_count": len(df),
            })

    # 2. Low confidence mappings
    for m in mappings:
        conf = m.get("confidence") or 0.0
        if m.get("source_column") and conf < 0.5 and m.get("status") not in ("approved", "rejected", "not_applicable"):
            issues.append({
                "category": "cleansing",
                "issue_type": "Low Confidence Mapping",
                "field_name": m.get("target_field_name"),
                "severity": "warning",
                "message": f"Mapping {m.get('source_column')} → {m.get('target_field_name')} confidence {int(conf*100)}%.",
                "suggested_fix": "Review and approve manually, or override the source column.",
                "auto_fixable": False,
                "impacted_count": 1,
            })

    # 3. Per-column data-quality checks
    for col in df.columns:
        prof = profile_by_col.get(col)
        if not prof:
            continue

        series = df[col].astype(str).fillna("")
        non_empty = [v for v in series.tolist() if v.strip() != ""]
        empty = len(series) - len(non_empty)

        # Null-heavy
        if empty / max(len(series), 1) > 0.5:
            issues.append({
                "category": "cleansing",
                "issue_type": "Null-Heavy Column",
                "field_name": col,
                "severity": "warning",
                "message": f"{col} is {round(empty/len(series)*100,1)}% empty.",
                "suggested_fix": "Apply DEFAULT_VALUE rule, or drop column from mapping.",
                "auto_fixable": True,
                "impacted_count": empty,
            })

        # Leading/trailing spaces
        whitespace_n = sum(1 for v in non_empty if v != v.strip())
        if whitespace_n:
            issues.append({
                "category": "cleansing",
                "issue_type": "Leading/Trailing Spaces",
                "field_name": col,
                "severity": "info",
                "message": f"{col} has {whitespace_n} value(s) with surrounding whitespace.",
                "suggested_fix": "Apply TRIM rule.",
                "auto_fixable": True,
                "impacted_count": whitespace_n,
            })

        # Inconsistent casing on short codes (UOM-like)
        if non_empty and all(len(v) <= 5 for v in non_empty[:50]):
            cased = sum(1 for v in non_empty if any(ch.islower() for ch in v))
            if 0 < cased < len(non_empty):
                issues.append({
                    "category": "cleansing",
                    "issue_type": "Inconsistent Casing",
                    "field_name": col,
                    "severity": "info",
                    "message": f"{col} has mixed-case values in a short-code column.",
                    "suggested_fix": "Apply UPPERCASE rule.",
                    "auto_fixable": True,
                    "impacted_count": cased,
                })

        # Invalid date / number patterns
        if prof.get("inferred_type") == "date":
            bad_dates = [i for i, v in enumerate(non_empty) if not _is_date(v)]
            if bad_dates:
                issues.append({
                    "category": "cleansing",
                    "issue_type": "Invalid Date Format",
                    "field_name": col,
                    "severity": "error",
                    "message": f"{col} has {len(bad_dates)} value(s) that don't match common date patterns.",
                    "suggested_fix": "Add DATE_FORMAT rule with explicit input_format.",
                    "auto_fixable": False,
                    "impacted_count": len(bad_dates),
                })

        # Duplicate keys when column name suggests an identifier
        if any(k in col.lower() for k in ("id", "number", "key", "code")) and prof.get("distinct_count") and prof.get("distinct_count", 0) < len(non_empty):
            dup_n = len(non_empty) - prof["distinct_count"]
            if dup_n > 0:
                issues.append({
                    "category": "cleansing",
                    "issue_type": "Duplicate Key Values",
                    "field_name": col,
                    "severity": "warning",
                    "message": f"{col} appears to be a key but has {dup_n} duplicate value(s).",
                    "suggested_fix": "De-duplicate before load or treat as non-unique.",
                    "auto_fixable": False,
                    "impacted_count": dup_n,
                })

    return issues


# -------------------- VALIDATION (post-conversion) --------------------

def run_validation_checks(
    converted_rows: list[dict[str, Any]],
    target_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate converted rows against FBDI field metadata.

    `target_fields` items: {field_name, required, data_type, max_length, format_mask}
    """
    issues: list[dict[str, Any]] = []
    by_name = {f["field_name"]: f for f in target_fields}
    minmax = _minmax_pairs(by_name)

    for idx, row in enumerate(converted_rows, start=1):
        for fname, meta in by_name.items():
            v = row.get(fname)
            sval = "" if v is None else str(v)
            stripped = sval.strip()

            if meta.get("required") and stripped == "":
                issues.append({
                    "category": "validation",
                    "row_number": idx,
                    "field_name": fname,
                    "issue_type": "Missing Required Field",
                    "severity": "error",
                    "message": f"Row {idx}: required field '{fname}' is empty.",
                    "suggested_fix": "Provide source column or default value.",
                    "auto_fixable": False,
                    "impacted_count": 1,
                })
                continue

            if stripped == "":
                continue

            dt = (meta.get("data_type") or "").lower()
            ml = meta.get("max_length")
            if ml and len(sval) > ml:
                issues.append({
                    "category": "validation",
                    "row_number": idx,
                    "field_name": fname,
                    # Spelled to match generate_dq's own check and its HARD_ERROR_TYPES
                    # set. It used to read "Max Length Exceeded" while both of those
                    # said "Exceeds Max Length", so a value too long for its column
                    # was never counted as a hard error and never got its
                    # plain-English explanation — two names for one thing.
                    "issue_type": "Exceeds Max Length",
                    "severity": "error",
                    "message": f"Row {idx}: '{fname}' length {len(sval)} > max {ml}.",
                    "suggested_fix": "Truncate or shorten value.",
                    "auto_fixable": False,
                    "impacted_count": 1,
                })

            nname = _norm_name(fname)

            # ── Rules transcribed from the template's own header comment ──────
            # Oracle states these in the tooltip on each header cell
            # ("BATCH_ID / NOT NULL / NUMBER (18)"); template_comments.py mines them.
            # NOT NULL is folded into `required` at parse time, so what remains here
            # is precision, scale, and the explicit do-not-populate instruction.
            if meta.get("do_not_populate"):
                issues.append({
                    "category": "validation", "row_number": idx, "field_name": fname,
                    "issue_type": "Column Oracle Says Not To Populate",
                    "severity": "warning",
                    "message": f"Row {idx}: '{fname}' has a value, but the template says "
                               f"this column is not used and no value should be provided.",
                    "suggested_fix": "Mark the field Not applicable so it ships blank, or "
                                     "confirm with the functional team that this interface "
                                     "column really is in use.",
                    "auto_fixable": True, "impacted_count": 1,
                })

            prec, scale = meta.get("precision"), meta.get("scale")
            if prec and _is_numeric(sval):
                digits = stripped.lstrip("+-").replace(",", "")
                int_part, _, dec_part = digits.partition(".")
                int_digits = len(int_part.lstrip("0"))
                if int_digits > prec - (scale or 0):
                    issues.append({
                        "category": "validation", "row_number": idx, "field_name": fname,
                        "issue_type": "Numeric Precision Exceeded", "severity": "error",
                        "message": f"Row {idx}: '{fname}' = '{sval}' needs {int_digits} digits "
                                   f"before the decimal point but the column is "
                                   f"NUMBER({prec}{',' + str(scale) if scale else ''}).",
                        "suggested_fix": "Oracle rejects the row. Check the source column — a "
                                         "number this long here is usually a mis-map.",
                        "auto_fixable": False, "impacted_count": 1,
                    })
                elif scale is not None and dec_part and len(dec_part.rstrip("0")) > scale:
                    issues.append({
                        "category": "validation", "row_number": idx, "field_name": fname,
                        "issue_type": "Too Many Decimal Places", "severity": "warning",
                        "message": f"Row {idx}: '{fname}' = '{sval}' has "
                                   f"{len(dec_part.rstrip('0'))} decimal place(s); the column is "
                                   f"NUMBER({prec},{scale}), so Oracle rounds it.",
                        "suggested_fix": "Round at source if the rounding matters.",
                        "auto_fixable": True, "impacted_count": 1,
                    })

            if "number" in dt or "decimal" in dt or "integer" in dt:
                if not _is_numeric(sval):
                    issues.append({
                        "category": "validation",
                        "row_number": idx,
                        "field_name": fname,
                        "issue_type": "Invalid Number Format",
                        "severity": "error",
                        "message": f"Row {idx}: '{fname}' = '{sval}' is not numeric.",
                        "suggested_fix": "Apply NUMBER_FORMAT rule or fix source.",
                        "auto_fixable": False,
                        "impacted_count": 1,
                    })
                else:
                    # Negative value in a field that can't be negative (quantities,
                    # on-hand, weight, min/max order). Catches bad source/transform.
                    num = _to_num(sval)
                    if num is not None and num < 0 and any(h in nname for h in _NONNEG_HINTS):
                        issues.append({
                            "category": "validation", "row_number": idx, "field_name": fname,
                            "issue_type": "Negative Value Not Allowed", "severity": "error",
                            "message": f"Row {idx}: '{fname}' = {sval} is negative; this field must be >= 0.",
                            "suggested_fix": "Fix the source value or the transform (ABS / clamp).",
                            "auto_fixable": False, "impacted_count": 1,
                        })

            # Enumerated / value-set column: value must be an Oracle-supported code.
            allowed = _allowed_value_set(meta)
            if allowed is not None and stripped.lower() not in allowed:
                issues.append({
                    "category": "validation", "row_number": idx, "field_name": fname,
                    "issue_type": "Value Not In Value Set", "severity": "error",
                    "message": f"Row {idx}: '{fname}' = '{sval}' is not an accepted value "
                               f"(expected one of {len(allowed)} Oracle-supported codes).",
                    "suggested_fix": "Map via a value crosswalk to a supported code.",
                    "auto_fixable": False, "impacted_count": 1,
                })

            # A bare number sitting in a text 'name'/'description' field (e.g. Item
            # Class Name = 501020) — almost always a mis-map or wrong source column.
            is_text = (dt in ("", "text", "string", "char", "varchar", "varchar2")) \
                and not any(k in dt for k in ("number", "decimal", "integer", "date"))
            if is_text and _is_numeric(sval) and any(h in nname for h in _TEXT_NAME_HINTS) \
                    and "number" not in nname and "id" not in nname.split():
                issues.append({
                    "category": "validation", "row_number": idx, "field_name": fname,
                    "issue_type": "Numeric Value In Text Field", "severity": "warning",
                    "message": f"Row {idx}: '{fname}' = '{sval}' is purely numeric in a text field "
                               f"— likely a mis-mapped source column.",
                    "suggested_fix": "Re-check the source column mapped to this field.",
                    "auto_fixable": False, "impacted_count": 1,
                })

            if dt == "date":
                fmt = meta.get("format_mask") or "%Y/%m/%d"
                py_fmt = (
                    fmt.replace("YYYY", "%Y").replace("MM", "%m").replace("DD", "%d")
                )
                try:
                    datetime.strptime(sval, py_fmt)
                except ValueError:
                    issues.append({
                        "category": "validation",
                        "row_number": idx,
                        "field_name": fname,
                        "issue_type": "Invalid Date Format",
                        "severity": "error",
                        "message": f"Row {idx}: '{fname}' = '{sval}' does not match {fmt}.",
                        "suggested_fix": f"Apply DATE_FORMAT rule with output_format={py_fmt}.",
                        "auto_fixable": False,
                        "impacted_count": 1,
                    })

    # Related-field business logic: a Minimum must not exceed its Maximum (e.g.
    # Minimum Order Quantity <= Maximum Order Quantity) — catches both being blindly
    # set to the same or reversed source.
    for min_f, max_f in minmax:
        for idx, row in enumerate(converted_rows, start=1):
            a = _to_num(str(row.get(min_f, "") or ""))
            b = _to_num(str(row.get(max_f, "") or ""))
            if a is not None and b is not None and a > b:
                issues.append({
                    "category": "validation", "row_number": idx, "field_name": max_f,
                    "issue_type": "Min Greater Than Max", "severity": "error",
                    "message": f"Row {idx}: '{min_f}' ({a:g}) exceeds '{max_f}' ({b:g}).",
                    "suggested_fix": "Correct the source values or the mapping for these two fields.",
                    "auto_fixable": False, "impacted_count": 1,
                })
    return issues
