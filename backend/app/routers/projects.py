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


def _project_out(p: Project, convs: list, client_name: Optional[str] = None) -> ProjectOut:
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
    data["client_id"] = str(p.client_id) if p.client_id else None
    data["client_name"] = client_name
    data["conversion_count"] = len(convs)
    data["planning_count"] = planning
    data["in_progress_count"] = in_progress
    data["loaded_count"] = loaded
    data["failed_count"] = failed
    return ProjectOut(**data)


async def _client_name_for(p: Project) -> Optional[str]:
    """Resolve the display name of a project's tenant (best-effort)."""
    if not p.client_id:
        return None
    try:
        from app.models.client import Client
        c = await Client.get(p.client_id)
        return c.name if c else None
    except Exception:  # noqa: BLE001 — never break the response on a tenant lookup
        return None


async def _hydrate(p: Project) -> ProjectOut:
    convs = await Conversion.find(Conversion.project_id == p.id).to_list()
    return _project_out(p, convs, await _client_name_for(p))


@router.get("", response_model=list[ProjectOut])
async def list_projects(_: User = Depends(get_current_user)):
    # Fetch all conversions ONCE and group in memory, instead of one rollup
    # query per project (was 1 + N queries → slow with many engagements).
    projects = await Project.find_all().sort("-_id").to_list()
    all_convs = await Conversion.find_all().to_list()
    by_proj: dict = {}
    for c in all_convs:
        by_proj.setdefault(c.project_id, []).append(c)
    # Resolve tenant display names in one query rather than one per project.
    client_names: dict = {}
    try:
        from app.models.client import Client
        for c in await Client.find_all().to_list():
            client_names[c.id] = c.name
    except Exception:  # noqa: BLE001 — names are cosmetic; never fail the list
        pass
    return [_project_out(p, by_proj.get(p.id, []), client_names.get(p.client_id))
            for p in projects]


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
    # Tenant: use the picked client, else fall back to the bootstrap (default)
    # client so EVERY project is explicitly tenant-tagged. This is what lets each
    # project's captured learnings attach to the right client automatically — an
    # untagged project would have its captures resolve only for the default tenant.
    raw_cid = data.pop("client_id", None)
    resolved_cid = None
    if raw_cid:
        try:
            resolved_cid = PydanticObjectId(raw_cid)
        except Exception:  # noqa: BLE001 — bad id -> fall back to default below
            resolved_cid = None
    if resolved_cid is None:
        from app.services.client_service import default_client_id
        resolved_cid = await default_client_id()
    data["client_id"] = resolved_cid
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
    # Reassigning the tenant: accept a client id string and store it as ObjectId.
    if "client_id" in update_data:
        raw = update_data.pop("client_id")
        if raw:
            try:
                update_data["client_id"] = PydanticObjectId(raw)
            except Exception:  # noqa: BLE001
                raise HTTPException(400, "Invalid client_id")
        else:
            update_data["client_id"] = None
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
            source_type=c.source_type,
            ebs_table_hint=c.ebs_table_hint,
            output_mode=c.output_mode,
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


@router.post("/{project_id}/chain-load-order")
async def chain_load_order(project_id: str, _: User = Depends(get_current_user)):
    """Materialize prerequisite dependency edges from the project's current load
    sequence, so the dependency map shows what must run after what.

    Orders the project's conversions by ``planned_load_order`` and creates a
    prerequisite Dependency between each consecutive pair (earlier → later).
    This is what turns the supplier load sequence (1 Import, 2 Address, 3 Site,
    4 Site Assignment, 5 Contacts, 6 Contact Addresses, 7 Banks) into a visible
    chain on the Load Order graph. Idempotent — skips edges that already exist.
    """
    from app.models.dependency import Dependency

    p = await Project.get(PydanticObjectId(project_id))
    if not p:
        raise HTTPException(404, "Project not found")

    convs = await Conversion.find(
        Conversion.project_id == p.id
    ).sort(+Conversion.planned_load_order).to_list()
    # Keep only conversions that have a target object and dedupe by object,
    # preserving load-order sequence (first occurrence wins).
    seq: list[str] = []
    seen: set[str] = set()
    for c in convs:
        obj = (c.target_object or "").strip()
        if not obj or obj.lower() in seen:
            continue
        seen.add(obj.lower())
        seq.append(obj)

    if len(seq) < 2:
        return {"project_id": project_id, "created": [], "sequence": seq,
                "detail": "Need at least two conversions with distinct target objects."}

    existing = await Dependency.find(Dependency.relationship_type == "prerequisite").to_list()
    existing_pairs = {(d.source_object.lower(), d.target_object.lower()) for d in existing}

    created: list[dict] = []
    for src, tgt in zip(seq, seq[1:]):
        if (src.lower(), tgt.lower()) in existing_pairs:
            continue
        dep = Dependency(
            source_object=src,
            target_object=tgt,
            relationship_type="prerequisite",
            description=f"Load sequence: {src} → {tgt}",
        )
        await dep.insert()
        existing_pairs.add((src.lower(), tgt.lower()))
        created.append({"source_object": src, "target_object": tgt})

    return {"project_id": project_id, "created": created, "sequence": seq}
