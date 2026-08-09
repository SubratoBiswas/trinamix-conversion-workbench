"""Transformation rule and crosswalk schemas."""
from datetime import datetime
from typing import Any
from pydantic import BaseModel

from app.schemas.oid import ApiOut


class TransformationRuleCreate(BaseModel):
    target_field_id: str | None = None
    source_column: str | None = None
    rule_type: str
    rule_config: dict[str, Any] = {}
    description: str | None = None
    # The plain-English/SQL instruction the analyst typed to author this rule,
    # persisted so it can be reviewed and re-used later (screenshot request).
    prompt: str | None = None
    # Where this rule should apply. "global" (default) captures it to the shared
    # client+source+object library so it fans out to existing and future projects;
    # "project" keeps it on THIS conversion only (no library capture, no fan-out).
    # Drives the "apply global vs per-project" control in the rule/constant modal.
    scope: str = "global"


class TransformationRuleOut(ApiOut):
    id: str
    conversion_id: str
    target_field_id: str | None = None
    source_column: str | None = None
    rule_type: str
    rule_config: dict[str, Any]
    description: str | None = None
    prompt: str | None = None
    sequence: int
    created_at: datetime
    # What saving the rule did to the MAPPING row — e.g. {"synced": true,
    # "source_column": "Legal Name", "previous_source_column": "Name"}. Declared
    # HERE because response_model silently strips keys it does not know about: the
    # router can return it all it likes and FastAPI will drop it on the way out.
    mapping_sync: dict[str, Any] | None = None
    # Whether the save also reached the shared client+source-scoped library (so it
    # will propagate to other/future projects). False = this conversion only.
    learned: bool | None = None
    # Echo of the requested scope ("global" | "project"), so the UI can confirm what
    # the save did and show the right badge.
    scope: str | None = None

    class Config:
        from_attributes = True


class CrosswalkCreate(BaseModel):
    name: str
    field_name: str
    source_value: str
    target_value: str


class CrosswalkOut(ApiOut):
    id: str
    conversion_id: str
    name: str
    field_name: str
    source_value: str
    target_value: str

    class Config:
        from_attributes = True
