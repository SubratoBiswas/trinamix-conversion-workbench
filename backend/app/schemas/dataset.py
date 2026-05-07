"""Dataset request/response schemas."""
from datetime import datetime
from typing import Any
from pydantic import BaseModel


class DatasetColumnProfileOut(BaseModel):
    id: int
    column_name: str
    position: int
    inferred_type: str | None = None
    null_count: int = 0
    null_percent: float = 0.0
    distinct_count: int = 0
    sample_values: list[Any] = []
    min_value: str | None = None
    max_value: str | None = None
    pattern_summary: str | None = None

    class Config:
        from_attributes = True


class DatasetOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    file_name: str
    file_type: str
    row_count: int
    column_count: int
    status: str
    uploaded_at: datetime

    class Config:
        from_attributes = True


class DatasetDetailOut(DatasetOut):
    columns: list[DatasetColumnProfileOut] = []


class DatasetPreviewOut(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
