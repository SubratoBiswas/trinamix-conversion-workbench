"""Dataset schemas."""
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel


class DatasetColumnProfileOut(BaseModel):
    id: str
    column_name: str
    position: int
    inferred_type: Optional[str] = None
    null_count: int = 0
    null_percent: float = 0.0
    distinct_count: int = 0
    sample_values: list[Any] = []
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    pattern_summary: Optional[str] = None

    class Config:
        from_attributes = True


class DatasetOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    file_name: str
    file_type: str
    row_count: int
    column_count: int
    status: str
    detected_object_type: Optional[str] = None
    detection_confidence: float = 0.0
    detection_suggestions: list = []
    uploaded_at: datetime

    class Config:
        from_attributes = True


class DatasetDetailOut(DatasetOut):
    columns: list[DatasetColumnProfileOut] = []


class DatasetPreviewOut(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
