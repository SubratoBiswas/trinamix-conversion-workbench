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
