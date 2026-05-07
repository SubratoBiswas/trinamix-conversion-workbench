"""Conversion object schemas — replace the old Project (which was really
a conversion) with this. Each Conversion belongs to a Project (engagement).
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConversionCreate(BaseModel):
    project_id: int
    name: str
    description: str | None = None
    target_object: str | None = None
    dataset_id: int | None = None
    template_id: int | None = None
    planned_load_order: int | None = 100
    status: str | None = None  # default "planning"


class ConversionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    target_object: str | None = None
    dataset_id: int | None = None
    template_id: int | None = None
    planned_load_order: int | None = None
    status: str | None = None


class ConversionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    description: str | None = None
    target_object: str | None = None
    dataset_id: int | None = None
    template_id: int | None = None
    planned_load_order: int
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    # Convenience joins so the frontend doesn't need a second call
    dataset_name: str | None = None
    template_name: str | None = None
    project_name: str | None = None
