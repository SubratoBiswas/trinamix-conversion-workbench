"""Load orchestration: simulated/actual loads and their errors."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.database import Base


LOAD_RUN_TYPES = ("simulate", "fusion")
LOAD_STATUSES = ("running", "completed", "failed")
ERROR_CATEGORIES = (
    "Missing Required Field",
    "Invalid Format",
    "Invalid Lookup",
    "Missing Dependency",
    "Duplicate Record",
    "Transformation Error",
    "Data Quality Warning",
)


class LoadRun(Base):
    __tablename__ = "load_runs"

    id = Column(Integer, primary_key=True, index=True)
    conversion_id = Column(Integer, ForeignKey("conversions.id", ondelete="CASCADE"), nullable=False)
    run_type = Column(String(50), default="simulate")
    status = Column(String(50), default="running")
    total_records = Column(Integer, default=0)
    passed_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    warning_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    conversion = relationship("Conversion", back_populates="load_runs")
    errors = relationship("LoadError", back_populates="load_run", cascade="all, delete-orphan")


class LoadError(Base):
    __tablename__ = "load_errors"

    id = Column(Integer, primary_key=True, index=True)
    load_run_id = Column(Integer, ForeignKey("load_runs.id", ondelete="CASCADE"), nullable=False)
    row_number = Column(Integer, nullable=True)
    object_name = Column(String(100))
    error_category = Column(String(100))
    error_message = Column(Text)
    root_cause = Column(Text, nullable=True)
    related_dependency = Column(String(255), nullable=True)
    # The actual key value that failed to resolve (e.g. "ITM-DELETED").
    # Lets the Error Traceback drawer render the connection path explicitly
    # without parsing it back out of error_message.
    reference_value = Column(String(255), nullable=True)
    suggested_fix = Column(Text, nullable=True)

    load_run = relationship("LoadRun", back_populates="errors")
