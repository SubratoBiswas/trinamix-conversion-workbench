"""Mapping suggestion schemas."""
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel


class MappingOut(BaseModel):
    id: str
    conversion_id: str
    target_field_id: str
    target_field_name: Optional[str] = None
    target_required: bool = False
    target_data_type: Optional[str] = None
    target_max_length: Optional[int] = None
    source_column: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None
    suggested_transformation: Optional[dict[str, Any]] = None
    review_required: int = 1
    status: str
    default_value: Optional[str] = None
    comment: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    sample_source_values: list[Any] = []
    sample_converted_values: list[Any] = []

    class Config:
        from_attributes = True


class MappingUpdate(BaseModel):
    source_column: Optional[str] = None
    suggested_transformation: Optional[dict[str, Any]] = None
    default_value: Optional[str] = None
    comment: Optional[str] = None
    status: Optional[str] = None
