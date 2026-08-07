"""Mapping suggestion schemas."""
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel

from app.schemas.oid import ApiOut


class MappingOut(ApiOut):
    id: str
    conversion_id: str
    target_field_id: str
    target_field_name: Optional[str] = None
    target_required: bool = False
    target_data_type: Optional[str] = None
    target_max_length: Optional[int] = None
    target_lov: list[dict[str, Any]] = []
    target_default_if_blank: Optional[str] = None
    source_column: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None
    suggested_transformation: Optional[dict[str, Any]] = None
    review_required: int = 1
    status: str
    default_value: Optional[str] = None
    comment: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    # WHICH dated decision is currently in force for this field, and where it came
    # from. The store resolves precedence by date-time (latest wins), so a value on
    # screen is the newest statement about the field — but nothing said WHICH one.
    # Surfaced so the analyst can read "this is the 06-Aug workbook rule" or "you set
    # this on 04-Aug 14:32", instead of guessing which change won. `applied_at` is the
    # instruction's effective date (a person's edit-time, or the store entry's
    # effective date for a value the library supplied); `applied_from` is a short
    # provenance label (the person's email, or where the library learned it).
    applied_at: Optional[datetime] = None
    applied_from: Optional[str] = None
    # This row is a VIEW of the one dated store, not a decision in its own right.
    # Declared here because response_model strips whatever it does not name, and a
    # field the screen never receives is a field the analyst cannot be shown —
    # which is how mapping_sync came to be silently dropped.
    derived: bool = False
    derived_from: Optional[str] = None
    # Which Oracle interface this field lives on, and whether the client loads it.
    # Customer's template ships 19 interfaces and NextPower loads 15; a constant set
    # on one of the other four is accepted, reads approved, and ships nowhere.
    # Declared here for the same reason `derived` is.
    target_sheet: Optional[str] = None
    target_in_load_scope: bool = True
    # Set when the generator will fill this column itself — the Customer linkage
    # (Batch Identifier, the Original System keys and their references) that no
    # source extract contains. Without it the grid calls such a field "Required
    # field with no source and no default" while the shipped file carries a value
    # on every row, which reads as the tool ignoring the mapping.
    target_generated: Optional[str] = None
    sample_source_values: list[Any] = []
    sample_converted_values: list[Any] = []

    class Config:
        from_attributes = True


class MappingUpdate(BaseModel):
    source_column: Optional[str] = None
    suggested_transformation: Optional[dict[str, Any]] = None
    default_value: Optional[str] = None
    comment: Optional[str] = None
    status: Optional[str] = None
