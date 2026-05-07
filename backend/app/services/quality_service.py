"""Cleansing, validation, and load orchestration services."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.fbdi import FBDIField
from app.models.load import LoadError, LoadRun
from app.models.mapping import MappingSuggestion
from app.models.conversion import Conversion
from app.models.validation import ValidationIssue
from app.parsers import parse_tabular, profile_dataframe
from app.services.output_service import build_converted_dataframe
from app.validation import run_cleansing_checks, run_validation_checks
from app.load import simulate_load


def run_cleansing(db: Session, conversion: Conversion) -> list[ValidationIssue]:
    df = parse_tabular(conversion.dataset.file_path, file_type=conversion.dataset.file_type)
    profiles = profile_dataframe(df)
    fields_by_id: dict[int, FBDIField] = {f.id: f for f in conversion.template.fields}
    mappings = (
        db.query(MappingSuggestion).filter(MappingSuggestion.conversion_id == conversion.id).all()
    )
    mapping_dicts: list[dict[str, Any]] = []
    for m in mappings:
        f = fields_by_id.get(m.target_field_id)
        mapping_dicts.append(
            {
                "source_column": m.source_column,
                "target_field_name": f.field_name if f else None,
                "target_required": bool(f.required) if f else False,
                "status": m.status,
                "confidence": m.confidence,
            }
        )
    raw_issues = run_cleansing_checks(df, profiles, mapping_dicts)

    # Replace existing cleansing issues for project
    db.query(ValidationIssue).filter(
        ValidationIssue.conversion_id == conversion.id,
        ValidationIssue.category == "cleansing",
    ).delete()
    saved: list[ValidationIssue] = []
    for issue in raw_issues:
        v = ValidationIssue(conversion_id=conversion.id, **issue)
        db.add(v)
        saved.append(v)
    db.commit()
    return saved


def run_validation(db: Session, conversion: Conversion) -> list[ValidationIssue]:
    df, _ = build_converted_dataframe(db, conversion)
    converted_rows = df.fillna("").to_dict(orient="records")
    fields_by_id: dict[int, FBDIField] = {f.id: f for f in conversion.template.fields}
    target_meta: list[dict[str, Any]] = []
    for f in fields_by_id.values():
        if f.field_name in df.columns:
            target_meta.append(
                {
                    "field_name": f.field_name,
                    "required": bool(f.required),
                    "data_type": f.data_type,
                    "max_length": f.max_length,
                    "format_mask": f.format_mask,
                }
            )
    raw_issues = run_validation_checks(converted_rows, target_meta)

    db.query(ValidationIssue).filter(
        ValidationIssue.conversion_id == conversion.id,
        ValidationIssue.category == "validation",
    ).delete()
    saved: list[ValidationIssue] = []
    for issue in raw_issues:
        v = ValidationIssue(conversion_id=conversion.id, **issue)
        db.add(v)
        saved.append(v)
    conversion.status = "validated"
    conversion.updated_at = datetime.utcnow()
    db.commit()
    return saved


# Reference fields on the current conversion that hold the FK for each
# upstream business object. Module-level so the unresolved-reference path
# can reuse it.
_REF_FIELDS_BY_OBJECT: dict[str, list[str]] = {
    "Item":     ["InventoryItemNumber", "Item Number", "ItemNumber"],
    "Customer": ["CustomerNumber", "Customer"],
    "Supplier": ["SupplierNumber", "Supplier"],
    "UOM":      ["UnitOfMeasureCode", "Unit of Measure Code", "UOM"],
}

# Source-side primary key columns on the upstream conversion's *raw* dataset.
# Used to enumerate the universe of valid keys to compare against.
_UPSTREAM_SOURCE_KEYS: dict[str, list[str]] = {
    "Item":     ["ITEM_NUM", "ItemNumber", "InventoryItemNumber"],
    "Customer": ["CUSTOMER_NUM", "CustomerNumber"],
    "Supplier": ["SUPPLIER_NUM", "SupplierNumber"],
    "UOM":      ["UOM_CD", "UnitOfMeasureCode"],
}

_KEY_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_key(v: str | None) -> str:
    """Loose comparison key for cross-conversion reference matching: strips
    case + punctuation so post-transform values like ``"ITM01053D"`` still
    match the upstream raw key ``"ITM-01053-D"``. Without this, any auto-
    suggested REMOVE_HYPHEN/UPPERCASE transform on the FK column would make
    every reference look unresolved.
    """
    if v is None:
        return ""
    return _KEY_NORMALIZE_RE.sub("", str(v).lower())


def _build_upstream_failure_context(
    db: Session, conversion: Conversion
) -> tuple[dict[str, set[str]], dict[str, str], dict[str, str]]:
    """Build cross-conversion cascade context for the load simulator.

    Two complementary signals are folded into one ``upstream_failed_keys``
    map:

    1. **Failed in upstream load** — sibling conversions in the same project
       with ``status="failed"`` and a recorded LoadRun: their failed source
       keys cascade down.
    2. **Unresolved reference** — the current conversion's converted output
       refers to keys that are *not present* in the upstream conversion's
       raw source dataset. This catches data-alignment problems even when
       the upstream load hasn't been simulated yet (the typical demo state).

    Returns:
        upstream_failed_keys: {dependency_label: {failed/unresolved keys}}
        key_field_by_dependency: {dependency_label: ref column on current output}
        failure_kinds: {dependency_label: "failed_load" | "unresolved_reference"}
            tells the simulator which message to emit.
    """
    if not conversion.project_id:
        return {}, {}, {}

    upstream: dict[str, set[str]] = {}
    key_fields: dict[str, str] = {}
    kinds: dict[str, str] = {}

    siblings = (
        db.query(Conversion)
        .filter(
            Conversion.project_id == conversion.project_id,
            Conversion.id != conversion.id,
        )
        .all()
    )

    # Current conversion's converted output (for reference column detection
    # and unresolved-reference checks). Cached so we read it once.
    try:
        df_curr, _ = build_converted_dataframe(db, conversion)
    except Exception:
        df_curr = None

    for s in siblings:
        if not s.target_object or not s.dataset_id:
            continue

        # Resolve which column on the current converted output references
        # this sibling's business object.
        ref_field_candidates = _REF_FIELDS_BY_OBJECT.get(s.target_object, [])
        ref_col = None
        if df_curr is not None:
            ref_col = next(
                (c for c in ref_field_candidates if c in df_curr.columns), None
            )
        if not ref_col:
            continue

        failed_keys: set[str] = set()
        kind: str | None = None

        # Path 1: Sibling explicitly failed in a prior simulated load.
        if s.status == "failed":
            try:
                sib_df, _ = build_converted_dataframe(db, s)
                from app.models.load import LoadError, LoadRun
                latest_run = (
                    db.query(LoadRun)
                    .filter(LoadRun.conversion_id == s.id)
                    .order_by(LoadRun.started_at.desc())
                    .first()
                )
                if latest_run is not None:
                    failed_row_nums = {
                        e.row_number for e in db.query(LoadError)
                        .filter(LoadError.load_run_id == latest_run.id)
                        .all() if e.row_number is not None
                    }
                    sib_key_col = next(
                        (c for c in _UPSTREAM_SOURCE_KEYS.get(s.target_object, [])
                         if c in sib_df.columns),
                        None,
                    )
                    if sib_key_col:
                        for ridx, row in enumerate(sib_df.itertuples(index=False), start=1):
                            if ridx in failed_row_nums:
                                val = getattr(row, sib_key_col, None)
                                if val:
                                    failed_keys.add(str(val))
                                    kind = "failed_load"
            except Exception:
                pass

        # Path 2: Refs in the current conversion that aren't in the upstream
        # source dataset at all. Catches the demo case where the user runs
        # the SO simulation without first running Item Master.
        if df_curr is not None:
            try:
                from app.parsers import parse_tabular
                up_df = parse_tabular(s.dataset.file_path, file_type=s.dataset.file_type)
                up_key_col = next(
                    (c for c in _UPSTREAM_SOURCE_KEYS.get(s.target_object, [])
                     if c in up_df.columns),
                    None,
                )
                if up_key_col:
                    upstream_universe_norm = {
                        _normalize_key(v) for v in up_df[up_key_col].dropna().astype(str).tolist()
                    }
                    upstream_universe_norm.discard("")
                    seen_in_curr = [
                        str(v) for v in df_curr[ref_col].dropna().astype(str).tolist()
                        if str(v).strip() != ""
                    ]
                    unresolved = {
                        v for v in seen_in_curr
                        if _normalize_key(v) not in upstream_universe_norm
                    }
                    if unresolved:
                        failed_keys |= unresolved
                        kind = kind or "unresolved_reference"
            except Exception:
                pass

        if failed_keys:
            upstream[s.target_object] = failed_keys
            key_fields[s.target_object] = ref_col
            kinds[s.target_object] = kind or "failed_load"

    return upstream, key_fields, kinds


def simulate_conversion_load(db: Session, conversion: Conversion) -> LoadRun:
    df, _lineage = build_converted_dataframe(db, conversion)
    converted = df.fillna("").to_dict(orient="records")
    issues = (
        db.query(ValidationIssue)
        .filter(ValidationIssue.conversion_id == conversion.id)
        .all()
    )
    issue_dicts = [
        {
            "issue_type": i.issue_type,
            "severity": i.severity,
            "row_number": i.row_number,
            "field_name": i.field_name,
            "message": i.message,
            "suggested_fix": i.suggested_fix,
        }
        for i in issues
    ]

    upstream_failed, key_fields, kinds = _build_upstream_failure_context(db, conversion)
    result = simulate_load(
        converted, issue_dicts,
        upstream_failed_keys=upstream_failed,
        key_field_by_dependency=key_fields,
        dependency_failure_kinds=kinds,
    )

    run = LoadRun(
        conversion_id=conversion.id,
        run_type="simulate",
        status="completed",
        total_records=result["total_records"],
        passed_count=result["passed_count"],
        failed_count=result["failed_count"],
        warning_count=result["warning_count"],
        error_count=result["error_count"],
        completed_at=datetime.utcnow(),
    )
    db.add(run)
    db.flush()
    for e in result["errors"]:
        db.add(LoadError(load_run_id=run.id, **e))
    conversion.status = "loaded" if result["failed_count"] == 0 else "failed"
    conversion.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run


def build_load_summary(db: Session, conversion: Conversion) -> dict[str, Any]:
    """Aggregate the latest load run into a dashboard-ready summary."""
    latest = (
        db.query(LoadRun)
        .filter(LoadRun.conversion_id == conversion.id)
        .order_by(LoadRun.started_at.desc())
        .first()
    )
    if not latest:
        return {
            "total_records": 0,
            "passed_count": 0,
            "failed_count": 0,
            "warning_count": 0,
            "error_count": 0,
            "error_categories": [],
            "root_causes": [],
            "dependency_impacts": [],
        }
    errors = (
        db.query(LoadError).filter(LoadError.load_run_id == latest.id).all()
    )
    cat: dict[str, int] = {}
    cause: dict[str, int] = {}
    dep: dict[str, int] = {}
    for e in errors:
        if e.error_category:
            cat[e.error_category] = cat.get(e.error_category, 0) + 1
        if e.root_cause:
            cause[e.root_cause] = cause.get(e.root_cause, 0) + 1
        if e.related_dependency:
            dep[e.related_dependency] = dep.get(e.related_dependency, 0) + 1
    return {
        "total_records": latest.total_records,
        "passed_count": latest.passed_count,
        "failed_count": latest.failed_count,
        "warning_count": latest.warning_count,
        "error_count": latest.error_count,
        "error_categories": [{"name": k, "count": v} for k, v in sorted(cat.items(), key=lambda x: -x[1])],
        "root_causes": [{"cause": k, "count": v} for k, v in sorted(cause.items(), key=lambda x: -x[1])],
        "dependency_impacts": [{"object": k, "count": v} for k, v in sorted(dep.items(), key=lambda x: -x[1])],
    }
