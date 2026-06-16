"""Conversions router."""
from datetime import datetime
from typing import Optional
from beanie import PydanticObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.fbdi import FBDITemplate
from app.models.project import Project
from app.models.user import User
from app.schemas.conversion import ConversionCreate, ConversionOut, ConversionUpdate
from app.services.auth_service import get_current_user


async def _auto_map(conversion_id) -> None:
    """Run AI mapping suggestions in the background if none exist yet."""
    try:
        from app.models.mapping import MappingSuggestion
        from app.services.mapping_service import run_mapping_suggestions
        existing = await MappingSuggestion.find(
            MappingSuggestion.conversion_id == conversion_id
        ).count()
        if existing == 0:
            conv = await Conversion.get(conversion_id)
            if conv and conv.dataset_id and conv.template_id:
                await run_mapping_suggestions(conv)
    except Exception:
        pass  # Background task -- never crash the request

router = APIRouter(prefix="/api/conversions", tags=["conversions"])


async def _hydrate(c: Conversion) -> ConversionOut:
    """Hydrate a single conversion (used for create/update responses)."""
    data = {**c.model_dump(), "id": str(c.id), "project_id": str(c.project_id)}
    if c.dataset_id:
        data["dataset_id"] = str(c.dataset_id)
        ds = await Dataset.get(c.dataset_id)
        data["dataset_name"] = ds.name if ds else None
    if c.template_id:
        data["template_id"] = str(c.template_id)
        tmpl = await FBDITemplate.get(c.template_id)
        data["template_name"] = tmpl.name if tmpl else None
    proj = await Project.get(c.project_id)
    data["project_name"] = proj.name if proj else None
    return ConversionOut(**data)


async def _hydrate_bulk(convs: list[Conversion]) -> list[ConversionOut]:
    """Hydrate many conversions with bulk lookups — avoids N+1 queries."""
    if not convs:
        return []

    # Collect unique IDs
    project_ids = list({c.project_id for c in convs})
    dataset_ids = list({c.dataset_id for c in convs if c.dataset_id})
    template_ids = list({c.template_id for c in convs if c.template_id})

    # Bulk fetch in 3 queries instead of 3*N
    projects_list = await Project.find({"_id": {"$in": project_ids}}).to_list()
    datasets_list = await Dataset.find({"_id": {"$in": dataset_ids}}).to_list() if dataset_ids else []
    templates_list = await FBDITemplate.find({"_id": {"$in": template_ids}}).to_list() if template_ids else []

    proj_map = {p.id: p for p in projects_list}
    ds_map = {d.id: d for d in datasets_list}
    tpl_map = {t.id: t for t in templates_list}

    results = []
    for c in convs:
        data = {**c.model_dump(), "id": str(c.id), "project_id": str(c.project_id)}
        if c.dataset_id:
            data["dataset_id"] = str(c.dataset_id)
            ds = ds_map.get(c.dataset_id)
            data["dataset_name"] = ds.name if ds else None
        if c.template_id:
            data["template_id"] = str(c.template_id)
            tmpl = tpl_map.get(c.template_id)
            data["template_name"] = tmpl.name if tmpl else None
        proj = proj_map.get(c.project_id)
        data["project_name"] = proj.name if proj else None
        results.append(ConversionOut(**data))
    return results


@router.get("", response_model=list[ConversionOut])
async def list_conversions(
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    _: User = Depends(get_current_user),
):
    query = {}
    if project_id:
        query["project_id"] = PydanticObjectId(project_id)
    if status:
        query["status"] = status
    convs = await Conversion.find(query).sort("planned_load_order").to_list()
    return await _hydrate_bulk(convs)


@router.get("/{conversion_id}", response_model=ConversionOut)
async def get_conversion(conversion_id: str, _: User = Depends(get_current_user)):
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    return await _hydrate(c)


@router.post("", response_model=ConversionOut)
async def create_conversion(
    payload: ConversionCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    proj = await Project.get(PydanticObjectId(payload.project_id))
    if not proj:
        raise HTTPException(400, f"Project {payload.project_id} does not exist")
    data = payload.model_dump(exclude_unset=True)
    data["project_id"] = PydanticObjectId(payload.project_id)
    if data.get("dataset_id"):
        data["dataset_id"] = PydanticObjectId(data["dataset_id"])
    if data.get("template_id"):
        data["template_id"] = PydanticObjectId(data["template_id"])
    data["created_by"] = user.email
    data["created_at"] = datetime.utcnow()
    data["updated_at"] = datetime.utcnow()
    if not data.get("status"):
        data["status"] = "draft" if data.get("dataset_id") and data.get("template_id") else "planning"
    c = Conversion(**data)
    await c.insert()
    if c.dataset_id and c.template_id:
        background_tasks.add_task(_auto_map, c.id)
    return await _hydrate(c)


@router.patch("/{conversion_id}", response_model=ConversionOut)
async def update_conversion(
    conversion_id: str,
    payload: ConversionUpdate,
    background_tasks: BackgroundTasks,
    _: User = Depends(get_current_user),
):
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    update_data = payload.model_dump(exclude_unset=True)
    if "dataset_id" in update_data and update_data["dataset_id"]:
        update_data["dataset_id"] = PydanticObjectId(update_data["dataset_id"])
    if "template_id" in update_data and update_data["template_id"]:
        update_data["template_id"] = PydanticObjectId(update_data["template_id"])
    update_data["updated_at"] = datetime.utcnow()
    await c.set(update_data)
    await c.sync()
    if c.dataset_id and c.template_id:
        background_tasks.add_task(_auto_map, c.id)
    return await _hydrate(c)


@router.delete("/{conversion_id}")
async def delete_conversion(conversion_id: str, _: User = Depends(get_current_user)):
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    await c.delete()
    return {"deleted": conversion_id}
