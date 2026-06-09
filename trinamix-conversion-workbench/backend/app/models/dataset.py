"""Dataset and column profile models."""
from datetime import datetime
from typing import Any, Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

class Dataset(Document):
    name: str
    description: Optional[str] = None
    file_name: str
    file_path: str
    file_type: str  # csv | xlsx
    row_count: int = 0
    column_count: int = 0
    status: str = "profiled"
    detected_object_type: Optional[str] = None
    detection_confidence: float = 0.0
    detection_suggestions: list = Field(default_factory=list)
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "datasets"

class DatasetColumnProfile(Document):
    dataset_id: PydanticObjectId
    column_name: str
    position: int = 0
    inferred_type: Optional[str] = None
    null_count: int = 0
    null_percent: float = 0.0
    distinct_count: int = 0
    sample_values: list[Any] = Field(default_factory=list)
    min_value: Optional[str] = None
    max_value: Optional[str] = None
    pattern_summary: Optional[str] = None

    class Settings:
        name = "dataset_column_profiles"
        indexes = ["dataset_id"]
