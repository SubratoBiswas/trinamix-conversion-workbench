"""Conversion model."""
from datetime import datetime
from typing import List, Optional
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
    # Primary source (compatibility): the first/only source file. Kept so all the
    # existing single-source code keeps working unchanged.
    dataset_id: Optional[PydanticObjectId] = None
    # Multi-source: a module/target object can be fed by SEVERAL source files
    # (NetSuite, EBS, …). Each is converted individually (per-source preview); at
    # Generate the converted frames are merged + de-duplicated into one output.
    # Ordered by SOURCE PRIORITY — earlier entries win on a natural-key clash.
    # dataset_id always mirrors dataset_ids[0] so legacy reads stay correct.
    dataset_ids: List[PydanticObjectId] = Field(default_factory=list)
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
    # Which cleansing families run at generation, and any per-field overrides.
    # See services/cleansing_rules.default_profile — None means "use the safe
    # defaults" (whitespace/punctuation + unicode), which is what an untouched
    # conversion gets. Case and legal-suffix standardisation rewrite legal entity
    # names, so they are only ever active because someone switched them on here.
    cleansing_profile: Optional[dict] = None
    created_by: str = "admin"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def source_dataset_ids(self) -> List[PydanticObjectId]:
        """Effective ordered source datasets (priority order). Prefers the
        multi-source list; falls back to the single dataset_id for legacy rows."""
        if self.dataset_ids:
            return list(self.dataset_ids)
        return [self.dataset_id] if self.dataset_id else []

    class Settings:
        name = "conversions"
        indexes = ["project_id", "target_object"]
