"""Dataset endpoints."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.dataset import Dataset
from app.models.user import User
from app.schemas.dataset import DatasetDetailOut, DatasetOut, DatasetPreviewOut
from app.services.auth_service import get_current_user
from app.services.dataset_service import create_dataset_from_upload, get_dataset_preview

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.post("/upload", response_model=DatasetDetailOut)
def upload_dataset(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    description: str | None = Form(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        ds = create_dataset_from_upload(db, file, name, description)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return ds


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Dataset).order_by(Dataset.uploaded_at.desc()).all()


@router.get("/{dataset_id}", response_model=DatasetDetailOut)
def get_dataset(dataset_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return ds


@router.get("/{dataset_id}/preview", response_model=DatasetPreviewOut)
def preview_dataset(
    dataset_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return get_dataset_preview(ds, limit=limit)


@router.get("/{dataset_id}/profile", response_model=DatasetDetailOut)
def get_profile(dataset_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    ds = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return ds
