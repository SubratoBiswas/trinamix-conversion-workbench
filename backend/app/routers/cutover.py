"""Environments + cutover dashboard endpoints."""
from datetime import date, datetime

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.models.conversion import Conversion
from app.models.environment import DEFAULT_ENVIRONMENTS, Environment, EnvironmentRun
from app.models.project import Project
from app.models.user import User
from app.schemas.environment import (
    CutoverDashboard, EnvironmentOut, EnvironmentRunCreate, EnvironmentRunOut,
    EnvironmentRunUpdate,
)
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api", tags=["cutover"])


# ─── Environments ─────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/environments", response_model=list[EnvironmentOut])
async def list_environments(
    project_id: str,
    _: User = Depends(get_current_user),
):
    envs = await Environment.find(
        Environment.project_id == PydanticObjectId(project_id)
    ).sort("sort_order").to_list()
    return [{**e.model_dump(), "id": str(e.id), "project_id": str(e.project_id)} for e in envs]


@router.post("/projects/{project_id}/environments/seed", response_model=list[EnvironmentOut])
async def seed_default_environments(
    project_id: str,
    _: User = Depends(get_current_user),
):
    proj = await Project.get(PydanticObjectId(project_id))
    if not proj:
        raise HTTPException(404, "Project not found")
    pid = PydanticObjectId(project_id)
    existing = {
        e.name for e in await Environment.find(Environment.project_id == pid).to_list()
    }
    for env in DEFAULT_ENVIRONMENTS:
        if env["name"] in existing:
            continue
        await Environment(
            project_id=pid,
            name=env["name"],
            description=env["description"],
            sort_order=env["order"],
            color=env["color"],
            sox_controlled=1 if env["name"] == "PROD" else 0,
        ).insert()
    envs = await Environment.find(
        Environment.project_id == pid
    ).sort("sort_order").to_list()
    return [{**e.model_dump(), "id": str(e.id), "project_id": str(e.project_id)} for e in envs]


# ─── Environment runs ─────────────────────────────────────────────────────────

async def _hydrate_run(run: EnvironmentRun) -> dict:
    out = run.model_dump()
    out["id"] = str(run.id)
    out["environment_id"] = str(run.environment_id)
    out["conversion_id"] = str(run.conversion_id)
    out["dataset_id"] = str(run.dataset_id) if run.dataset_id else None
    env = await Environment.get(run.environment_id)
    conv = await Conversion.get(run.conversion_id)
    from app.models.dataset import Dataset
    ds = await Dataset.get(run.dataset_id) if run.dataset_id else None
    out["environment_name"] = env.name if env else None
    out["conversion_name"] = conv.name if conv else None
    out["dataset_name"] = ds.name if ds else None
    return out


