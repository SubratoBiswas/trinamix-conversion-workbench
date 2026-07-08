"""Global application settings (singleton key/value)."""
from datetime import datetime

from beanie import Document
from pydantic import Field


class AppSetting(Document):
    key: str
    value: str
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "app_settings"
        indexes = ["key"]
