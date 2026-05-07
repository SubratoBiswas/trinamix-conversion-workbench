"""Validation issue / cleansing / output / load schemas."""
from datetime import datetime
from typing import Any
from pydantic import BaseModel


class ValidationIssueOut(BaseModel):
    id: int
    conversion_id: int
    category: str
    row_number: int | None = None
    field_name: str | None = None
    issue_type: str
    severity: str
    message: str
    suggested_fix: str | None = None
    auto_fixable: bool = False
    impacted_count: int = 1
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ConvertedOutputOut(BaseModel):
    id: int
    conversion_id: int
    output_file_name: str
    row_count: int
    column_count: int
    status: str
    generated_at: datetime

    class Config:
        from_attributes = True


class OutputPreviewOut(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
    lineage: dict[str, dict[str, Any]]  # target_col -> {source_column, transformations}


class LoadErrorOut(BaseModel):
    id: int
    row_number: int | None = None
    object_name: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    root_cause: str | None = None
    related_dependency: str | None = None
    reference_value: str | None = None
    suggested_fix: str | None = None

    class Config:
        from_attributes = True


class LoadRunOut(BaseModel):
    id: int
    conversion_id: int
    run_type: str
    status: str
    total_records: int
    passed_count: int
    failed_count: int
    warning_count: int
    error_count: int
    started_at: datetime
    completed_at: datetime | None = None

    class Config:
        from_attributes = True


class LoadSummaryOut(BaseModel):
    total_records: int
    passed_count: int
    failed_count: int
    warning_count: int
    error_count: int
    error_categories: list[dict[str, Any]]
    root_causes: list[dict[str, Any]]
    dependency_impacts: list[dict[str, Any]]
