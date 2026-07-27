"""Dataset endpoints."""
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.models.dataset import Dataset, DatasetColumnProfile
from app.models.fbdi import FBDITemplate
from app.models.user import User
from app.schemas.dataset import DatasetDetailOut, DatasetOut, DatasetPreviewOut
from app.services.auth_service import get_current_user
from app.services.dataset_service import (
    create_dataset_from_upload, get_dataset_preview, detect_dataset_type,
    detect_source_system, column_signature,
)

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _ds_out(ds: Dataset, columns=None) -> dict:
    d = ds.model_dump()
    d["id"] = str(ds.id)
    if columns is not None:
        d["columns"] = [{"id": str(c.id), **{k: v for k, v in c.model_dump().items() if k != "id"}} for c in columns]
    return d


@router.post("/peek-sheets")
async def peek_sheets(
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
):
    """List a workbook's sheets (name + stored dimensions) WITHOUT persisting a
    dataset, so the upload UI can prompt when a workbook has several data sheets
    (Issue #1 — e.g. a Customer + Address workbook). CSV/single-sheet files report
    one implicit sheet."""
    import tempfile
    from pathlib import Path as _P
    from app.parsers.tabular_parser import list_excel_sheets
    ext = _P(file.filename or "").suffix.lower()
    data = await file.read()
    try:
        with tempfile.NamedTemporaryFile(suffix=ext or ".bin", delete=False) as tf:
            tf.write(data)
            tmp = tf.name
        sheets = list_excel_sheets(tmp)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not read '{file.filename}': {exc}")
    finally:
        try:
            _P(tmp).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    return {"file_name": file.filename, "multi": len(sheets) > 1, "sheets": sheets}


@router.post("/upload", response_model=DatasetDetailOut)
async def upload_dataset(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    description: str | None = Form(None),
    sheet: str | None = Form(None),
    _: User = Depends(get_current_user),
):
    try:
        ds, columns = await create_dataset_from_upload(file, name, description, sheet=sheet)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — surface a clear parse reason, not a 500
        raise HTTPException(400, f"Could not read '{file.filename}': {exc}")
    return _ds_out(ds, columns)


@router.get("", response_model=list[DatasetOut])
async def list_datasets(_: User = Depends(get_current_user)):
    from app.models.conversion import Conversion
    datasets = await Dataset.find_all().sort("-uploaded_at").to_list()
    # Map dataset_id -> [conversion names] so each card can show what uses it.
    convs = await Conversion.find_all().to_list()
    by_ds: dict[str, list[str]] = {}
    for c in convs:
        if c.dataset_id:
            by_ds.setdefault(str(c.dataset_id), []).append(c.name)
    out = []
    for ds in datasets:
        d = _ds_out(ds)
        names = by_ds.get(str(ds.id), [])
        d["conversion_names"] = names
        d["conversion_count"] = len(names)
        out.append(d)
    return out


@router.delete("/{dataset_id}")
async def delete_dataset(
    dataset_id: str, cascade: bool = False, _: User = Depends(get_current_user)
):
    """Delete a dataset, its column profiles, and its stored file. If a conversion
    references it, blocked with 409 unless ?cascade=true, which also deletes those
    conversions."""
    from app.models.conversion import Conversion
    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    convs = await Conversion.find(Conversion.dataset_id == ds.id).to_list()
    if convs and not cascade:
        raise HTTPException(
            409,
            f"Can't delete — {len(convs)} conversion(s) still use this dataset. "
            "Delete with cascade, or remove those conversions first.",
        )
    deleted_convs = 0
    for c in convs:  # cascade == True here
        await c.delete()
        deleted_convs += 1
    await DatasetColumnProfile.find(DatasetColumnProfile.dataset_id == ds.id).delete()
    try:
        import os
        if ds.file_path and os.path.exists(ds.file_path):
            os.remove(ds.file_path)
    except Exception:  # noqa: BLE001 — file cleanup is best-effort
        pass
    await ds.delete()
    return {"deleted": True, "id": dataset_id, "conversions_deleted": deleted_convs}


@router.get("/{dataset_id}", response_model=DatasetDetailOut)
async def get_dataset(dataset_id: str, _: User = Depends(get_current_user)):
    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    columns = await DatasetColumnProfile.find(
        DatasetColumnProfile.dataset_id == ds.id
    ).sort("position").to_list()
    return _ds_out(ds, columns)


@router.get("/{dataset_id}/preview", response_model=DatasetPreviewOut)
async def preview_dataset(dataset_id: str, limit: int = 50, _: User = Depends(get_current_user)):
    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    return await get_dataset_preview(ds, limit=limit)


@router.get("/{dataset_id}/profile", response_model=DatasetDetailOut)
async def get_profile(dataset_id: str, _: User = Depends(get_current_user)):
    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    columns = await DatasetColumnProfile.find(
        DatasetColumnProfile.dataset_id == ds.id
    ).sort("position").to_list()
    return _ds_out(ds, columns)


