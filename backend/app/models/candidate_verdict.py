"""Stored AI verdicts for mapping candidates.

The deterministic guard is free and recomputed every load. The AI verdict costs a
model call, so once produced it is SAVED here and re-attached on every subsequent
load of that conversion — an analyst returning to a project sees the reasons
without re-running the review, and no tokens are spent twice.

Keyed by (conversion, target field, source column): the finest grain that a
verdict actually applies to. Re-vetting a field upserts, so a corrected verdict
replaces the old one rather than accumulating.
"""
from datetime import datetime
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import Field


class CandidateVerdict(Document):
    conversion_id: PydanticObjectId
    target_field_id: str
    source_column: str
    verdict: str                       # plausible | unlikely | wrong
    reason: str = ""
    model: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "candidate_verdicts"
        indexes = ["conversion_id"]
