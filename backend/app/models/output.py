"""Generated Fusion-ready output artifacts."""
from datetime import datetime
from beanie import Document, PydanticObjectId
from pydantic import Field

class ConvertedOutput(Document):
    conversion_id: PydanticObjectId
    output_file_path: str
    output_file_name: str
    row_count: int = 0
    column_count: int = 0
    status: str = "generated"
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "converted_outputs"
        indexes = ["conversion_id"]
