"""FBDI template endpoints."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.fbdi import FBDIField, FBDISheet, FBDITemplate
from app.models.user import User
from app.schemas.fbdi import (
    FBDIFieldOut, FBDIFieldUpdate, FBDISheetOut, FBDITemplateDetailOut, FBDITemplateOut,
)
from app.services.auth_service import get_current_user
from app.services.fbdi_service import create_template_from_upload

router = APIRouter(prefix="/api/fbdi", tags=["fbdi"])


@router.post("/upload", response_model=FBDITemplateDetailOut)
def upload_template(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    module: str | None = Form(None),
    business_object: str | None = Form(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        tpl = create_template_from_upload(db, file, name, module, business_object)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _detail_payload(tpl, db)


@router.get("/templates", response_model=list[FBDITemplateOut])
def list_templates(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(FBDITemplate).order_by(FBDITemplate.uploaded_at.desc()).all()


@router.get("/templates/{template_id}", response_model=FBDITemplateDetailOut)
def get_template(template_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    tpl = db.query(FBDITemplate).filter(FBDITemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(404, "Template not found")
    return _detail_payload(tpl, db)


@router.get("/templates/{template_id}/fields", response_model=list[FBDIFieldOut])
def list_template_fields(
    template_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return (
        db.query(FBDIField)
        .filter(FBDIField.template_id == template_id)
        .order_by(FBDIField.sequence)
        .all()
    )


@router.put("/fields/{field_id}", response_model=FBDIFieldOut)
def update_field(
    field_id: int,
    payload: FBDIFieldUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    field = db.query(FBDIField).filter(FBDIField.id == field_id).first()
    if not field:
        raise HTTPException(404, "Field not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(field, k, v)
    db.commit()
    db.refresh(field)
    return field


def _detail_payload(tpl: FBDITemplate, db: Session) -> dict:
    sheets = (
        db.query(FBDISheet)
        .filter(FBDISheet.template_id == tpl.id)
        .order_by(FBDISheet.sequence)
        .all()
    )
    field_count = db.query(FBDIField).filter(FBDIField.template_id == tpl.id).count()
    return {
        "id": tpl.id,
        "name": tpl.name,
        "module": tpl.module,
        "business_object": tpl.business_object,
        "version": tpl.version,
        "file_name": tpl.file_name,
        "status": tpl.status,
        "description": tpl.description,
        "uploaded_at": tpl.uploaded_at,
        "sheets": [FBDISheetOut.model_validate(s) for s in sheets],
        "field_count": field_count,
    }
