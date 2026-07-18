"""Learned mappings registry."""
from datetime import datetime
from typing import Any, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field


class LearnedMapping(Document):
    kind: str
    category: str
    original_value: str
    resolved_value: str
    target_object: Optional[str] = None
    target_field: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: Optional[dict] = None
    # Tenant scope. A client-scoped learning applies only to conversions of that
    # client; a global learning (is_global=True, client_id=None) applies to all
    # clients. Legacy rows with neither set are treated as the default client by
    # the scoping query (back-compat) until the migration tags them.
    client_id: Optional[PydanticObjectId] = None
    is_global: bool = False
    project_id: Optional[PydanticObjectId] = None
    captured_from: Optional[str] = None
    captured_by: Optional[str] = None
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    confidence_boost: float = 0.26
    records_auto_fixed: int = 0
    times_reused: int = 0
    originated_in_project_id: Optional[PydanticObjectId] = None
    source_erp: Optional[str] = None

    class Settings:
        name = "learned_mappings"
