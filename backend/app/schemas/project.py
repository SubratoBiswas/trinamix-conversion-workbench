"""Project schemas."""
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict

from app.schemas.oid import ApiOut


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    client: Optional[str] = None                 # legacy free-text label
    # THE TENANT, AND IT IS REQUIRED — either an existing client id, or
    # ``new_client_name`` to create one in the same call.
    #
    # This was optional and fell back to the bootstrap "default" client, which reads
    # as harmless and is not. Everything an analyst decides is stored as a CLIENT
    # rule, so the client is the key the whole library is filed and read under: an
    # untagged project silently files its decisions under "default", and a correction
    # made in a properly tagged project is then skipped when it reaches that one — as
    # a cross-tenant leak. That is the "changed the mapping in one project, it did not
    # reach the other, same client and source" report. The fan-out no longer treats
    # untagged as a foreign tenant, but leaving the hole open just moves the problem:
    # the rules still land under the wrong client and cannot be found later.
    #
    # Asking once, at creation, is cheaper than any of that.
    client_id: Optional[str] = None
    new_client_name: Optional[str] = None        # create this client and use it
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