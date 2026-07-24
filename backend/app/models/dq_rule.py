"""Data-quality rule (validation OR cleansing), scoped by FBDI object + client.

One collection backs both rule kinds (discriminated by ``kind``). Rules are created
three ways (``source``): EXTRACTED (auto-derived from an FBDI template's field
metadata — required/max-length/LOV/numeric/date), UPLOADED (imported from a rules
workbook/JSON), or MANUAL. They apply at Generate-time (and on-demand) for the
matching (target_object, client), like learnings: a client-scoped rule plus any
``is_global`` rule.
"""
from datetime import datetime
from typing import Any, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field

RULE_KINDS = ("validation", "cleansing")
RULE_SOURCES = ("extracted", "uploaded", "manual")


class DataQualityRule(Document):
    kind: str                                  # "validation" | "cleansing"
    target_object: str                         # e.g. "Supplier Import", "Item Import"
    field: Optional[str] = None                # target field name; None = object-wide
    rule_type: str                             # REQUIRED / MAX_LENGTH / VALUE_IN_SET / TRIM / UPPERCASE / ...
    params: dict[str, Any] = Field(default_factory=dict)
    severity: str = "error"                    # validation only: "error" | "warning"
    description: Optional[str] = None
    source: str = "manual"                     # "extracted" | "uploaded" | "manual"
    active: bool = True
    # Tenant scope (mirrors learnings): a client's own rules + anything global.
    client_id: Optional[PydanticObjectId] = None
    is_global: bool = False
    created_by: str = "admin"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "dq_rules"
        indexes = ["kind", "target_object", "client_id"]
