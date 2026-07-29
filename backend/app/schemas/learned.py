"""Pydantic schemas for the learned-mappings registry."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class LearnedMappingBase(BaseModel):
    kind: str
    category: str
    original_value: str
    resolved_value: str
    target_object: Optional[str] = None
    target_field: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: Optional[Any] = None
    project_id: Optional[str] = None
    captured_from: Optional[str] = None
    confidence_boost: Optional[float] = 0.26
    records_auto_fixed: Optional[int] = 0


class LearnedMappingCreate(LearnedMappingBase):
    pass


class LearnedMappingUpdate(BaseModel):
    category: Optional[str] = None
    original_value: Optional[str] = None
    resolved_value: Optional[str] = None
    target_object: Optional[str] = None
    target_field: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: Optional[Any] = None
    # Restrict a learning to specific interface sheets, or remove it from some.
    # Both empty = every sheet (the previous behaviour). Needed because Oracle
    # repeats a field name across sheets, so one approval used to reach all of
    # them — including sheets where the field must stay blank.
    sheets: Optional[list[str]] = None
    exclude_sheets: Optional[list[str]] = None


class LearnedMappingOut(LearnedMappingBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    captured_by: Optional[str] = None
    captured_at: datetime


class LearningStats(BaseModel):
    total: int
    objects_covered: int
    reusable_no_ai: int          # rules that resolve without any AI call
    times_applied: int           # times a learned rule was auto-applied to a conversion
    by_category: list[dict[str, Any]]
    by_source: list[dict[str, Any]] = []
