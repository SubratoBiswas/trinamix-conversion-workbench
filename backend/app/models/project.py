"""Project — a multi-month implementation engagement.

A Project represents the overall consulting engagement (e.g. "Acme SCM Cloud
Phase 1") and contains many Conversion objects (Item Master, Customer Master,
Sales Orders, etc.) that share the same client, target environment, go-live
wave, and approval chain.
"""
from datetime import datetime, date

from sqlalchemy import Column, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


PROJECT_STATUSES = (
    "planning",
    "in_progress",
    "ready_for_uat",
    "complete",
    "on_hold",
)


class Project(Base):
    """Implementation-engagement-level container."""

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    client = Column(String(150), nullable=True)
    target_environment = Column(String(150), nullable=True)
    go_live_date = Column(Date, nullable=True)
    owner = Column(String(150), default="admin")
    status = Column(String(50), default="planning")

    # Cutover window (used by the migration monitor / cutover dashboard)
    production_cutover_start = Column(DateTime, nullable=True)
    production_cutover_end = Column(DateTime, nullable=True)
    migration_lead = Column(String(150), nullable=True)
    data_owner = Column(String(150), nullable=True)
    sox_controlled = Column(Integer, default=1)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversions = relationship(
        "Conversion", back_populates="project", cascade="all, delete-orphan"
    )
    environments = relationship(
        "Environment", cascade="all, delete-orphan",
        order_by="Environment.sort_order",
    )
