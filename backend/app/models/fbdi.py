"""Oracle FBDI template metadata models."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Text, JSON
from sqlalchemy.orm import relationship
from app.database import Base


class FBDITemplate(Base):
    __tablename__ = "fbdi_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    module = Column(String(100))           # GL | LE | CM | AP | TAX | SCM | HCM | EXP | AR | PO | OM | FA | PPM | PAY | MFG
    business_object = Column(String(100))  # Item | Customer | SalesOrder ...

    # Tier — captures load order across an engagement.
    #   T0 = Configuration (UOM, Inv Org, COA, Bank Accounts, Tax setup)
    #   T1 = Master data (Items, Customers, Suppliers, Workers, Projects)
    #   T2 = Transactional (Sales Orders, POs, Invoices, Journals)
    #   T3 = Closing / Historical (Open balances, AR/AP aging, Commissions)
    tier = Column(String(4), default="T1")

    # Phase — current state of this template inside the engagement.
    #   "Blueprint"  - Template registered, scope agreed
    #   "Build"      - Mapping + transformations under construction
    #   "Validation" - Validated against business rules
    #   "Cutover"    - Ready for production cutover
    phase = Column(String(20), default="Blueprint")
    required_field_count = Column(Integer, default=0)

    version = Column(String(50), default="1.0")
    file_name = Column(String(500))
    file_path = Column(String(1000))
    status = Column(String(50), default="parsed")  # parsed | manual | error
    description = Column(Text)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    sheets = relationship("FBDISheet", back_populates="template", cascade="all, delete-orphan")
    fields = relationship("FBDIField", back_populates="template", cascade="all, delete-orphan")


class FBDISheet(Base):
    __tablename__ = "fbdi_sheets"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("fbdi_templates.id", ondelete="CASCADE"), nullable=False)
    sheet_name = Column(String(255), nullable=False)
    sequence = Column(Integer, default=0)
    field_count = Column(Integer, default=0)

    template = relationship("FBDITemplate", back_populates="sheets")
    fields = relationship("FBDIField", back_populates="sheet", cascade="all, delete-orphan")


class FBDIField(Base):
    __tablename__ = "fbdi_fields"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("fbdi_templates.id", ondelete="CASCADE"), nullable=False)
    sheet_id = Column(Integer, ForeignKey("fbdi_sheets.id", ondelete="CASCADE"), nullable=False)
    field_name = Column(String(255), nullable=False)
    display_name = Column(String(255))
    description = Column(Text)
    required = Column(Boolean, default=False)
    data_type = Column(String(50))  # Character | Number | Date | Decimal
    max_length = Column(Integer, nullable=True)
    format_mask = Column(String(100), nullable=True)
    sample_value = Column(String(500), nullable=True)
    lookup_type = Column(String(255), nullable=True)
    validation_notes = Column(Text, nullable=True)
    sequence = Column(Integer, default=0)
    required_modules = Column(JSON, default=list)  # list of module names that require this field

    template = relationship("FBDITemplate", back_populates="fields")
    sheet = relationship("FBDISheet", back_populates="fields")
