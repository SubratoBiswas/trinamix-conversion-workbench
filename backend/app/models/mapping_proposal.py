"""A mapping document uploaded for review, before anything is trusted.

An analyst mapping workbook is the strongest signal the tool can get — a human
already worked the crosswalk out. But a NEW document can also silently contradict
what the tool already believes, and applying it blind would rewrite established
mappings with no trace. So an upload is parsed into a PROPOSAL: every row is
classified against the current learning library, contradictions are surfaced, and
nothing reaches the library until a person approves it.

Rows are classified as:
  new         — the library holds nothing for this target field
  unchanged   — the library already says exactly this
  conflict    — the library maps this target field to a DIFFERENT source column,
                or carries a different transformation rule
"""
from datetime import datetime
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field


class ProposedMapping(BaseModel):
    row_no: int
    target_object: str
    target_field: str
    source_field: str
    # A mapping cell often offers several columns ("Country / Country Code") or
    # wraps the name in an instruction. source_field holds the one being proposed;
    # these keep the rest so a reviewer can see what the document actually said.
    source_alternatives: list[str] = Field(default_factory=list)
    source_raw: Optional[str] = None
    fbdi_sheet: Optional[str] = None
    source_system: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: Optional[dict] = None
    notes: Optional[str] = None

    # classification against the library at analysis time
    status: str = "new"                    # new | unchanged | conflict
    decision: str = "pending"              # pending | approved | rejected
    # what the library currently says, when this row contradicts it
    current_source_field: Optional[str] = None
    current_rule_type: Optional[str] = None
    current_captured_from: Optional[str] = None
    conflict_reason: Optional[str] = None
    existing_learning_id: Optional[PydanticObjectId] = None


class MappingProposal(Document):
    file_name: str
    client_id: Optional[PydanticObjectId] = None
    target_object: Optional[str] = None      # the module this document covers
    source_system: Optional[str] = None
    # how the columns were located — "deterministic" or "ai", so a reviewer can
    # weigh a layout the tool had to guess at differently from one it recognised.
    layout_method: str = "deterministic"
    layout_note: Optional[str] = None
    detected_columns: dict = Field(default_factory=dict)

    rows: list[ProposedMapping] = Field(default_factory=list)
    count_new: int = 0
    count_unchanged: int = 0
    count_conflict: int = 0
    count_skipped: int = 0

    status: str = "awaiting_review"          # awaiting_review | applied | discarded
    applied_at: Optional[datetime] = None
    applied_by: Optional[str] = None
    learnings_written: int = 0
    conversions_touched: int = 0

    uploaded_by: Optional[str] = None
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "mapping_proposals"
        indexes = ["client_id", "target_object", "status"]
