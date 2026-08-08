"""Pydantic schemas for the learned-mappings registry."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.oid import ApiOut


class LearnedMappingBase(BaseModel):
    kind: str
    category: str
    original_value: str
    resolved_value: str
    target_object: Optional[str] = None
    target_field: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: Optional[Any] = None
    project_id: Optional[str] = None
    captured_from: Optional[str] = None
    confidence_boost: Optional[float] = 0.26
    records_auto_fixed: Optional[int] = 0


class LearnedMappingCreate(LearnedMappingBase):
    pass


class LearnedMappingUpdate(BaseModel):
    category: Optional[str] = None
    original_value: Optional[str] = None
    resolved_value: Optional[str] = None
    target_object: Optional[str] = None
    target_field: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: Optional[Any] = None
    # Source-system scope. LearnedMappingOut has always RETURNED this, but it was
    # missing here, so a learning's scope was read-only — the engine scopes by it
    # (mapping_store.applies) yet nothing could change "any" (None) to a specific
    # ERP like "netsuite" without a reseed. Making it settable lets a rule be
    # narrowed to one source (e.g. Alternate Name blank-if-equals on NetSuite only,
    # not eBOS) from the API/UI. None/"" clears it back to source-agnostic.
    source_erp: Optional[str] = None
    # Restrict a learning to specific interface sheets, or remove it from some.
    # Both empty = every sheet (the previous behaviour). Needed because Oracle
    # repeats a field name across sheets, so one approval used to reach all of
    # them — including sheets where the field must stay blank.
    sheets: Optional[list[str]] = None
    exclude_sheets: Optional[list[str]] = None


class LearnedMappingOut(ApiOut, LearnedMappingBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    captured_by: Optional[str] = None
    captured_at: datetime
    # These three are STORED and ACTED ON but were never returned, so the Learning
    # Center could not show them and the analyst had no way to confirm a setting had
    # taken. Found by reading the live payload on 29-Jul.
    #
    # source_erp: which legacy system this learning came from. Item mappings from
    # NetSuite and from SyteLine are different mappings for the same target field;
    # the engine already scopes by it, but with the field absent from the payload
    # the two are indistinguishable on screen.
    #
    # sheets / exclude_sheets: the per-interface-sheet scope. LearnedMappingUpdate
    # accepts both, so they were write-only — set an exclusion and nothing anywhere
    # reflected it back. Oracle repeats field names across sheets (Customer has 19),
    # and these are what stop one approval reaching all of them.
    source_erp: Optional[str] = None
    sheets: list[str] = []
    exclude_sheets: list[str] = []


class LearningStats(BaseModel):
    total: int
    objects_covered: int
    reusable_no_ai: int          # rules that resolve without any AI call
    times_applied: int           # times a learned rule was auto-applied to a conversion
    by_category: list[dict[str, Any]]
    by_source: list[dict[str, Any]] = []
