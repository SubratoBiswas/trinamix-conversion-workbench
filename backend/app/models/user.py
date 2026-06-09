"""User account model."""
from datetime import datetime
from typing import Optional
from beanie import Document
from pydantic import Field


class User(Document):
    name: str
    email: str
    role: str = "admin"
    password_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "users"
        indexes = ["email"]
