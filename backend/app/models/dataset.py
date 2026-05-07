"""Dataset and column-profile models."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text, Float
from sqlalchemy.orm import relationship
from app.database import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    file_name = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=False)
    file_type = Column(String(20), nullable=False)  # csv | xlsx
    row_count = Column(Integer, default=0)
    column_count = Column(Integer, default=0)
    status = Column(String(50), default="profiled")  # uploaded | profiling | profiled | error
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    columns = relationship(
        "DatasetColumnProfile", back_populates="dataset", cascade="all, delete-orphan"
    )


class DatasetColumnProfile(Base):
    __tablename__ = "dataset_column_profiles"

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False)
    column_name = Column(String(255), nullable=False)
    position = Column(Integer, default=0)
    inferred_type = Column(String(50))  # string | integer | float | date | boolean
    null_count = Column(Integer, default=0)
    null_percent = Column(Float, default=0.0)
    distinct_count = Column(Integer, default=0)
    sample_values = Column(JSON, default=list)
    min_value = Column(String(255), nullable=True)
    max_value = Column(String(255), nullable=True)
    pattern_summary = Column(String(500), nullable=True)

    dataset = relationship("Dataset", back_populates="columns")
