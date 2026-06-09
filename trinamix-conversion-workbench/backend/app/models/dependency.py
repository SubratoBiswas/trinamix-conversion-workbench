"""Conversion dependency graph."""
from typing import Optional
from beanie import Document

class Dependency(Document):
    source_object: str
    target_object: str
    relationship_type: str = "prerequisite"
    description: Optional[str] = None

    class Settings:
        name = "dependencies"
