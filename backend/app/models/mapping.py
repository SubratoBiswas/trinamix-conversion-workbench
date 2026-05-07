"""AI / rule-based mapping suggestions for a project."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Text, JSON
from sqlalchemy.orm import relationship
from app.database import Base


MAPPING_STATUSES = ("suggested", "approved", "rejected", "overridden", "not_applicable")


class MappingSuggestion(Base):
    __tablename__ = "mapping_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    conversion_id = Column(Integer, ForeignKey("conversions.id", ondelete="CASCADE"), nullable=False)
    target_field_id = Column(Integer, ForeignKey("fbdi_fields.id"), nullable=False)
    source_column = Column(String(255), nullable=True)
    confidence = Column(Float, default=0.0)  # 0..1
    reason = Column(Text)
    suggested_transformation = Column(JSON, nullable=True)  # {rule_type, config}
    review_required = Column(Integer, default=1)  # 0/1 flag
    status = Column(String(50), default="suggested")
    default_value = Column(String(500), nullable=True)
    comment = Column(Text, nullable=True)
    approved_by = Column(String(150), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversion = relationship("Conversion", back_populates="mappings")
    target_field = relationship("FBDIField")
