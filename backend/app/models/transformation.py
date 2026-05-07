"""Transformation rules and value crosswalks."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from app.database import Base


RULE_TYPES = (
    "TRIM",
    "UPPERCASE",
    "LOWERCASE",
    "TITLE_CASE",
    "REMOVE_HYPHEN",
    "REMOVE_SPECIAL_CHARS",
    "REPLACE",
    "REGEX_REPLACE",
    "REGEX_EXTRACT",
    "PAD",
    "SUBSTRING",
    "DEFAULT_VALUE",
    "CONSTANT",
    "VALUE_MAP",
    "DATE_FORMAT",
    "NUMBER_FORMAT",
    "ARITHMETIC",
    "CONCAT",
    "SPLIT",
    "COALESCE",
    "CONDITIONAL",
    "CASE_WHEN",
    "COMPUTED",
    "CROSSWALK_LOOKUP",
)


class TransformationRule(Base):
    __tablename__ = "transformation_rules"

    id = Column(Integer, primary_key=True, index=True)
    conversion_id = Column(Integer, ForeignKey("conversions.id", ondelete="CASCADE"), nullable=False)
    target_field_id = Column(Integer, ForeignKey("fbdi_fields.id"), nullable=True)
    source_column = Column(String(255), nullable=True)
    rule_type = Column(String(50), nullable=False)
    rule_config = Column(JSON, default=dict)
    description = Column(Text)
    sequence = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversion = relationship("Conversion", back_populates="rules")
    target_field = relationship("FBDIField")


class Crosswalk(Base):
    __tablename__ = "crosswalks"

    id = Column(Integer, primary_key=True, index=True)
    conversion_id = Column(Integer, ForeignKey("conversions.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    field_name = Column(String(255), nullable=False)
    source_value = Column(String(500), nullable=False)
    target_value = Column(String(500), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversion = relationship("Conversion", back_populates="crosswalks")
