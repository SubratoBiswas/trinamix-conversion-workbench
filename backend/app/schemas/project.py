"""Project schemas."""
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict

from app.schemas.oid import ApiOut


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    client: Optional[str] = None                 # legacy free-text label
    client_id: Optional[str] = None              # tenant this project belongs to
    target_environment: Optional[str] = None
    go_live_date: Optional[date] = None
    owner: Optional[str] = None
    status: Optional[str] = "planning"
    # v10
    source_system: Optional[str] = None
    selected_modules: List[str] = []
    phase: Optional[str] = "discovery"
    source_connection_id: Optional[str] = None
    # Wizard: create a SourceConnection at the same time as the project
    initial_connection: Optional[Dict[str, Any]] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    client: Optional[str] = None
    client_id: Optional[str] = None              # reassign the project's tenant
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


class ProjectOut(ApiOut):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    name: str
    description: Optional[str] = None
    client: Optional[str] = None
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    target_environment: Optional[str] = None
    go_live_date: Optional[date] = None
    owner: Optional[str] = None
    status: str
    production_cutover_start: Optional[datetime] = None
    production_cutover_end: Optional[datetime] = None
    # v10 engagement metadata (previously dropped because they weren't declared)
    source_system: Optional[str] = None
    phase: Optional[str] = None
    selected_modules: Optional[List[str]] = None
    migration_lead: Optional[str] = None
    data_owner: Optional[str] = None
    sox_controlled: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Conversion roll-ups added by _hydrate (kept in sync with ProjectOverview's
    # client-side definitions so the card and the detail page always agree).
    conversion_count: int = 0
    planning_count: int = 0
    in_progress_count: int = 0
    loaded_count: int = 0
    failed_count: int = 0
    source_connection_count: int = 0
    has_active_source_connection: bool = False