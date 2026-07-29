"""User decisions about duplicate clusters and cleansing findings.

WHY A STABLE KEY, NOT A ROW NUMBER
----------------------------------
``/duplicate-candidates`` reports ``member.row`` as a POSITIONAL index into the
frame it happened to build for that request. Generation builds a different frame
(different source routing, different max_rows, and de-duplication runs in
between), so row 4,213 in the review screen is not row 4,213 at write time.
Storing a decision against a position would silently drop the wrong supplier.

So a decision is keyed on a hash of the row's IDENTITY VALUES (the same identity
fields ``entity_resolution`` uses for matching). The key is derived from the data,
so it survives re-mapping, re-generation, row reordering and a changed row count.
If the identity values themselves change, the key changes and the decision no
longer applies — which is the correct behaviour: it is a different record.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import Field

# Duplicate-cluster verdicts
KEEP_SURVIVOR = "keep_survivor"   # keep one nominated row, drop the rest
MERGE = "merge"                   # collapse to a golden record, first non-blank wins
KEEP_ALL = "keep_all"             # genuinely distinct entities — leave every row
EXCLUDE = "exclude"               # drop the whole cluster from the output
# Keep a NAMED SUBSET. The all-or-one verdicts above cannot describe a cluster
# that is partly duplicated — five PricewaterhouseCoopers rows carrying three
# different tax registrations are three real entities plus one duplicate pair,
# so neither merging to one nor keeping all five is correct.
KEEP_SUBSET = "keep_subset"
DUP_VERDICTS = {KEEP_SURVIVOR, MERGE, KEEP_ALL, EXCLUDE, KEEP_SUBSET}

# Cleansing-finding verdicts
APPLY = "apply"                   # apply the suggested fix
IGNORE = "ignore"                 # leave the data as-is
CLEANSE_VERDICTS = {APPLY, IGNORE}


class RowDecision(Document):
    conversion_id: PydanticObjectId
    # Kept so a decision can be promoted to a client-scoped learning and matched
    # against a future conversion of the same object.
    client_id: Optional[PydanticObjectId] = None
    target_object: Optional[str] = None

    scope: str = "duplicate"          # "duplicate" | "cleansing"
    # duplicate: hash of the cluster's sorted member keys.
    # cleansing: "<field>|<issue_type>".
    decision_key: str
    verdict: str

    # Duplicates only.
    survivor_key: Optional[str] = None
    member_keys: list[str] = Field(default_factory=list)
    # keep_subset only: the rows to keep. Everything else in member_keys drops.
    keep_keys: list[str] = Field(default_factory=list)
    # Human-readable identity of the cluster, for the audit trail and for showing
    # a promoted learning in the UI without rebuilding the frame.
    label: Optional[str] = None

    decided_by: Optional[str] = None
    decided_at: datetime = Field(default_factory=datetime.utcnow)
    note: Optional[str] = None
    # Set when the verdict has been promoted to a cross-conversion LearnedMapping,
    # so re-promoting is a no-op rather than a duplicate.
    promoted: bool = False

    class Settings:
        name = "row_decisions"
        indexes = ["conversion_id", "decision_key",
                   [("conversion_id", 1), ("decision_key", 1)]]