@router.get("/{dataset_id}/anomalies")
async def dataset_anomalies(
    dataset_id: str,
    use_ai: bool = False,
    max_rows: int = 5000,
    _: User = Depends(get_current_user),
):
    """Source-data anomaly / outlier detection — profile the raw extract BEFORE
    mapping and flag the data problems (nulls, mixed types, outliers, embedded
    units, casing/whitespace variants, non-printables, duplicate rows) that later
    fail the FBDI load. Deterministic; ``use_ai=true`` adds a risk note per finding."""
    import asyncio as _aio
    from app.parsers import parse_tabular
    from app.services.dataset_file_store import materialize_dataset_file
    from app.services.anomaly_service import detect_anomalies, ai_review_anomalies

    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    path = await materialize_dataset_file(ds)
    if not path:
        raise HTTPException(404, "Source file not available for this dataset")
    ext = (ds.file_type or "csv").lstrip(".")
    df = await _aio.to_thread(parse_tabular, str(path), ext, max(200, max_rows))
    result = await _aio.to_thread(detect_anomalies, df)
    result["dataset_id"] = dataset_id
    result["dataset_name"] = ds.file_name
    if use_ai:
        result = await ai_review_anomalies(result, getattr(ds, "detected_object_type", None))
    return result


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


@router.get("/{dataset_id}/classify")
async def classify_dataset(dataset_id: str, _: User = Depends(get_current_user)):
    """AI classification for an uploaded file: which source system it came from
    and which Fusion FBDI target it maps to. Consults prior learned choices for
    files with the same column signature so repeat conversions auto-recommend."""
    from app.models.learned import LearnedMapping

    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    col_profiles = await DatasetColumnProfile.find(
        DatasetColumnProfile.dataset_id == ds.id
    ).sort("position").to_list()
    column_names = [p.column_name for p in col_profiles]
    templates = await FBDITemplate.find_all().to_list()

    sources = detect_source_system(ds.file_name, column_names)
    targets = detect_dataset_type(ds.file_name, column_names, templates)
    sig = column_signature(column_names)

    # Prior learning for this exact column signature wins (higher confidence).
    learned = await LearnedMapping.find(
        LearnedMapping.kind == "file_signature",
        LearnedMapping.original_value == sig,
    ).sort("-captured_at").to_list()
    learned_hit = learned[0] if learned else None
    source_default = sources[0]["code"] if sources else "custom"
    target_default = targets[0]["template_id"] if targets else None
    if learned_hit:
        source_default = learned_hit.resolved_value or source_default
        if learned_hit.target_field:  # stored template id
            target_default = learned_hit.target_field

    # Persist the detection on the dataset.
    await ds.set({
        "source_system": source_default,
        "source_confidence": (sources[0]["confidence"] if sources else 0.0),
        "detected_object_type": (targets[0]["business_object"] if targets else None),
        "detection_confidence": (targets[0]["confidence"] if targets else 0.0),
    })

    return {
        "dataset_id": dataset_id,
        "signature": sig,
        "learned": bool(learned_hit),
        "source": {
            "detected": source_default,
            "candidates": sources,
        },
        "target": {
            "detected_template_id": target_default,
            "suggestions": targets,
        },
    }


@router.post("/{dataset_id}/classify-learn")
async def classify_learn(dataset_id: str, body: dict, _: User = Depends(get_current_user)):
    """Remember the user-confirmed source system + target template for a file's
    column signature, so the next similar file is auto-recommended correctly."""
    from app.models.learned import LearnedMapping

    ds = await Dataset.get(PydanticObjectId(dataset_id))
    if not ds:
        raise HTTPException(404, "Dataset not found")
    source_system = (body or {}).get("source_system")
    template_id = (body or {}).get("template_id")
    target_object = (body or {}).get("target_object")
    col_profiles = await DatasetColumnProfile.find(
        DatasetColumnProfile.dataset_id == ds.id
    ).to_list()
    sig = column_signature([p.column_name for p in col_profiles])

    # De-dup: update the existing signature record if present.
    existing = await LearnedMapping.find_one(
        LearnedMapping.kind == "file_signature",
        LearnedMapping.original_value == sig,
    )
    if existing:
        await existing.set({
            "resolved_value": source_system or existing.resolved_value,
            "target_field": template_id or existing.target_field,
            "target_object": target_object or existing.target_object,
        })
        rec = existing
    else:
        rec = LearnedMapping(
            kind="file_signature", category="File Classification",
            original_value=sig, resolved_value=source_system or "custom",
            target_object=target_object, target_field=template_id,
            captured_from="file-classify",
        )
        await rec.insert()

    await ds.set({"source_system": source_system or ds.source_system})
    return {"learned": True, "signature": sig, "id": str(rec.id)}
