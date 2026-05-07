"""Conversion object dependency graph metadata."""
from sqlalchemy import Column, Integer, String, Text
from app.database import Base


class Dependency(Base):
    __tablename__ = "dependencies"

    id = Column(Integer, primary_key=True, index=True)
    source_object = Column(String(100), nullable=False)
    target_object = Column(String(100), nullable=False)
    relationship_type = Column(String(50), default="prerequisite")  # prerequisite | reference
    description = Column(Text, nullable=True)
