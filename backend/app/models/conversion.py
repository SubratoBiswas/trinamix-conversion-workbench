"""Conversion object — a single conversion stream within a Project.

Each Conversion is one source file → one FBDI target template. A Project
typically contains 30–50+ Conversions for a real implementation (Item Master,
Customer Master, Supplier Master, Sales Orders, Purchase Orders, BOMs, …).
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


CONVERSION_STATUSES = (
    "planning",            # placeholder; no source file or target yet
    "draft",               # source uploaded; not yet mapped
    "mapping_suggested",
    "awaiting_approval",
    "validated",
    "output_generated",
    "loaded",
    "failed",
)


class Conversion(Base):
    """One conversion object inside an engagement."""

    __tablename__ = "conversions"

    id = Column(Integer, primary_key=True, index=True)

    # Engagement parent
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Bindings — nullable so a conversion can be planned before its file or
    # target is decided.
    dataset_id = Column(Integer, ForeignKey("datasets.id"), nullable=True)
    template_id = Column(Integer, ForeignKey("fbdi_templates.id"), nullable=True)

    # The Fusion business object this conversion targets, e.g. "Item",
    # "Customer", "Sales Order". Used to compute project-scoped dependencies
    # even before a template is selected.
    target_object = Column(String(120), nullable=True, index=True)

    # Suggested load order within the project (1-based). Lower runs first.
    planned_load_order = Column(Integer, default=100)

    status = Column(String(50), default="planning")
    created_by = Column(String(150), default="admin")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="conversions")
    dataset = relationship("Dataset")
    template = relationship("FBDITemplate")

    mappings = relationship(
        "MappingSuggestion", back_populates="conversion",
        cascade="all, delete-orphan",
    )
    rules = relationship(
        "TransformationRule", back_populates="conversion",
        cascade="all, delete-orphan",
    )
    crosswalks = relationship(
        "Crosswalk", back_populates="conversion",
        cascade="all, delete-orphan",
    )
    validation_issues = relationship(
        "ValidationIssue", back_populates="conversion",
        cascade="all, delete-orphan",
    )
    outputs = relationship(
        "ConvertedOutput", back_populates="conversion",
        cascade="all, delete-orphan",
    )
    load_runs = relationship(
        "LoadRun", back_populates="conversion",
        cascade="all, delete-orphan",
    )
    workflows = relationship(
        "Workflow", back_populates="conversion",
        cascade="all, delete-orphan",
    )
