"""Project schemas."""
from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    client: Optional[str] = None
    target_environment: Optional[str] = None
    go_live_date: Optional[date] = None
    owner: Optional[str] = None
    status: Optional[str] = "planning"
    # v10
    source_system: Optional[str] = None
    selected_modules: List[str] = []
    phase: Optional[str] = "discovery"
    source_connection_id: Optional[str] = None


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
    # v10
    source_system: Optional[str] = None
    selected_modules: Optional[List[str]] = None
    phase: Optional[str] = None
    dress_rehearsal_count: Optional[int] = None
    source_connection_id: Optional[str] = None


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