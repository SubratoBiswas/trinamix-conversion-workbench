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
    # Source mode: "ebs" = live Oracle EBS query (default), "dataset" = uploaded file
    source_type: str = "ebs"
    ebs_table_hint: Optional[str] = None   # e.g. "MTL_SYSTEM_ITEMS_B"
    # Output mode: how this conversion is delivered.
    #   "fbdi_download" = generate an FBDI file the user uploads to Fusion manually
    #   "fusion_load"   = load directly via ERP Integration (default)
    output_mode: str = "fusion_load"
    # Background output generation job state, so heavy multi-sheet objects can build
    # off the request thread (the request returns immediately; the UI polls).
    #   None → never generated async · "generating" · "ready" · "failed"
    output_status: Optional[str] = None
    output_error: Optional[str] = None
    output_started_at: Optional[datetime] = None
    created_by: str = "admin"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "conversions"
        indexes = ["project_id", "target_object"]
