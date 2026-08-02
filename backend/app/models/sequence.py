"""Persisted number allocations, so a generated key is generated ONCE.

Analyst, 31-Jul (CW row 23): "Party Number should have a unique sequence. NXT000001
(if party type is ORGANIZATION), NXT00001_C1 (if the party type is PERSON), should
increment for each org/person" — and then, decisively: "write a logic in which this
should be generated once and next time onwards the same should be repeated, not
different number each run."

That second sentence is the whole design. A counter that starts at 1 on every
Generate produces a DIFFERENT Party Number for the same customer each time, and the
damage is not cosmetic: Party Number is the key every child interface points at, so a
second load creates duplicate parties rather than updating the first, and a
reconciliation between two downloads of "the same" data disagrees on every row. The
number therefore cannot be derived from position in the frame — the extract is
re-sorted, filtered and re-uploaded constantly, and any of those would renumber
everyone.

So the allocation is keyed on a NATURAL KEY from the source (entityid, internalid —
whatever identifies the entity in the legacy system) and stored. A later run looks the
key up and reuses the number it was given; only a key never seen before draws a new
one. Deleting the conversion, regenerating, re-uploading the extract or adding rows
does not disturb any number already issued.

Scoped by client and target field, because two clients' sequences are unrelated and
one client's Party Number sequence is unrelated to any other numbered field.
"""
from datetime import datetime
from typing import Optional

from beanie import Document, PydanticObjectId
from pymongo import IndexModel

from pydantic import Field


class SequenceAllocation(Document):
    # WHOSE sequence. A client's numbering is its own; nothing crosses tenants.
    client_id: Optional[PydanticObjectId] = None
    # WHICH sequence. "Customer" / "Party Number" is a different counter from
    # "Customer" / "Account Number", even for the same client.
    target_object: str
    target_field: str
    # WHAT it was issued to — the natural key from the SOURCE, normalised. This is
    # what makes the number stable: the same legacy entity asks the same question and
    # gets the same answer, whatever row it happens to be on this time.
    natural_key: str
    # The issued value, formatted ("NXT000123"), and the raw ordinal behind it so the
    # next allocation can be computed without re-parsing the format.
    value: str
    seq: int
    # A person hanging off an organisation carries the org's number plus a child
    # suffix (NXT000123_C1), so the child counter is per-parent.
    parent_key: Optional[str] = None
    child_seq: Optional[int] = None
    issued_at: datetime = Field(default_factory=datetime.utcnow)
    issued_in_project_id: Optional[PydanticObjectId] = None

    class Settings:
        name = "sequence_allocations"
        indexes = [
            # The identity of an allocation. Unique, so two concurrent generates
            # cannot hand the same entity two different numbers — the loser of the
            # race re-reads and finds the winner's value rather than inventing one.
            IndexModel(
                [("client_id", 1), ("target_object", 1), ("target_field", 1),
                 ("natural_key", 1)],
                unique=True, name="uniq_allocation",
            ),
            # "What is the highest number issued so far" — asked once per generate.
            IndexModel([("client_id", 1), ("target_object", 1), ("target_field", 1),
                        ("seq", -1)], name="by_seq"),
        ]
