"""Conversion model."""
from datetime import datetime
from typing import Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

CONVERSION_STATUSES = (
    "planning","draft","mapping_suggested","awaiting_approval",
    "validated","output_generated","loaded","failed",
)

class Conversion(Document):
    project_id: PydanticObjectId
    name: str
    description: Optional[str] = None
    dataset_id: Optional[PydanticObjectId] = None
    template_id: Optional[PydanticObjectId] = None
    target_object: Optional[str] = None
    planned_load_order: int = 100
    status: str = "planning"
    created_by: str = "admin"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "conversions"
        indexes = ["project_id", "target_object"]
