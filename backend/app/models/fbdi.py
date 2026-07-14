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

class FBDITemplateFile(Document):
    """Durable copy of an uploaded template's raw bytes, stored in MongoDB so it
    survives Render redeploys (the container disk is ephemeral). FBDI templates
    are small (well under the 16MB BSON doc limit), so inline binary is fine."""
    template_id: PydanticObjectId
    file_name: Optional[str] = None
    content: bytes
    size: int = 0
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "fbdi_template_files"
        indexes = ["template_id"]

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
    # List of values the destination accepts, e.g.
    # [{"code": "1", "meaning": "Not planned"}, {"code": "3", "meaning": "MRP planned"}].
    # Used for value-aware mapping and crosswalk (value-pair) recommendations.
    allowed_values: list[dict] = Field(default_factory=list)
    # What Fusion defaults this field to when left blank (FBDI behaviour).
    default_if_blank: Optional[str] = None
    sequence: int = 0
    required_modules: list[str] = Field(default_factory=list)

    class Settings:
        name = "fbdi_fields"
        indexes = ["template_id", "sheet_id"]


class OracleLookup(Document):
    """One lookup code from the customer's own Fusion instance.

    Populated by importing a Manage Standard Lookups export. This is the ONLY
    authoritative source for the ~45 lookup types the FBDI templates reference by
    name but don't publish (EGP_MATERIAL_PLANNING, EGP_SOURCE_TYPES, …) — those
    codes are instance-configurable, so anything we'd otherwise seed is a guess.

    Once imported, these codes are written onto the matching FBDIFields'
    ``allowed_values`` with ``verified=True``, which flips the column from
    "unverified — passing values through" to fully mapped and validated.
    """
    lookup_type: str
    code: str
    meaning: Optional[str] = None
    description: Optional[str] = None
    enabled: bool = True
    source: str = "instance_import"   # instance_import | oracle_standard
    imported_by: Optional[str] = None
    imported_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "oracle_lookups"
        indexes = ["lookup_type", "code"]
