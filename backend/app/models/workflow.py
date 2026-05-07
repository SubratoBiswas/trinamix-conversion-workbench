"""Visual workflow / dataflow definitions."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    conversion_id = Column(Integer, ForeignKey("conversions.id", ondelete="SET NULL"), nullable=True)
    nodes = Column(JSON, default=list)
    edges = Column(JSON, default=list)
    status = Column(String(50), default="draft")  # draft | saved | running | success | failed
    last_run_at = Column(DateTime, nullable=True)
    last_run_summary = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversion = relationship("Conversion", back_populates="workflows")
