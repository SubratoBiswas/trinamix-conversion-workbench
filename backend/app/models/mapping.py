"""Mapping suggestion model."""
from datetime import datetime
from typing import Any, Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

MAPPING_STATUSES = ("suggested","approved","rejected","overridden","not_applicable")

class MappingSuggestion(Document):
    conversion_id: PydanticObjectId
    target_field_id: PydanticObjectId
    source_column: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None
    suggested_transformation: Optional[dict] = None
    review_required: int = 1
    status: str = "suggested"
    default_value: Optional[str] = None
    comment: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # A VIEW OF THE ONE DATED STORE, not a decision in its own right.
    #
    # These rows used to be the second store: the library was copied into them and
    # generation read the copy, so the screen and the file could disagree and
    # neither could be shown to be wrong. A row is now derived whenever the store
    # put it there, and `derived_from` records which dated entry it came from — so
    # "why does this say that" is answerable from the row, and a derived row can be
    # regenerated at any time without losing anything a person said.
    #
    # A row a PERSON edited is not derived. That edit is itself an entry in the
    # store, carrying approved_at as its date, and it competes on date like every
    # other statement.
    derived: bool = False
    derived_from: Optional[str] = None
    derived_at: Optional[datetime] = None
    # v10: dual-approval for SOX-controlled projects
    requires_dual_approval: int = 0
    second_approver_email: Optional[str] = None
    second_approved_at: Optional[datetime] = None

    class Settings:
        name = "mapping_suggestions"