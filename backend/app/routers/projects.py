"""Projects (engagement) router.

A Project is the implementation engagement (e.g. "Acme SCM Phase 1") that
contains many Conversion objects.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.conversion import Conversion
from app.models.project import Project
from app.models.user import User
from app.schemas.conversion import ConversionOut
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _hydrate(db: Session, p: Project) -> ProjectOut:
    """Compute conversion roll-ups for the engagement card view."""
    convs = db.query(Conversion).filter(Conversion.project_id == p.id).all()
    in_progress = sum(
        1 for c in convs
        if c.status in ("draft", "mapping_suggested", "awaiting_approval", "validated", "output_generated")
    )
    loaded = sum(1 for c in convs if c.status == "loaded")
    failed = sum(1 for c in convs if c.status == "failed")
    out = ProjectOut.model_validate(p)
    out.conversion_count = len(convs)
    out.in_progress_count = in_progress
    out.loaded_count = loaded
    out.failed_count = failed
    return out


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return [_hydrate(db, p) for p in db.query(Project).order_by(Project.id.desc()).all()]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    return _hydrate(db, p)


@router.post("", response_model=ProjectOut)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = Project(**payload.model_dump(exclude_unset=True), owner=payload.owner or user.email)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _hydrate(db, p)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _hydrate(db, p)


@router.delete("/{project_id}")
def delete_project(
    project_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")
    db.delete(p)
    db.commit()
    return {"deleted": project_id}


@router.get("/{project_id}/conversions", response_model=list[ConversionOut])
def list_conversions_for_project(
    project_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    """List all conversions inside an engagement, ordered by planned load order."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(404, "Project not found")

    out: list[ConversionOut] = []
    for c in (
        db.query(Conversion)
        .filter(Conversion.project_id == project_id)
        .order_by(Conversion.planned_load_order, Conversion.id)
        .all()
    ):
        co = ConversionOut.model_validate(c)
        co.dataset_name = c.dataset.name if c.dataset else None
        co.template_name = c.template.name if c.template else None
        co.project_name = p.name
        out.append(co)
    return out
