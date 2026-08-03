"""Conversion schemas."""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict

from app.schemas.oid import ApiOut


class ConversionCreate(BaseModel):
    project_id: str
    name: str
    description: Optional[str] = None
    target_object: Optional[str] = None
    dataset_id: Optional[str] = None
    dataset_ids: Optional[List[str]] = None  # multi-source (priority order)
    template_id: Optional[str] = None
    planned_load_order: Optional[int] = 100
    status: Optional[str] = None
    source_type: Optional[str] = None       # "dataset" | "ebs"
    ebs_table_hint: Optional[str] = None
    output_mode: Optional[str] = None       # "fbdi_download" | "fusion_load"


class ConversionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    target_object: Optional[str] = None
    dataset_id: Optional[str] = None
    dataset_ids: Optional[List[str]] = None  # multi-source (priority order)
    template_id: Optional[str] = None
    planned_load_order: Optional[int] = None
    status: Optional[str] = None
    source_type: Optional[str] = None       # "dataset" | "ebs"
    ebs_table_hint: Optional[str] = None    # e.g. "MTL_SYSTEM_ITEMS_B"
    output_mode: Optional[str] = None       # "fbdi_download" | "fusion_load"


class ConversionOut(ApiOut):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    project_id: str
    name: str
    description: Optional[str] = None
    target_object: Optional[str] = None
    dataset_id: Optional[str] = None
    dataset_ids: List[str] = []
    template_id: Optional[str] = None
    planned_load_order: int
    status: str
    source_type: str = "dataset"
    ebs_table_hint: Optional[str] = None
    output_mode: str = "fusion_load"
    # WHY A GENERATE PRODUCED NOTHING. _run_generation has always recorded these —
    # output_status="failed" and the exception text — and response_model silently
    # dropped both, because they were never declared here. So a generation that threw
    # and one that was never started looked identical from every screen and every API
    # consumer: the conversion simply stayed at its previous status with no output and
    # no explanation.
    #
    # That is exactly how Supplier Site and Supplier Site Assignment sat at
    # "mapping_suggested" while the other four generated — the bulk zip shipped four
    # files of six and nothing anywhere said the other two had failed, or why. Same
    # shape as the stale flag that was written by every path and read by none.
    output_status: Optional[str] = None     # "ready" | "failed"
    output_error: Optional[str] = None      # the exception, when it failed
    created_by: str
    created_at: datetime
    updated_at: datetime
    dataset_name: Optional[str] = None
    dataset_names: List[str] = []          # names of all sources (priority order)
    template_name: Optional[str] = None
    project_name: Optional[str] = None
