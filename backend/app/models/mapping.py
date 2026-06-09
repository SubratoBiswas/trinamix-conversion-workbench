"""Mapping suggestion model."""
from datetime import datetime
from typing import Any, Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

MAPPING_STATUSES = ("suggested","approved","rejected","overridden","not_applicable")

class MappingSuggestion(Document):
    conversion_id: PydanticObjectId
    target_field_id: PydanticObjectId
    source_column: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None
    suggested_transformation: Optional[dict] = None
    review_required: int = 1
    status: str = "suggested"
    default_value: Optional[str] = None
    comment: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "mapping_suggestions"
        indexes = ["conversion_id", "target_field_id"]
