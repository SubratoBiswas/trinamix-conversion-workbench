"""Environment + cutover dashboard schemas."""
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict

from app.schemas.oid import ApiOut


class EnvironmentOut(ApiOut):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    name: str
    description: Optional[str] = None
    sort_order: int
    color: str
    sox_controlled: int
    created_at: datetime


class EnvironmentRunOut(ApiOut):
    model_config = ConfigDict(from_attributes=True)
    id: str
    environment_id: str
    conversion_id: str
    dataset_id: Optional[str] = None
    status: str
    stage: Optional[str] = None
    record_count: Optional[int] = None
    passed_count: Optional[int] = None
    failed_count: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: Optional[str] = None
    environment_name: Optional[str] = None
    conversion_name: Optional[str] = None
    dataset_name: Optional[str] = None


class EnvironmentRunCreate(BaseModel):
    environment_id: str
    conversion_id: str
    dataset_id: Optional[str] = None
    notes: Optional[str] = None


class EnvironmentRunUpdate(BaseModel):
    status: Optional[str] = None
    stage: Optional[str] = None
    notes: Optional[str] = None
    dataset_id: Optional[str] = None


class CutoverDashboard(BaseModel):
    project_id: str
    project_name: str
    days_to_go_live: Optional[int] = None
    cutover_window_start: Optional[datetime] = None
    cutover_window_end: Optional[datetime] = None
    sox_controlled: bool
    environments: list[dict[str, Any]]
    pipeline_runs: list[dict[str, Any]]
