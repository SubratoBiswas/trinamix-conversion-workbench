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


def _project_out(p: Project, convs: list) -> ProjectOut:
    """Build a ProjectOut with conversion roll-ups (no DB access).

    Status definitions are kept identical to ProjectOverviewPage so the project
    card and the detail page always show the same numbers.
    """
    planning = sum(1 for c in convs if c.status == "planning")
    in_progress = sum(1 for c in convs if c.status in (
        "draft","mapping_suggested","awaiting_approval","validated","output_generated"))
    loaded = sum(1 for c in convs if c.status == "loaded")
    failed = sum(1 for c in convs if c.status == "failed")
    data = p.model_dump()
    data["id"] = str(p.id)
    data["conversion_count"] = len(convs)
    data["planning_count"] = planning
    data["in_progress_count"] = in_progress
    data["loaded_count"] = loaded
    data["failed_count"] = failed
    return ProjectOut(**data)


async def _hydrate(p: Project) -> ProjectOut:
    convs = await Conversion.find(Conversion.project_id == p.id).to_list()
    return _project_out(p, convs)


@router.get("", response_model=list[ProjectOut])
async def list_projects(_: User = Depends(get_current_user)):
    # Fetch all conversions ONCE and group in memory, instead of one rollup
    # query per project (was 1 + N queries → slow with many engagements).
    projects = await Project.find_all().sort("-_id").to_list()
    all_convs = await Conversion.find_all().to_list()
    by_proj: dict = {}
    for c in all_convs:
        by_proj.setdefault(c.project_id, []).append(c)
    return [_project_out(p, by_proj.get(p.id, [])) for p in projects]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, _: User = Depends(get_current_user)):
    p = await Project.get(PydanticObjectId(project_id))
    if not p:
        raise HTTPException(404, "Project not found")
    return await _hydrate(p)


@router.post("", response_model=ProjectOut)
async def create_project(payload: ProjectCreate, user: User = Depends(get_current_user)):
    from app.models.v10 import SourceConnection
    data = payload.model_dump(exclude_unset=True)
    # initial_connection is handled separately — don't try to store it on Project
    ic = data.pop("initial_connection", None)
    data.setdefault("owner", user.email)
    data.setdefault("created_at", datetime.utcnow())
    data.setdefault("updated_at", datetime.utcnow())
    p = Project(**data)
    await p.insert()

    # Persist the initial SourceConnection if the wizard provided one
    if ic:
        meta = ic.get("connection_metadata") or {}
        creds = ic.get("credentials") or {}
        mock = ic.get("mock_mode", True)
        password = creds.get("password") or creds.get("token")
        if mock:
            password = "__mock__"

        # Extract host/port/service from connection_metadata or endpoint string
        host = meta.get("host")
        service_name = meta.get("service_name")
        port_raw = meta.get("port")
        port = int(port_raw) if port_raw else 1521

        endpoint = ic.get("endpoint") or ""
        if not host and endpoint:
            # Parse "host:port/service" format
            parts = endpoint.split(":")
            host = parts[0] if parts else None
            if len(parts) > 1:
                rest = parts[1]
                slash = rest.find("/")
                if slash != -1:
                    try:
                        port = int(rest[:slash])
                    except ValueError:
                        pass
                    if not service_name:
                        service_name = rest[slash + 1:]

        conn = SourceConnection(
            project_id=p.id,
            system_type=ic.get("source_system", "manual"),
            name=ic.get("display_name", "Connection"),
            host=host,
            port=port,
            service_name=service_name,
            username=creds.get("username"),
            encrypted_password=password,
            base_url=endpoint or None,
            auth_type=ic.get("auth_type"),
            connection_metadata=meta or None,
        )
        await conn.insert()

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

    # Cascade so nothing is orphaned (orphaned conversions would still show up
    # in the global conversion dropdowns and the 'N conversions' counter).
    convs = await Conversion.find(Conversion.project_id == p.id).to_list()
    conv_ids = [c.id for c in convs]
    if conv_ids:
        from app.models.mapping import MappingSuggestion
        from app.models.transformation import TransformationRule, Crosswalk
        from app.models.output import ConvertedOutput
        from app.models.load import LoadRun, LoadError
        await MappingSuggestion.find({"conversion_id": {"$in": conv_ids}}).delete()
        await TransformationRule.find({"conversion_id": {"$in": conv_ids}}).delete()
        await Crosswalk.find({"conversion_id": {"$in": conv_ids}}).delete()
        await ConvertedOutput.find({"conversion_id": {"$in": conv_ids}}).delete()
        runs = await LoadRun.find({"conversion_id": {"$in": conv_ids}}).to_list()
        run_ids = [r.id for r in runs]
        if run_ids:
            await LoadError.find({"load_run_id": {"$in": run_ids}}).delete()
        await LoadRun.find({"conversion_id": {"$in": conv_ids}}).delete()
        await Conversion.find(Conversion.project_id == p.id).delete()

    # NOTE: deliberately NOT deleting SourceConnection — the live Oracle EBS
    # connection is shared infrastructure and other engagements rely on it.
    await p.delete()
    return {"deleted": project_id, "conversions_deleted": len(conv_ids)}


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
