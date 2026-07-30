"""Transformation rules and value crosswalks."""
from datetime import datetime
from typing import Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

RULE_TYPES = (
    "TRIM","UPPERCASE","LOWERCASE","TITLE_CASE","REMOVE_HYPHEN",
    "REMOVE_SPECIAL_CHARS","REPLACE","REGEX_REPLACE","REGEX_EXTRACT",
    "PAD","SUBSTRING","DEFAULT_VALUE","CONSTANT","VALUE_MAP",
    "DATE_FORMAT","NUMBER_FORMAT","ARITHMETIC","CONCAT","SPLIT",
    "COALESCE","CONDITIONAL","CASE_WHEN","MAP_BOOLEAN","CONDITIONAL_DATE",
    "COMPUTED","CROSSWALK_LOOKUP","PHONE_PART","PREFIX","SUFFIX",
    # CW #23 — a unique running key with an optional per-row variant
    # ("NXT000001", and a "_C1" form where the party is a PERSON).
    "SEQUENCE",
    # CW #19 — append one of several suffixes chosen by a condition ("_b" on a
    # bill-to row, "_s" on a ship-to row). SUFFIX can only append a fixed string.
    "SUFFIX_WHEN",
)

class TransformationRule(Document):
    conversion_id: PydanticObjectId
    target_field_id: Optional[PydanticObjectId] = None
    source_column: Optional[str] = None
    rule_type: str
    rule_config: dict = Field(default_factory=dict)
    description: Optional[str] = None
    sequence: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "transformation_rules"
        indexes = ["conversion_id"]

class Crosswalk(Document):
    conversion_id: PydanticObjectId
    name: str
    field_name: str
    source_value: str
    target_value: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "crosswalks"
        indexes = ["conversion_id"]
