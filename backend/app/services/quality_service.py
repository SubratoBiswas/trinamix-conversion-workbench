"""Cleansing, validation, and load orchestration services (async/Beanie)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from beanie import PydanticObjectId

from app.models.fbdi import FBDIField
from app.models.load import LoadError, LoadRun
from app.models.mapping import MappingSuggestion
from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.validation import ValidationIssue
from app.parsers import parse_tabular, profile_dataframe
from app.services.output_service import build_converted_dataframe
from app.validation import run_cleansing_checks, run_validation_checks
from app.load import simulate_load


async def run_cleansing(conversion: Conversion) -> list[ValidationIssue]:
    dataset = await Dataset.get(conversion.dataset_id)
    df = parse_tabular(dataset.file_path, file_type=dataset.file_type)
    profiles = profile_dataframe(df)
    fields = await FBDIField.find(FBDIField.template_id == conversion.template_id).to_list() if conversion.template_id else []
    fields_by_id = {f.id: f for f in fields}
    mappings = await MappingSuggestion.find(MappingSuggestion.conversion_id == conversion.id).to_list()
    mapping_dicts = [
        {"source_column": m.source_column,
         "target_field_name": fields_by_id[m.target_field_id].field_name if m.target_field_id in fields_by_id else None,
         "target_required": bool(fields_by_id[m.target_field_id].required) if m.target_field_id in fields_by_id else False,
         "status": m.status, "confidence": m.confidence}
        for m in mappings
    ]
    raw_issues = run_cleansing_checks(df, profiles, mapping_dicts)
    await ValidationIssue.find({"conversion_id": conversion.id, "category": "cleansing"}).delete()
    saved = []
    for issue in raw_issues:
        v = ValidationIssue(conversion_id=conversion.id, **issue)
        await v.insert()
        saved.append(v)
    return saved


async def run_validation(conversion: Conversion) -> list[ValidationIssue]:
    df, _ = await build_converted_dataframe(conversion)
    converted_rows = df.fillna("").to_dict(orient="records")
    fields = await FBDIField.find(FBDIField.template_id == conversion.template_id).to_list() if conversion.template_id else []
    target_meta = [
        {"field_name": f.field_name, "required": bool(f.required), "data_type": f.data_type,
         "max_length": f.max_length, "format_mask": f.format_mask}
        for f in fields if f.field_name in df.columns
    ]
    raw_issues = run_validation_checks(converted_rows, target_meta)
    await ValidationIssue.find({"conversion_id": conversion.id, "category": "validation"}).delete()
    saved = []
    for issue in raw_issues:
        v = ValidationIssue(conversion_id=conversion.id, **issue)
        await v.insert()
        saved.append(v)
    await conversion.set({"status": "validated", "updated_at": datetime.utcnow()})
    return saved


_REF_FIELDS_BY_OBJECT: dict[str, list[str]] = {
    "Item":     ["InventoryItemNumber", "Item Number", "ItemNumber"],
    "Customer": ["CustomerNumber", "Customer"],
    "Supplier": ["SupplierNumber", "Supplier"],
    "UOM":      ["UnitOfMeasureCode", "Unit of Measure Code", "UOM"],
}
_UPSTREAM_SOURCE_KEYS: dict[str, list[str]] = {
    "Item":     ["ITEM_NUM", "ItemNumber", "InventoryItemNumber"],
    "Customer": ["CUSTOMER_NUM", "CustomerNumber"],
    "Supplier": ["SUPPLIER_NUM", "SupplierNumber"],
    "UOM":      ["UOM_CD", "UnitOfMeasureCode"],
}
_KEY_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_key(v: str | None) -> str:
    if v is None:
        return ""
    return _KEY_NORMALIZE_RE.sub("", str(v).lower())


async def simulate_conversion_load(conversion: Conversion) -> LoadRun:
    if not conversion.dataset_id or not conversion.template_id:
        run = LoadRun(
            conversion_id=conversion.id, run_type="simulate", status="skipped",
            total_records=0, passed_count=0, failed_count=0,
            warning_count=0, error_count=0, completed_at=datetime.utcnow(),
        )
        await run.insert()
        return run
    df, _lineage = await build_converted_dataframe(conversion)
    converted = df.fillna("").to_dict(orient="records")
    issues = await ValidationIssue.find(ValidationIssue.conversion_id == conversion.id).to_list()
    issue_dicts = [{"issue_type": i.issue_type, "severity": i.severity, "row_number": i.row_number,
                    "field_name": i.field_name, "message": i.message, "suggested_fix": i.suggested_fix}
                   for i in issues]
    result = simulate_load(converted, issue_dicts, upstream_failed_keys={}, key_field_by_dependency={}, dependency_failure_kinds={})
    run = LoadRun(
        conversion_id=conversion.id, run_type="simulate", status="completed",
        total_records=result["total_records"], passed_count=result["passed_count"],
        failed_count=result["failed_count"], warning_count=result["warning_count"],
        error_count=result["error_count"], completed_at=datetime.utcnow(),
    )
    await run.insert()
    for e in result["errors"]:
        await LoadError(load_run_id=run.id, **e).insert()
    new_status = "loaded" if result["failed_count"] == 0 else "failed"
    await conversion.set({"status": new_status, "updated_at": datetime.utcnow()})
    return run


async def build_load_summary(conversion: Conversion) -> dict[str, Any]:
    latest = await LoadRun.find(LoadRun.conversion_id == conversion.id).sort("-started_at").first_or_none()
    if not latest:
        return {"total_records": 0, "passed_count": 0, "failed_count": 0, "warning_count": 0,
                "error_count": 0, "error_categories": [], "root_causes": [], "dependency_impacts": []}
    errors = await LoadError.find(LoadError.load_run_id == latest.id).to_list()
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
        "total_records": latest.total_records, "passed_count": latest.passed_count,
        "failed_count": latest.failed_count, "warning_count": latest.warning_count,
        "error_count": latest.error_count,
        "error_categories": [{"name": k, "count": v} for k, v in sorted(cat.items(), key=lambda x: -x[1])],
        "root_causes": [{"cause": k, "count": v} for k, v in sorted(cause.items(), key=lambda x: -x[1])],
        "dependency_impacts": [{"object": k, "count": v} for k, v in sorted(dep.items(), key=lambda x: -x[1])],
    }
