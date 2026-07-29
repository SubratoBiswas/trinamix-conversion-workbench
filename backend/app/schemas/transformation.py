"""Transformation rule and crosswalk schemas."""
from datetime import datetime
from typing import Any
from pydantic import BaseModel

from app.schemas.oid import ApiOut


class TransformationRuleCreate(BaseModel):
    target_field_id: str | None = None
    source_column: str | None = None
    rule_type: str
    rule_config: dict[str, Any] = {}
    description: str | None = None


class TransformationRuleOut(ApiOut):
    id: str
    conversion_id: str
    target_field_id: str | None = None
    source_column: str | None = None
    rule_type: str
    rule_config: dict[str, Any]
    description: str | None = None
    sequence: int
    created_at: datetime

    class Config:
        from_attributes = True


class CrosswalkCreate(BaseModel):
    name: str
    field_name: str
    source_value: str
    target_value: str


class CrosswalkOut(ApiOut):
    id: str
    conversion_id: str
    name: str
    field_name: str
    source_value: str
    target_value: str

    class Config:
        from_attributes = True
