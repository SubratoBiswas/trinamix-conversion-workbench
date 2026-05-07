"""FBDI template/sheet/field schemas."""
from datetime import datetime
from typing import Any
from pydantic import BaseModel


class FBDIFieldOut(BaseModel):
    id: int
    template_id: int
    sheet_id: int
    field_name: str
    display_name: str | None = None
    description: str | None = None
    required: bool = False
    data_type: str | None = None
    max_length: int | None = None
    format_mask: str | None = None
    sample_value: str | None = None
    lookup_type: str | None = None
    validation_notes: str | None = None
    sequence: int = 0
    required_modules: list[str] = []

    class Config:
        from_attributes = True


class FBDIFieldUpdate(BaseModel):
    field_name: str | None = None
    display_name: str | None = None
    description: str | None = None
    required: bool | None = None
    data_type: str | None = None
    max_length: int | None = None
    format_mask: str | None = None
    sample_value: str | None = None
    lookup_type: str | None = None
    validation_notes: str | None = None


class FBDISheetOut(BaseModel):
    id: int
    template_id: int
    sheet_name: str
    sequence: int
    field_count: int

    class Config:
        from_attributes = True


class FBDITemplateOut(BaseModel):
    id: int
    name: str
    module: str | None = None
    tier: str = "T1"
    phase: str = "Blueprint"
    business_object: str | None = None
    required_field_count: int = 0
    version: str
    file_name: str | None = None
    status: str
    description: str | None = None
    uploaded_at: datetime

    class Config:
        from_attributes = True


class FBDITemplateDetailOut(FBDITemplateOut):
    sheets: list[FBDISheetOut] = []
    field_count: int = 0
