"""Project model."""
from datetime import datetime, date
from typing import List, Optional
from beanie import Document
from pydantic import Field

PROJECT_STATUSES = ("planning","in_progress","ready_for_uat","complete","on_hold")

# v10: source system types supported for discovery
SOURCE_SYSTEMS = ("netsuite", "oracle_ebs", "sap", "dynamics", "manual")

class Project(Document):
    name: str
    description: Optional[str] = None
    client: Optional[str] = None
    target_environment: Optional[str] = None
    go_live_date: Optional[date] = None
    owner: str = "admin"
    status: str = "planning"
    production_cutover_start: Optional[datetime] = None
    production_cutover_end: Optional[datetime] = None
    migration_lead: Optional[str] = None
    data_owner: Optional[str] = None
    sox_controlled: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    # environments
    environments: List[dict] = Field(default_factory=list)
    # v10 fields
    source_system: Optional[str] = None
    selected_modules: List[str] = Field(default_factory=list)
    phase: str = "discovery"
    dress_rehearsal_count: int = 0
    source_connection_id: Optional[str] = None
    current_environment: str = "DEV"

    class Settings:
        name = "projects"
