"""Generated Fusion-ready output artifacts."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class ConvertedOutput(Base):
    __tablename__ = "converted_outputs"

    id = Column(Integer, primary_key=True, index=True)
    conversion_id = Column(Integer, ForeignKey("conversions.id", ondelete="CASCADE"), nullable=False)
    output_file_path = Column(String(1000), nullable=False)
    output_file_name = Column(String(500), nullable=False)
    row_count = Column(Integer, default=0)
    column_count = Column(Integer, default=0)
    status = Column(String(50), default="generated")
    generated_at = Column(DateTime, default=datetime.utcnow)

    conversion = relationship("Conversion", back_populates="outputs")
