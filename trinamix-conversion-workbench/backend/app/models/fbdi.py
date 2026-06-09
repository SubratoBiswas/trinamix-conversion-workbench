"""Oracle FBDI template metadata models."""
from datetime import datetime
from typing import Any, Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

class FBDITemplate(Document):
    name: str
    module: Optional[str] = None
    business_object: Optional[str] = None
    tier: str = "T1"
    phase: str = "Blueprint"
    required_field_count: int = 0
    version: str = "1.0"
    file_name: Optional[str] = None
    file_path: Optional[str] = None
    status: str = "parsed"
    description: Optional[str] = None
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "fbdi_templates"

class FBDISheet(Document):
    template_id: PydanticObjectId
    sheet_name: str
    sequence: int = 0
    field_count: int = 0

    class Settings:
        name = "fbdi_sheets"
        indexes = ["template_id"]

class FBDIField(Document):
    template_id: PydanticObjectId
    sheet_id: PydanticObjectId
    field_name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    required: bool = False
    data_type: Optional[str] = None
    max_length: Optional[int] = None
    format_mask: Optional[str] = None
    sample_value: Optional[str] = None
    lookup_type: Optional[str] = None
    validation_notes: Optional[str] = None
    sequence: int = 0
    required_modules: list[str] = Field(default_factory=list)

    class Settings:
        name = "fbdi_fields"
        indexes = ["template_id", "sheet_id"]
