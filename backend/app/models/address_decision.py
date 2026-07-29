"""Analyst verdicts on a proposed address change.

Separate from ``RowDecision`` on purpose. That model adjudicates whether a ROW
survives; this adjudicates what a row's address SAYS. Overloading one document
with both would mean a duplicate verdict and an address verdict competing for the
same ``decision_key``, and the cross-conversion reuse rules differ — a "these two
suppliers are distinct" ruling is about this client's data, while a corrected
street address is true everywhere.

Keyed by ``address_key`` (a content hash of the address fields), for the same
reason duplicate decisions are keyed by content: the row's position moves between
the review and the generate, the address text does not.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import Field

# Analyst verdicts.
APPROVE = "approve"   # take the arbitrated recommendation as-is
EDIT = "edit"         # take the analyst's own corrected address
ADD = "add"           # source had no usable address; analyst supplies one
KEEP = "keep"         # reject the suggestion, ship the original untouched
FLAG = "flag"         # cannot resolve here — mark it and move on
ADDRESS_VERDICTS = {APPROVE, EDIT, ADD, KEEP, FLAG}


class AddressDecision(Document):
    conversion_id: PydanticObjectId
    # Kept so a verified address can be reused for the same client on the next
    # extract rather than re-billed to a provider.
    client_id: Optional[PydanticObjectId] = None
    target_object: Optional[str] = None

    # sha1 over the ORIGINAL address values — what identifies the thing decided.
    address_key: str
    verdict: str

    # The address that should ship. Set for approve / edit / add; None for keep.
    resolved: Optional[dict] = None
    # What was there before, so the audit trail survives the source changing.
    original: Optional[dict] = None
    # Which providers backed the recommendation at decision time, and how the
    # arbiter scored it — so a later reviewer can see what the analyst saw.
    backers: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    outcome: Optional[str] = None

    decided_by: Optional[str] = None
    decided_at: datetime = Field(default_factory=datetime.utcnow)
    note: Optional[str] = None

    class Settings:
        name = "address_decisions"
        indexes = ["conversion_id", "address_key",
                   [("conversion_id", 1), ("address_key", 1)]]
