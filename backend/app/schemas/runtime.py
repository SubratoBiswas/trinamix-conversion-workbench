"""Validation issue / cleansing / output / load schemas."""
from datetime import datetime
from typing import Any
from pydantic import BaseModel

from app.schemas.oid import ApiOut


class ValidationIssueOut(ApiOut):
    id: str
    conversion_id: str
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


class ConvertedOutputOut(ApiOut):
    id: str
    conversion_id: str
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
    # The two counts kept apart. `total_rows` is the best estimate of the finished
    # file; these say what it was estimated FROM, because a preview converts only
    # `preview_limit` rows and the two numbers diverge exactly when something has
    # gone wrong — which is when anybody reads them.
    source_rows: int = 0
    converted_rows: int = 0
    preview_limit: int = 0
    # Why the frame is empty. Present ONLY when it is: zero rows with columns
    # renders as a header over an empty body, and a blank panel cannot be told
    # apart from a failed load. {cause, headline, detail}.
    empty_reason: dict[str, Any] | None = None


class LoadErrorOut(ApiOut):
    id: str
    load_run_id: str
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


class LoadRunOut(ApiOut):
    id: str
    conversion_id: str
    run_type: str
    status: str
    total_records: int
    passed_count: int
    failed_count: int
    warning_count: int
    error_count: int
    started_at: datetime
    completed_at: datetime | None = None
    fusion_request_id: str | None = None
    fusion_state: str | None = None
    business_object: str | None = None
    fusion_tables: list[str] | None = None
    fusion_work_area: str | None = None
    fusion_response: str | None = None

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
