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
    # THE TENANT, AND IT IS REQUIRED. Either an existing client, or a name to create
    # one with right here — the analyst should not have to leave a half-built project
    # to go and add a client on another page.
    #
    # It used to fall back to the bootstrap "default" client whenever the field was
    # missing or the id was malformed. That looks harmless and is not: every decision
    # an analyst makes is stored as a CLIENT rule, so the client is the key the whole
    # library is filed and read under. A project that quietly became "default" filed
    # its rules there, and a correction made in a properly tagged project was then
    # skipped when it reached that project — as a cross-tenant leak. Silently
    # guessing the most important scope in the system is not a kindness.
    raw_cid = data.pop("client_id", None)
    new_name = data.pop("new_client_name", None)
    from app.services.client_service import resolve_client_for_project
    try:
        resolved_cid = await resolve_client_for_project(raw_cid, new_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
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

    # WHAT A DELETE TAKES, AND WHAT IT LEAVES.
    #
    # Three groups, and the split is deliberate rather than incidental:
    #
    #   PRESERVED — the LOGIC, which is not the rows. "Column A of a NetSuite
    #     extract for this module maps to column B of the FBDI" is a statement in
    #     the dated store, keyed (client, source system, target field). That is
    #     what is worth keeping and it is not stored on the conversion at all.
    #     The per-conversion mapping rows are a VIEW of it — deleting them loses
    #     nothing the store already holds, which is why they go with their
    #     conversions rather than accumulating as an archive nothing can render.
    #
    #     So the rows are CAPTURED FIRST and deleted second. Every deliberate
    #     edit already records a learning as it is made, and generation captures
    #     again — but "already" is an assumption, and this is the last moment the
    #     rows exist. A backstop pass runs before the delete and never blocks it.
    #
    #   DELETED — the rows and the artefacts. Mapping rows, transformation rules,
    #     crosswalks, generated output records, load runs and their errors.
    #
    #   DELETED, NEW — datasets, but ONLY those nothing else still uses. Uploads
    #     are deduped by content hash, so re-uploading the same file REUSES the
    #     Dataset row. A dataset can therefore be shared with another engagement,
    #     and deleting it blind pulls the source out from under conversions in a
    #     project nobody touched. Shared ones are skipped and NAMED in the
    #     response, because a deliverable that quietly got smaller is worse than
    #     one that says so.
    #
    # The learning library and the dated store are untouched, as they always
    # were: the decisions live there, so a rebuilt engagement picks them up.
    from app.models.mapping import MappingSuggestion
    from app.models.output import ConvertedOutput
    from app.models.load import LoadRun, LoadError
    from app.models.dataset import Dataset, DatasetColumnProfile

    convs = await Conversion.find(Conversion.project_id == p.id).to_list()
    conv_ids = [c.id for c in convs]
    datasets_deleted: list[str] = []
    datasets_kept: list[dict] = []
    logic_captured = 0
    capture_errors: list[str] = []
    # Anything that went wrong in the housekeeping. The ENGAGEMENT still goes —
    # leaving it undeletable because a side task failed is what turns a tidy-up
    # into a support call — but the failure is returned rather than swallowed.
    warnings: list[str] = []

    if conv_ids:
        # Which datasets did this project use? dataset_ids is the multi-source
        # list; dataset_id mirrors its first entry, and older rows only have that.
        ds_ids: set = set()
        for c in convs:
            for d in list(getattr(c, "dataset_ids", None) or []):
                ds_ids.add(d)
            if getattr(c, "dataset_id", None):
                ds_ids.add(c.dataset_id)

        # Who else is still using them, ignoring the conversions about to go.
        still_used: dict = {}
        if ds_ids:
            others = await Conversion.find(
                {"_id": {"$nin": conv_ids},
                 "$or": [{"dataset_id": {"$in": list(ds_ids)}},
                         {"dataset_ids": {"$in": list(ds_ids)}}]}
            ).to_list()
            _proj_names: dict = {}
            for o in others:
                used = set(list(getattr(o, "dataset_ids", None) or []))
                if getattr(o, "dataset_id", None):
                    used.add(o.dataset_id)
                for d in used & ds_ids:
                    if o.project_id not in _proj_names:
                        _op = await Project.get(o.project_id)
                        _proj_names[o.project_id] = _op.name if _op else str(o.project_id)
                    still_used.setdefault(d, set()).add(_proj_names[o.project_id])

        for d in ds_ids:
            # Per dataset, so one unreadable row cannot take the whole delete down
            # with it and leave the engagement undeletable.
            try:
                ds = await Dataset.get(d)
                if ds is None:
                    continue
                if d in still_used:
                    datasets_kept.append(
                        {"id": str(d), "name": ds.name,
                         "still_used_by": sorted(still_used[d])})
                    continue
                await DatasetColumnProfile.find(DatasetColumnProfile.dataset_id == d).delete()
                try:
                    import os
                    if ds.file_path and os.path.exists(ds.file_path):
                        os.remove(ds.file_path)
                except Exception:  # noqa: BLE001 — file cleanup is best-effort
                    pass
                await ds.delete()
                datasets_deleted.append(ds.name)
            except Exception as _ds_exc:  # noqa: BLE001
                warnings.append(f"dataset {d}: {type(_ds_exc).__name__}")

        # LAST CHANCE TO KEEP THE LOGIC. Capture each conversion's trustworthy
        # mappings into the dated store before the rows go, so "Column A of this
        # source maps to Column B of the FBDI" survives the engagement that
        # taught it. Deliberate edits already record a learning as they are made
        # and generation captures again, so this is usually a no-op — but usually
        # is not a guarantee, and after the next statement the rows are gone.
        #
        # BOUNDED. Capture across ~1200 fields is hundreds of Mongo upserts, which
        # is why generation itself skips it above 300 fields — output_service says
        # in as many words that it "is what made the request hang". An unbounded
        # loop of it over every conversion in an engagement did exactly that: the
        # small projects deleted, the 6- and 17-conversion ones timed out at the
        # gateway, and the browser reported "Failed to delete engagement" for what
        # was really a backstop nobody had asked to wait for.
        #
        # So it runs against a deadline. What does not fit is REPORTED, not
        # silently dropped — a delete that skipped the capture and said nothing
        # would be the screen looking right while logic was lost.
        import asyncio as _asyncio
        import time as _time
        _CAPTURE_BUDGET_S = 20.0     # total, across the whole engagement
        _PER_CONVERSION_S = 6.0
        _deadline = _time.monotonic() + _CAPTURE_BUDGET_S
        from app.services.learning_service import capture_learnings_from_conversion
        for _c in convs:
            _left = _deadline - _time.monotonic()
            if _left <= 0:
                capture_errors.append(f"{_c.name}: skipped, capture budget spent")
                continue
            try:
                _cap = await _asyncio.wait_for(
                    capture_learnings_from_conversion(_c),
                    timeout=min(_PER_CONVERSION_S, _left))
                logic_captured += int((_cap or {}).get("captured", 0) or 0)
            except _asyncio.TimeoutError:
                capture_errors.append(f"{_c.name}: timed out")
            except Exception as _cap_exc:  # noqa: BLE001
                capture_errors.append(f"{_c.name}: {type(_cap_exc).__name__}")

        # The rows themselves are a view of the store, so they go with their
        # conversions. Keeping them would leave hundreds of thousands of rows no
        # screen can render and no query is scoped to reach.
        from app.models.transformation import TransformationRule, Crosswalk
        for _label, _op in (
            ("mapping rows", MappingSuggestion.find({"conversion_id": {"$in": conv_ids}})),
            ("transformation rules", TransformationRule.find({"conversion_id": {"$in": conv_ids}})),
            ("crosswalks", Crosswalk.find({"conversion_id": {"$in": conv_ids}})),
            ("output records", ConvertedOutput.find({"conversion_id": {"$in": conv_ids}})),
        ):
            try:
                await _op.delete()
            except Exception as _del_exc:  # noqa: BLE001
                warnings.append(f"{_label}: {type(_del_exc).__name__}")
        try:
            runs = await LoadRun.find({"conversion_id": {"$in": conv_ids}}).to_list()
            run_ids = [r.id for r in runs]
            if run_ids:
                await LoadError.find({"load_run_id": {"$in": run_ids}}).delete()
            await LoadRun.find({"conversion_id": {"$in": conv_ids}}).delete()
        except Exception as _lr_exc:  # noqa: BLE001
            warnings.append(f"load history: {type(_lr_exc).__name__}")
        await Conversion.find(Conversion.project_id == p.id).delete()

    # NOTE: deliberately NOT deleting SourceConnection — the live Oracle EBS
    # connection is shared infrastructure and other engagements rely on it.
    await p.delete()
    return {
        "deleted": project_id,
        "conversions_deleted": len(conv_ids),
        "datasets_deleted": datasets_deleted,
        "datasets_kept": datasets_kept,
        # What survived as LOGIC. Reported rather than assumed: the rows are gone
        # and this number is the only evidence the store got what they held.
        "logic_captured": logic_captured,
        "capture_errors": capture_errors,
        "warnings": warnings,
    }


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
