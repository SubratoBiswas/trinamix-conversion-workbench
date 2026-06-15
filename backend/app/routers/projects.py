"""Projects (engagement) router."""
from datetime import datetime
from typing import Optional
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.models.conversion import Conversion
from app.models.project import Project
from app.models.user import User
from app.schemas.conversion import ConversionOut
from app.schemas.misc import AutoPopulateRequest
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _hydrate(p: Project) -> ProjectOut:
    from app.models.v10 import SourceConnection
    convs = await Conversion.find(Conversion.project_id == p.id).to_list()
    in_progress = sum(1 for c in convs if c.status in (
        "draft","mapping_suggested","awaiting_approval","validated","output_generated"))
    loaded = sum(1 for c in convs if c.status == "loaded")
    failed = sum(1 for c in convs if c.status == "failed")
    # Count source connections for this project
    sc_count = await SourceConnection.find(SourceConnection.project_id == p.id).count()
    data = p.model_dump()
    data["id"] = str(p.id)
    data["conversion_count"] = len(convs)
    data["in_progress_count"] = in_progress
    data["loaded_count"] = loaded
    data["failed_count"] = failed
    data["source_connection_count"] = sc_count
    data["has_active_source_connection"] = sc_count > 0
    return ProjectOut(**data)


@router.get("", response_model=list[ProjectOut])
async def list_projects(_: User = Depends(get_current_user)):
    projects = await Project.find_all().sort("-_id").to_list()
    return [await _hydrate(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, _: User = Depends(get_current_user)):
    p = await Project.get(PydanticObjectId(project_id))
    if not p:
        raise HTTPException(404, "Project not found")
    return await _hydrate(p)


@router.post("", response_model=ProjectOut)
async def create_project(payload: ProjectCreate, user: User = Depends(get_current_user)):
    data = payload.model_dump(exclude_unset=True)
    data.setdefault("owner", user.email)
    data.setdefault("created_at", datetime.utcnow())
    data.setdefault("updated_at", datetime.utcnow())
    p = Project(**data)
    await p.insert()
    return await _hydrate(p)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str, payload: ProjectUpdate, _: User = Depends(get_current_user)
):
    p = await Project.get(PydanticObjectId(project_id))
    if not p:
        raise HTTPException(404, "Project not found")
    update_data = payload.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.utcnow()
    await p.set(update_data)
    return await _hydrate(p)


@router.delete("/{project_id}")
async def delete_project(project_id: str, _: User = Depends(get_current_user)):
    p = await Project.get(PydanticObjectId(project_id))
    if not p:
        raise HTTPException(404, "Project not found")
    await p.delete()
    return {"deleted": project_id}


@router.get("/{project_id}/conversions", response_model=list[ConversionOut])
async def list_conversions_for_project(
    project_id: str, _: User = Depends(get_current_user)
):
    p = await Project.get(PydanticObjectId(project_id))
    if not p:
        raise HTTPException(404, "Project not found")
    convs = await Conversion.find(
        Conversion.project_id == p.id
    ).sort("planned_load_order").to_list()
    from app.models.dataset import Dataset
    from app.models.fbdi import FBDITemplate
    out = []
    for c in convs:
        co = ConversionOut(
            id=str(c.id),
            project_id=str(c.project_id),
            name=c.name,
            description=c.description,
            target_object=c.target_object,
            dataset_id=str(c.dataset_id) if c.dataset_id else None,
            template_id=str(c.template_id) if c.template_id else None,
            planned_load_order=c.planned_load_order,
            status=c.status,
            created_by=c.created_by,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        if c.dataset_id:
            ds = await Dataset.get(c.dataset_id)
            co.dataset_name = ds.name if ds else None
        if c.template_id:
            tmpl = await FBDITemplate.get(c.template_id)
            co.template_name = tmpl.name if tmpl else None
        co.project_name = p.name
        out.append(co)
    return out


@router.post("/{project_id}/auto-populate-conversions")
async def auto_populate_conversions(
    project_id: str,
    payload: AutoPopulateRequest,
    user: User = Depends(get_current_user),
):
    from app.services.project_service import auto_populate_conversions as _do
    p = await Project.get(PydanticObjectId(project_id))
    if not p:
        raise HTTPException(404, "Project not found")
    created = await _do(p, payload.modules, created_by=user.email)
    return {
        "project_id": project_id,
        "created_count": len(created),
        "conversions": [
            {"id": str(c.id), "name": c.name, "target_object": c.target_object,
             "planned_load_order": c.planned_load_order}
            for c in created
        ],
    }


@router.post("/{project_id}/derive-load-order")
async def derive_load_order(project_id: str, _: User = Depends(get_current_user)):
    from app.services.project_service import derive_load_order as _do
    p = await Project.get(PydanticObjectId(project_id))
    if not p:
        raise HTTPException(404, "Project not found")
    result = await _do(p)
    return {"project_id": project_id, "load_order": result}