@router.get(
    "/conversions/{conversion_id}/environment-runs",
    response_model=list[EnvironmentRunOut],
)
async def list_runs_for_conversion(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    runs = await EnvironmentRun.find(
        EnvironmentRun.conversion_id == PydanticObjectId(conversion_id)
    ).sort("_id").to_list()
    return [await _hydrate_run(r) for r in runs]


@router.post("/environment-runs", response_model=EnvironmentRunOut)
async def create_environment_run(
    payload: EnvironmentRunCreate,
    _: User = Depends(get_current_user),
):
    env = await Environment.get(PydanticObjectId(payload.environment_id))
    if not env:
        raise HTTPException(404, "Environment not found")
    conv = await Conversion.get(PydanticObjectId(payload.conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    if str(conv.project_id) != str(env.project_id):
        raise HTTPException(400, "Environment does not belong to the conversion's project")

    run = EnvironmentRun(
        environment_id=env.id,
        conversion_id=conv.id,
        dataset_id=PydanticObjectId(payload.dataset_id) if payload.dataset_id else None,
        status="pending",
        notes=payload.notes,
    )
    await run.insert()
    return await _hydrate_run(run)


@router.patch("/environment-runs/{run_id}", response_model=EnvironmentRunOut)
async def update_environment_run(
    run_id: str,
    payload: EnvironmentRunUpdate,
    _: User = Depends(get_current_user),
):
    run = await EnvironmentRun.get(PydanticObjectId(run_id))
    if not run:
        raise HTTPException(404, "Run not found")
    updates = payload.model_dump(exclude_unset=True)
    if payload.status == "running" and not run.started_at:
        updates["started_at"] = datetime.utcnow()
    if payload.status in ("complete", "failed") and not run.completed_at:
        updates["completed_at"] = datetime.utcnow()
    await run.set(updates)
    return await _hydrate_run(run)


# ─── Cutover dashboard ────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/cutover", response_model=CutoverDashboard)
async def cutover_dashboard(
    project_id: str,
    _: User = Depends(get_current_user),
):
    pid = PydanticObjectId(project_id)
    proj = await Project.get(pid)
    if not proj:
        raise HTTPException(404, "Project not found")

    days_to_go_live: int | None = None
    if proj.go_live_date:
        days_to_go_live = (proj.go_live_date - date.today()).days

    envs = await Environment.find(
        Environment.project_id == pid
    ).sort("sort_order").to_list()

    conversions = await Conversion.find(
        Conversion.project_id == pid
    ).sort("planned_load_order").to_list()

    conv_ids = [c.id for c in conversions]

    # Build per-environment stage list
    env_columns: list[dict] = []
    for env in envs:
        runs_list = await EnvironmentRun.find(
            EnvironmentRun.environment_id == env.id
        ).to_list()
        runs = {r.conversion_id: r for r in runs_list}
        stages = []
        for c in conversions:
            run = runs.get(c.id)
            if env.name == "DEV":
                stage_status = (
                    "complete" if c.status in ("loaded", "validated", "output_generated")
                    else "running" if c.status in ("draft", "mapping_suggested", "awaiting_approval")
                    else "pending"
                )
            else:
                stage_status = run.status if run else "pending"
            stages.append({
                "conversion_id": str(c.id),
                "conversion_name": c.name,
                "target_object": c.target_object,
                "status": stage_status,
                "run_id": str(run.id) if run else None,
                "dataset_id": str(run.dataset_id) if run and run.dataset_id else (str(c.dataset_id) if c.dataset_id else None),
                "started_at": run.started_at.isoformat() if run and run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run and run.completed_at else None,
            })
        env_columns.append({
            "id": str(env.id),
            "name": env.name,
            "color": env.color,
            "sox_controlled": bool(env.sox_controlled),
            "stages": stages,
            "complete_count": sum(1 for s in stages if s["status"] == "complete"),
            "running_count": sum(1 for s in stages if s["status"] == "running"),
            "failed_count": sum(1 for s in stages if s["status"] == "failed"),
            "pending_count": sum(1 for s in stages if s["status"] == "pending"),
        })

    # Recent pipeline runs
    recent_runs = await EnvironmentRun.find(
        {"conversion_id": {"$in": conv_ids}}
    ).sort("-id").limit(20).to_list()

    pipeline_runs = []
    for r in recent_runs:
        conv = await Conversion.get(r.conversion_id)
        env = await Environment.get(r.environment_id)
        pipeline_runs.append({
            "run_id": str(r.id),
            "entity": conv.name if conv else "—",
            "stage": r.stage or r.status,
            "status": r.status,
            "records": r.record_count,
            "started": r.started_at.isoformat() if r.started_at else None,
            "environment": env.name if env else None,
        })

    return CutoverDashboard(
        project_id=str(proj.id),
        project_name=proj.name,
        days_to_go_live=days_to_go_live,
        cutover_window_start=proj.production_cutover_start,
        cutover_window_end=proj.production_cutover_end,
        sox_controlled=bool(proj.sox_controlled),
        environments=env_columns,
        pipeline_runs=pipeline_runs,
    )
