"""Load orchestration models."""
from datetime import datetime
from typing import Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

LOAD_RUN_TYPES = ("simulate","fusion")
LOAD_STATUSES = ("running","completed","failed")

class LoadRun(Document):
    conversion_id: PydanticObjectId
    run_type: str = "simulate"
    status: str = "running"
    total_records: int = 0
    passed_count: int = 0
    failed_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    # Oracle ERP Integration request id returned by importBulkData, and the
    # latest polled ESS phase (succeeded/warning/error/running/unknown).
    fusion_request_id: Optional[str] = None
    fusion_state: Optional[str] = None

    class Settings:
        name = "load_runs"
        indexes = ["conversion_id"]

class LoadError(Document):
    load_run_id: PydanticObjectId
    row_number: Optional[int] = None
    object_name: Optional[str] = None
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    root_cause: Optional[str] = None
    related_dependency: Optional[str] = None
    reference_value: Optional[str] = None
    suggested_fix: Optional[str] = None

    class Settings:
        name = "load_errors"
        indexes = ["load_run_id"]
