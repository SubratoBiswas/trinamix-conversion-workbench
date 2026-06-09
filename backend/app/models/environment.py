"""Environment + EnvironmentRun models."""
from datetime import datetime
from typing import Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

DEFAULT_ENVIRONMENTS = [
    {"name": "DEV", "order": 1, "color": "info",    "description": "Development build & blueprint validation"},
    {"name": "QA",  "order": 2, "color": "brand",   "description": "Functional QA cycle"},
    {"name": "UAT", "order": 3, "color": "warning", "description": "User acceptance testing"},
    {"name": "PROD","order": 4, "color": "danger",  "description": "Production cutover (SOX-controlled)"},
]

ENV_RUN_STATUSES = ("pending","running","complete","failed","blocked")

class Environment(Document):
    project_id: PydanticObjectId
    name: str
    description: Optional[str] = None
    sort_order: int = 1
    color: str = "info"
    sox_controlled: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "environments"
        indexes = ["project_id"]

class EnvironmentRun(Document):
    environment_id: PydanticObjectId
    conversion_id: PydanticObjectId
    dataset_id: Optional[PydanticObjectId] = None
    status: str = "pending"
    stage: Optional[str] = None
    record_count: Optional[int] = None
    passed_count: Optional[int] = None
    failed_count: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: Optional[str] = None

    class Settings:
        name = "environment_runs"
        indexes = ["environment_id", "conversion_id"]
