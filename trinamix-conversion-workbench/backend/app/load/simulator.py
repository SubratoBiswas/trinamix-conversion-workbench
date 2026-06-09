"""Load simulator.

Takes a converted-output rowset + active validation issues and produces a
realistic Fusion-style load result: per-row pass/fail/warning, error categories,
root causes, and dependency-impact mapping.

Also supports cross-conversion cascade: if `upstream_failed_keys` is provided
(e.g. Item numbers that failed in the upstream Item Master load), any rows in
the current conversion that reference those keys are marked failed with a
"Missing Dependency" category — visualises the cascade in the Error Traceback
view.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


_DEPENDENCY_HINTS = {
    "Inventory Item Name": ("Item Master", "ensure UOM and Item Class loaded first"),
    "InventoryItemNumber": ("Item Master", "ensure Item Master loaded first"),
    "Item Number":         ("Item Master", "ensure Item Master loaded first"),
    "Customer":            ("Customer Master", "ensure Customer hierarchy loaded first"),
    "Supplier":            ("Supplier Master", "ensure Supplier loaded first"),
    "Organization Code":   ("Inventory Org", "ensure Inventory Org defined in Fusion"),
    "ShipFromOrgCode":     ("Inventory Org", "ensure Inventory Org defined in Fusion"),
    "Unit of Measure":     ("UOM", "ensure UOM codes seeded"),
    "Unit of Measure Code":("UOM", "ensure UOM codes seeded"),
    "UnitOfMeasureCode":   ("UOM", "ensure UOM codes seeded"),
}


def _categorise(issue: dict[str, Any]) -> str:
    t = (issue.get("issue_type") or "").lower()
    if "required" in t:
        return "Missing Required Field"
    if "format" in t or "date" in t or "number" in t:
        return "Invalid Format"
    if "lookup" in t:
        return "Invalid Lookup"
    if "duplicate" in t:
        return "Duplicate Record"
    if "dependency" in t:
        return "Missing Dependency"
    if "transform" in t:
        return "Transformation Error"
    return "Data Quality Warning"


def _root_cause(issue: dict[str, Any]) -> tuple[str, str | None]:
    fname = issue.get("field_name") or ""
    for key, (dep, hint) in _DEPENDENCY_HINTS.items():
        if key.lower() in fname.lower():
            return (hint, dep)
    if "required" in (issue.get("issue_type") or "").lower():
        return ("Source did not provide required value; configure default or remap.", None)
    if "format" in (issue.get("issue_type") or "").lower():
        return ("Source format does not match Fusion expectation; add transformation rule.", None)
    return ("Generic data-quality issue.", None)


def simulate_load(
    converted_rows: list[dict[str, Any]],
    validation_issues: list[dict[str, Any]],
    upstream_failed_keys: dict[str, set[str]] | None = None,
    key_field_by_dependency: dict[str, str] | None = None,
    dependency_failure_kinds: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a dict with: total, passed, failed, warning, errors[], categories, root_causes.

    Args:
        converted_rows: post-transformation rows we'd ship to Fusion.
        validation_issues: validation findings (already collected).
        upstream_failed_keys: mapping of dependency_object -> set of keys that
            failed in the upstream conversion. e.g. {"Item": {"ITM-X", "ITM-Y"}}.
        key_field_by_dependency: which column in `converted_rows` carries the
            reference key for each dependency. e.g. {"Item": "InventoryItemNumber"}.
        dependency_failure_kinds: per dependency, why the keys are bad —
            ``"failed_load"`` (sibling load failed) or ``"unresolved_reference"``
            (the upstream conversion has no record with this key at all).
            Drives the user-facing error wording.
    """
    total = len(converted_rows)
    failed_rows: set[int] = set()
    warning_rows: set[int] = set()

    errors: list[dict[str, Any]] = []
    cat_counter: Counter = Counter()
    cause_counter: Counter = Counter()
    dep_counter: Counter = Counter()

    for issue in validation_issues:
        sev = (issue.get("severity") or "warning").lower()
        rownum = issue.get("row_number")
        category = _categorise(issue)
        cat_counter[category] += 1
        cause_text, dep = _root_cause(issue)
        cause_counter[cause_text] += 1
        if dep:
            dep_counter[dep] += 1

        if sev in ("error", "critical"):
            if rownum is not None:
                failed_rows.add(rownum)
        elif sev == "warning":
            if rownum is not None:
                warning_rows.add(rownum)

        errors.append({
            "row_number": rownum,
            "object_name": issue.get("field_name"),
            "error_category": category,
            "error_message": issue.get("message"),
            "root_cause": cause_text,
            "related_dependency": dep,
            "reference_value": None,
            "suggested_fix": issue.get("suggested_fix"),
        })

    # ── Cross-conversion cascade ──
    # Walk the converted rows and find references to upstream failed keys.
    kinds = dependency_failure_kinds or {}
    if upstream_failed_keys and key_field_by_dependency:
        for row_idx, row in enumerate(converted_rows, start=1):
            for dep_object, failed_keys in upstream_failed_keys.items():
                key_field = key_field_by_dependency.get(dep_object)
                if not key_field:
                    continue
                ref = row.get(key_field)
                if ref and str(ref) in failed_keys:
                    failed_rows.add(row_idx)
                    cat_counter["Missing Dependency"] += 1
                    dep_counter[dep_object] += 1
                    is_unresolved = kinds.get(dep_object) == "unresolved_reference"
                    if is_unresolved:
                        message = (
                            f"{dep_object} reference '{ref}' has no matching record in "
                            f"the upstream {dep_object} master — load will reject this row."
                        )
                        cause_text = (
                            f"Unresolved reference: row points to {dep_object} key "
                            f"'{ref}', which is absent from the {dep_object} source dataset."
                        )
                        suggested_fix = (
                            f"Either add '{ref}' to the upstream {dep_object} extract, or "
                            f"clean the reference here (correct typo / map to a valid key)."
                        )
                    else:
                        message = (
                            f"{dep_object} reference '{ref}' did not load successfully — "
                            f"this row cannot be inserted until upstream remediation."
                        )
                        cause_text = (
                            f"Cascade failure: upstream {dep_object} conversion "
                            f"left key '{ref}' in failed state."
                        )
                        suggested_fix = (
                            f"Re-run the {dep_object} conversion after fixing the "
                            f"underlying issue, then re-load this object."
                        )
                    cause_counter[cause_text] += 1
                    errors.append({
                        "row_number": row_idx,
                        "object_name": key_field,
                        "error_category": "Missing Dependency",
                        "error_message": message,
                        "root_cause": cause_text,
                        "related_dependency": dep_object,
                        "reference_value": str(ref),
                        "suggested_fix": suggested_fix,
                    })

    failed = len(failed_rows)
    warning = len(warning_rows - failed_rows)
    passed = max(0, total - failed - warning)

    return {
        "total_records": total,
        "passed_count": passed,
        "failed_count": failed,
        "warning_count": warning,
        "error_count": len(errors),
        "errors": errors,
        "categories": [{"name": k, "count": v} for k, v in cat_counter.most_common()],
        "root_causes": [{"cause": k, "count": v} for k, v in cause_counter.most_common()],
        "dependencies": [{"object": k, "count": v} for k, v in dep_counter.most_common()],
    }
