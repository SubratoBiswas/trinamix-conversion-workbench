"""Environment + EnvironmentRun models.

A `Project` (engagement) has a fixed set of environments — typically DEV →
QA → UAT → PROD. Each Conversion gets re-executed against each environment
in turn (with potentially a different source dataset uploaded for that env).

EnvironmentRun captures one (Conversion × Environment) execution: which
dataset was uploaded for that environment, what status it's in, when it ran.
The same dataflow / mappings / transformations are reused across environments
— only the source data changes.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


# Standard environment names + colours (matched in the UI cutover dashboard)
DEFAULT_ENVIRONMENTS = [
    {"name": "DEV", "order": 1, "color": "info",    "description": "Development build & blueprint validation"},
    {"name": "QA",  "order": 2, "color": "brand",   "description": "Functional QA cycle"},
    {"name": "UAT", "order": 3, "color": "warning", "description": "User acceptance testing"},
    {"name": "PROD","order": 4, "color": "danger",  "description": "Production cutover (SOX-controlled)"},
]


ENV_RUN_STATUSES = (
    "pending",     # not started in this environment yet
    "running",     # in flight
    "complete",    # finished cleanly
    "failed",      # finished with errors
    "blocked",     # cannot start — dependency upstream failed
)


class Environment(Base):
    """One environment instance scoped to a Project (engagement)."""

    __tablename__ = "environments"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String(50), nullable=False)            # DEV / QA / UAT / PROD
    description = Column(Text)
    sort_order = Column(Integer, default=1)
    color = Column(String(20), default="info")
    sox_controlled = Column(Integer, default=0)          # 1 for PROD, 0 elsewhere
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", overlaps="environments")
    runs = relationship(
        "EnvironmentRun", back_populates="environment", cascade="all, delete-orphan"
    )


class EnvironmentRun(Base):
    """One execution of a Conversion in a specific environment."""

    __tablename__ = "environment_runs"

    id = Column(Integer, primary_key=True, index=True)
    environment_id = Column(Integer, ForeignKey("environments.id"), nullable=False, index=True)
    conversion_id = Column(Integer, ForeignKey("conversions.id"), nullable=False, index=True)

    # Each environment can have a different dataset uploaded — the dataflow is
    # reused but the source data is environment-specific.
    dataset_id = Column(Integer, ForeignKey("datasets.id"), nullable=True)

    status = Column(String(50), default="pending")
    stage = Column(String(80))                            # e.g. "FBDI Generation", "Validate", "Master Data Load"
    record_count = Column(Integer)
    passed_count = Column(Integer)
    failed_count = Column(Integer)

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    notes = Column(Text)

    environment = relationship("Environment", back_populates="runs")
    conversion = relationship("Conversion")
    dataset = relationship("Dataset")
