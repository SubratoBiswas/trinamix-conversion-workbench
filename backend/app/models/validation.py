"""Validation and cleansing issues raised on a project."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from app.database import Base


SEVERITIES = ("info", "warning", "error", "critical")
ISSUE_CATEGORIES = ("cleansing", "validation")


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id = Column(Integer, primary_key=True, index=True)
    conversion_id = Column(Integer, ForeignKey("conversions.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(50), default="validation")  # cleansing | validation
    row_number = Column(Integer, nullable=True)
    field_name = Column(String(255), nullable=True)
    issue_type = Column(String(100), nullable=False)
    severity = Column(String(20), default="warning")
    message = Column(Text, nullable=False)
    suggested_fix = Column(Text, nullable=True)
    auto_fixable = Column(Boolean, default=False)
    impacted_count = Column(Integer, default=1)
    status = Column(String(50), default="open")  # open | resolved | ignored
    created_at = Column(DateTime, default=datetime.utcnow)

    conversion = relationship("Conversion", back_populates="validation_issues")
