"""Conversion schemas."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ConversionCreate(BaseModel):
    project_id: str
    name: str
    description: Optional[str] = None
    target_object: Optional[str] = None
    dataset_id: Optional[str] = None
    template_id: Optional[str] = None
    planned_load_order: Optional[int] = 100
    status: Optional[str] = None


class ConversionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    target_object: Optional[str] = None
    dataset_id: Optional[str] = None
    template_id: Optional[str] = None
    planned_load_order: Optional[int] = None
    status: Optional[str] = None
    source_type: Optional[str] = None       # "dataset" | "ebs"
    ebs_table_hint: Optional[str] = None    # e.g. "MTL_SYSTEM_ITEMS_B"


class ConversionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    project_id: str
    name: str
    description: Optional[str] = None
    target_object: Optional[str] = None
    dataset_id: Optional[str] = None
    template_id: Optional[str] = None
    planned_load_order: int
    status: str
    source_type: str = "dataset"
    ebs_table_hint: Optional[str] = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    dataset_name: Optional[str] = None
    template_name: Optional[str] = None
    project_name: Optional[str] = None
