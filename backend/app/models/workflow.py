"""Visual workflow / dataflow definitions."""
from datetime import datetime
from typing import Any, Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

class Workflow(Document):
    name: str
    description: Optional[str] = None
    conversion_id: Optional[PydanticObjectId] = None
    nodes: list[Any] = Field(default_factory=list)
    edges: list[Any] = Field(default_factory=list)
    status: str = "draft"
    last_run_at: Optional[datetime] = None
    last_run_summary: Optional[dict] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "workflows"
        indexes = ["conversion_id"]
