"""Project schemas."""
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    client: Optional[str] = None
    target_environment: Optional[str] = None
    go_live_date: Optional[date] = None
    owner: Optional[str] = None
    status: Optional[str] = "planning"


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    client: Optional[str] = None
    target_environment: Optional[str] = None
    go_live_date: Optional[date] = None
    owner: Optional[str] = None
    status: Optional[str] = None
    migration_lead: Optional[str] = None
    data_owner: Optional[str] = None
    sox_controlled: Optional[int] = None
    production_cutover_start: Optional[datetime] = None
    production_cutover_end: Optional[datetime] = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    name: str
    description: Optional[str] = None
    client: Optional[str] = None
    target_environment: Optional[str] = None
    go_live_date: Optional[date] = None
    owner: Optional[str] = None
    status: str
    production_cutover_start: Optional[datetime] = None
    production_cutover_end: Optional[datetime] = None
    migration_lead: Optional[str] = None
    data_owner: Optional[str] = None
    sox_controlled: Optional[int] = 1
    created_at: datetime
    updated_at: datetime
    conversion_count: Optional[int] = 0
    in_progress_count: Optional[int] = 0
    loaded_count: Optional[int] = 0
    failed_count: Optional[int] = 0
