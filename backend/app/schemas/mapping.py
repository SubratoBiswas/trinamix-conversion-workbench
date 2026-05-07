"""Mapping suggestion schemas."""
from datetime import datetime
from typing import Any
from pydantic import BaseModel


class MappingOut(BaseModel):
    id: int
    conversion_id: int
    target_field_id: int
    target_field_name: str | None = None
    target_required: bool = False
    target_data_type: str | None = None
    target_max_length: int | None = None
    source_column: str | None = None
    confidence: float = 0.0
    reason: str | None = None
    suggested_transformation: dict[str, Any] | None = None
    review_required: int = 1
    status: str
    default_value: str | None = None
    comment: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    sample_source_values: list[Any] = []
    sample_converted_values: list[Any] = []

    class Config:
        from_attributes = True


class MappingUpdate(BaseModel):
    source_column: str | None = None
    suggested_transformation: dict[str, Any] | None = None
    default_value: str | None = None
    comment: str | None = None
    status: str | None = None  # approved | rejected | overridden | not_applicable | suggested
