"""Conversions router — CRUD for individual conversion objects.

A Conversion is one source-file → one FBDI-target unit of work, scoped to a
parent Project (engagement). All downstream operations (mapping, validation,
output, load) hang off a Conversion.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.conversion import Conversion
from app.models.project import Project
from app.models.user import User
from app.schemas.conversion import ConversionCreate, ConversionOut, ConversionUpdate
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/conversions", tags=["conversions"])


def _hydrate(db: Session, c: Conversion) -> ConversionOut:
    out = ConversionOut.model_validate(c)
    out.dataset_name = c.dataset.name if c.dataset else None
    out.template_name = c.template.name if c.template else None
    out.project_name = c.project.name if c.project else None
    return out


def _resolve_status(c: Conversion) -> str:
    """Default a conversion's status from its bindings if not set."""
    if c.status:
        return c.status
    if c.dataset_id and c.template_id:
        return "draft"
    return "planning"


@router.get("", response_model=list[ConversionOut])
def list_conversions(
    project_id: int | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(Conversion)
    if project_id is not None:
        q = q.filter(Conversion.project_id == project_id)
    if status:
        q = q.filter(Conversion.status == status)
    return [
        _hydrate(db, c)
        for c in q.order_by(Conversion.planned_load_order, Conversion.id).all()
    ]


@router.get("/{conversion_id}", response_model=ConversionOut)
def get_conversion(
    conversion_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    c = db.query(Conversion).filter(Conversion.id == conversion_id).first()
    if not c:
        raise HTTPException(404, "Conversion not found")
    return _hydrate(db, c)


@router.post("", response_model=ConversionOut)
def create_conversion(
    payload: ConversionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj = db.query(Project).filter(Project.id == payload.project_id).first()
    if not proj:
        raise HTTPException(400, f"Project {payload.project_id} does not exist")

    c = Conversion(
        **payload.model_dump(exclude_unset=True, exclude={"status"}),
        created_by=user.email,
    )
    if payload.status:
        c.status = payload.status
    elif c.dataset_id and c.template_id:
        c.status = "draft"
    else:
        c.status = "planning"

    db.add(c)
    db.commit()
    db.refresh(c)
    return _hydrate(db, c)


@router.patch("/{conversion_id}", response_model=ConversionOut)
def update_conversion(
    conversion_id: int,
    payload: ConversionUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    c = db.query(Conversion).filter(Conversion.id == conversion_id).first()
    if not c:
        raise HTTPException(404, "Conversion not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    # If now fully bound and still in planning, advance to draft
    if c.status == "planning" and c.dataset_id and c.template_id:
        c.status = "draft"
    db.commit()
    db.refresh(c)
    return _hydrate(db, c)


@router.delete("/{conversion_id}")
def delete_conversion(
    conversion_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    c = db.query(Conversion).filter(Conversion.id == conversion_id).first()
    if not c:
        raise HTTPException(404, "Conversion not found")
    db.delete(c)
    db.commit()
    return {"deleted": conversion_id}
