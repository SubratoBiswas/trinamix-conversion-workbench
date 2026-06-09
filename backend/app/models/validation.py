"""Validation and cleansing issue models."""
from datetime import datetime
from typing import Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

SEVERITIES = ("info","warning","error","critical")
ISSUE_CATEGORIES = ("cleansing","validation")

class ValidationIssue(Document):
    conversion_id: PydanticObjectId
    category: str = "validation"
    row_number: Optional[int] = None
    field_name: Optional[str] = None
    issue_type: str
    severity: str = "warning"
    message: str
    suggested_fix: Optional[str] = None
    auto_fixable: bool = False
    impacted_count: int = 1
    status: str = "open"
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "validation_issues"
        indexes = ["conversion_id"]
