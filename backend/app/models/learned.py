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
    project_id: Optional[PydanticObjectId] = None
    captured_from: Optional[str] = None
    captured_by: Optional[str] = None
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    confidence_boost: float = 0.26
    records_auto_fixed: int = 0

    # v10: cross-project knowledge base tracking
    times_reused: int = 0
    originated_in_