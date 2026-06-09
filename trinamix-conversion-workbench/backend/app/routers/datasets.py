"""Dataset endpoints."""
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.models.dataset import Dataset, DatasetColumnProfile
from app.models.fbdi import FBDITemplate
from app.models.user import User
from app.schemas.dataset import DatasetDetailOut, DatasetOut, DatasetPreviewOut
from app.services.auth_service import get_current_user
from app.services.dataset_service import create_dataset_from_upload, get_dataset_preview, detect_dataset_type

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _ds_out(ds: Dataset, columns=None) -> dict:
    d = ds.model_dump()
    d["id"] = str(ds.id)
    if columns is not None:
        d["columns"] = [{"id": str(c.id), **{k: v for k, v in c.model_dump().items() if k != "id"}} for c in columns]
    return d


@router.post("/upload", response_model=DatasetDetailOut)
async def upload_dataset(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    description: str | None = Form(None),
    _: User = Depends(get_current_user),
):
    try:
        ds, columns = await create_dataset_from_upload(file, name, description)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _ds_out(ds, columns)


@router.get("", response_model=list[DatasetOut])
async def list_datasets(_: User = Depends(get_current_user)):
    datasets = await Dataset.find_all().sort(-Dataset.uploaded_at).to_list()
    return [_ds_out(ds) for ds in datasets]


@router.get("/{dataset_id}", response_model=DatasetDetailOut)
async def get_dataset(dataset_id: str, _: User = Depends(get_current_user)):
    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    columns = await DatasetColumnProfile.find(
        DatasetColumnProfile.dataset_id == ds.id
    ).sort(+DatasetColumnProfile.position).to_list()
    return _ds_out(ds, columns)


@router.get("/{dataset_id}/preview", response_model=DatasetPreviewOut)
async def preview_dataset(dataset_id: str, limit: int = 50, _: User = Depends(get_current_user)):
    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return get_dataset_preview(ds, limit=limit)


@router.get("/{dataset_id}/profile", response_model=DatasetDetailOut)
async def get_profile(dataset_id: str, _: User = Depends(get_current_user)):
    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    columns = await DatasetColumnProfile.find(
        DatasetColumnProfile.dataset_id == ds.id
    ).sort(+DatasetColumnProfile.position).to_list()
    return _ds_out(ds, columns)


@router.get("/{dataset_id}/suggest-template")
async def suggest_template_for_dataset(dataset_id: str, _: User = Depends(get_current_user)):
    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    col_profiles = await DatasetColumnProfile.find(
        DatasetColumnProfile.dataset_id == ds.id
    ).to_list()
    column_names = [p.column_name for p in col_profiles]
    templates = await FBDITemplate.find_all().to_list()
    suggestions = detect_dataset_type(ds.file_name, column_names, templates)
    return {"dataset_id": dataset_id, "suggestions": suggestions}
