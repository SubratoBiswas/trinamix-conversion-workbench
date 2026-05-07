"""Project (engagement) schemas."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    client: str | None = None
    target_environment: str | None = None
    go_live_date: date | None = None
    owner: str | None = None
    status: str | None = "planning"


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    client: str | None = None
    target_environment: str | None = None
    go_live_date: date | None = None
    owner: str | None = None
    status: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    client: str | None = None
    target_environment: str | None = None
    go_live_date: date | None = None
    owner: str | None = None
    status: str
    production_cutover_start: datetime | None = None
    production_cutover_end: datetime | None = None
    migration_lead: str | None = None
    data_owner: str | None = None
    sox_controlled: int | None = 1
    created_at: datetime
    updated_at: datetime

    # Roll-ups
    conversion_count: int | None = 0
    in_progress_count: int | None = 0
    loaded_count: int | None = 0
    failed_count: int | None = 0
