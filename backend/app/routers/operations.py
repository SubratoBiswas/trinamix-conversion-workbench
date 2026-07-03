"""Output, load, workflow, dependency, and dashboard endpoints."""
# deploy-nudge: re-trigger backend build for wave-2 (commit 8782692)
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.models.conversion import Conversion
from app.models.dependency import Dependency
from app.models.load import LoadError, LoadRun
from app.models.output import ConvertedOutput
from app.models.user import User
from app.models.workflow import Workflow
from app.schemas.misc import (
    DashboardKpis, DependencyOut, WorkflowCreate, WorkflowOut, WorkflowUpdate,
)
from app.schemas.runtime import (
    ConvertedOutputOut, LoadErrorOut, LoadRunOut, LoadSummaryOut, OutputPreviewOut,
)
from app.services.auth_service import get_current_user
from app.services.dashboard_service import get_kpis
from app.services.output_service import generate_output_artifact, get_output_preview
from app.services.quality_service import build_load_summary, simulate_conversion_load


async def _require_conversion(conversion_id: str) -> Conversion:
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    return c


# ----- OUTPUT -----
output_router = APIRouter(prefix="/api/conversions", tags=["output"])


@output_router.post("/{conversion_id}/generate-output", response_model=ConvertedOutputOut)
async def generate_output(
    conversion_id: str,
    fmt: str = Query("csv", pattern="^(csv|xlsx)$"),
    _: User = Depends(get_current_user),
):
    c = await _require_conversion(conversion_id)
    is_ebs = getattr(c, "source_type", "dataset") == "ebs"
    if not c.template_id or (not is_ebs and not c.dataset_id):
        raise HTTPException(400, "Conversion is not fully bound")
    out = await generate_output_artifact(c, fmt=fmt)
    return {**out.model_dump(), "id": str(out.id), "conversion_id": str(out.conversion_id)}


@output_router.get("/{conversion_id}/output-preview", response_model=OutputPreviewOut)
async def output_preview(
    conversion_id: str,
    limit: int = 50,
    _: User = Depends(get_current_user),
):
    c = await _require_conversion(conversion_id)
    is_ebs = getattr(c, "source_type", "dataset") == "ebs"
    if not c.template_id or (not is_ebs and not c.dataset_id):
        raise HTTPException(400, "Conversion is not fully bound")
    return await get_output_preview(c, limit=limit)


@output_router.get("/{conversion_id}/outputs", response_model=list[ConvertedOutputOut])
async def list_outputs(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    outputs = await ConvertedOutput.find(
        ConvertedOutput.conversion_id == PydanticObjectId(conversion_id)
    ).sort("-generated_at").to_list()
    return [
        {**o.model_dump(), "id": str(o.id), "conversion_id": str(o.conversion_id)}
        for o in outputs
    ]


@output_router.get("/{conversion_id}/download-output")
async def download_output(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    out = await ConvertedOutput.find(
        ConvertedOutput.conversion_id == PydanticObjectId(conversion_id)
    ).sort("-generated_at").first_or_none()
    if not out or not Path(out.output_file_path).exists():
        raise HTTPException(404, "No output artifact found — generate output first")
    return FileResponse(out.output_file_path, filename=out.output_file_name)


def _safe_name(s: str) -> str:
    """Filesystem-safe token for zip entry names."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "conversion"


@output_router.get("/project/{project_id}/download-all")
async def download_all_outputs(
    project_id: str,
    fmt: str = Query("csv", pattern="^(csv|xlsx)$"),
    _: User = Depends(get_current_user),
):
    """Generate the FBDI output for every bound conversion in a project and
    return them together as a single zip, named/ordered by the supplier load
    sequence so the user gets all the filled-in files in one download."""
    from app.models.project import Project

    project = await Project.get(PydanticObjectId(project_id))
    if not project:
        raise HTTPException(404, "Project not found")

    conversions = await Conversion.find(
        Conversion.project_id == PydanticObjectId(project_id)
    ).sort(+Conversion.planned_load_order).to_list()
    if not conversions:
        raise HTTPException(404, "This engagement has no conversions yet")

    buf = io.BytesIO()
    used: dict[str, int] = {}
    added = 0
    skipped: list[str] = []
    ext = "xlsx" if fmt == "xlsx" else "csv"

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in conversions:
            is_ebs = getattr(c, "source_type", "dataset") == "ebs"
            if not c.template_id or (not is_ebs and not c.dataset_id):
                skipped.append(c.name)
                continue
            try:
                art = await generate_output_artifact(c, fmt=fmt)
            except Exception:
                skipped.append(c.name)
                continue
            if not art or not Path(art.output_file_path).exists():
                skipped.append(c.name)
                continue
            # Prefix with the load-order number so files sort in the sequence
            # the supplier load must run in (1 Import, 2 Address, …).
            order = c.planned_load_order if c.planned_load_order and c.planned_load_order < 100 else None
            prefix = f"{order:02d}_" if order is not None else ""
            arcname = f"{prefix}{_safe_name(c.name)}.{ext}"
            if arcname in used:
                used[arcname] += 1
                arcname = f"{prefix}{_safe_name(c.name)}_{used[arcname]}.{ext}"
            else:
                used[arcname] = 0
            zf.write(art.output_file_path, arcname=arcname)
            added += 1

    if added == 0:
        raise HTTPException(
            400,
            "No conversions in this engagement are ready to generate FBDI output "
            "(each needs a source dataset and a bound FBDI template).",
        )

    buf.seek(0)
    zip_name = f"{_safe_name(project.name)}_FBDI.zip"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_name}"',
            "X-Files-Added": str(added),
            "X-Files-Skipped": str(len(skipped)),
        },
    )


# ----- LOAD -----
load_router = APIRouter(prefix="/api", tags=["load"])


@load_router.post("/conversions/{conversion_id}/simulate-load", response_model=LoadRunOut)
async def simulate_load_endpoint(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    c = await _require_conversion(conversion_id)
    run = await simulate_conversion_load(c)
    return {**run.model_dump(), "id": str(run.id), "conversion_id": str(run.conversion_id)}


@load_router.get("/conversions/{conversion_id}/load-runs", response_model=list[LoadRunOut])
async def list_load_runs(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    runs = await LoadRun.find(
        LoadRun.conversion_id == PydanticObjectId(conversion_id)
    ).sort("-started_at").to_list()
    return [
        {**r.model_dump(), "id": str(r.id), "conversion_id": str(r.conversion_id)}
        for r in runs
    ]


@load_router.get("/load-runs/{run_id}/errors", response_model=list[LoadErrorOut])
async def list_load_errors(
    run_id: str,
    _: User = Depends(get_current_user),
):
    errors = await LoadError.find(
        LoadError.load_run_id == PydanticObjectId(run_id)
    ).to_list()
    return [
        {**e.model_dump(), "id": str(e.id), "load_run_id": str(e.load_run_id)}
        for e in errors
    ]


@load_router.get(
    "/conversions/{conversion_id}/load-errors", response_model=list[LoadErrorOut]
)
async def list_latest_load_errors(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    latest = await LoadRun.find(
        LoadRun.conversion_id == PydanticObjectId(conversion_id)
    ).sort("-started_at").first_or_none()
    if not latest:
        return []
    errors = await LoadError.find(
        LoadError.load_run_id == latest.id
    ).to_list()
    return [
        {**e.model_dump(), "id": str(e.id), "load_run_id": str(e.load_run_id)}
        for e in errors
    ]


@load_router.get("/conversions/{conversion_id}/load-summary", response_model=LoadSummaryOut)
async def load_summary(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    c = await _require_conversion(conversion_id)
    return await build_load_summary(c)


# ----- WORKFLOW -----
workflow_router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@workflow_router.post("", response_model=WorkflowOut)
async def create_workflow(
    payload: WorkflowCreate,
    _: User = Depends(get_current_user),
):
    w = Workflow(**payload.model_dump(), status="saved")
    await w.insert()
    return {
        **w.model_dump(), "id": str(w.id),
        "conversion_id": str(w.conversion_id) if w.conversion_id else None,
    }


@workflow_router.get("", response_model=list[WorkflowOut])
async def list_workflows(_: User = Depends(get_current_user)):
    workflows = await Workflow.find_all().sort("-updated_at").to_list()
    return [
        {**w.model_dump(), "id": str(w.id),
         "conversion_id": str(w.conversion_id) if w.conversion_id else None}
        for w in workflows
    ]


@workflow_router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: str,
    _: User = Depends(get_current_user),
):
    w = await Workflow.get(PydanticObjectId(workflow_id))
    if not w:
        raise HTTPException(404, "Workflow not found")
    return {
        **w.model_dump(), "id": str(w.id),
        "conversion_id": str(w.conversion_id) if w.conversion_id else None,
    }


@workflow_router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    _: User = Depends(get_current_user),
):
    w = await Workflow.get(PydanticObjectId(workflow_id))
    if not w:
        raise HTTPException(404, "Workflow not found")
    await w.set(payload.model_dump(exclude_unset=True))
    return {
        **w.model_dump(), "id": str(w.id),
        "conversion_id": str(w.conversion_id) if w.conversion_id else None,
    }


@workflow_router.post("/{workflow_id}/run", response_model=WorkflowOut)
async def run_workflow(
    workflow_id: str,
    _: User = Depends(get_current_user),
):
    w = await Workflow.get(PydanticObjectId(workflow_id))
    if not w:
        raise HTTPException(404, "Workflow not found")
    conv = await Conversion.get(w.conversion_id) if w.conversion_id else None
    summary: dict = {"steps": [], "started_at": datetime.utcnow().isoformat()}
    await w.set({"status": "running"})

    try:
        for node in (w.nodes or []):
            ntype = (node.get("data") or {}).get("nodeType") or node.get("type") or "unknown"
            step = {"node_id": node.get("id"), "type": ntype, "status": "ok", "detail": None}
            if not conv:
                step["status"] = "skipped"
                step["detail"] = "no conversion bound to dataflow"
                summary["steps"].append(step)
                continue
            try:
                if ntype == "ai_auto_map":
                    _ebs = getattr(conv, "source_type", "dataset") == "ebs"
                    if not conv.template_id or (not _ebs and not conv.dataset_id):
                        raise RuntimeError("conversion not fully bound")
                    from app.services.mapping_service import run_mapping_suggestions
                    res = await run_mapping_suggestions(conv)
                    step["detail"] = f"{len(res)} mapping suggestions"
                elif ntype == "validate":
                    from app.services.quality_service import run_validation
                    res = await run_validation(conv)
                    step["detail"] = f"{len(res)} validation issues"
                elif ntype == "preview_output":
                    res = await get_output_preview(conv, limit=10)
                    step["detail"] = f"{res['total_rows']} converted rows"
                elif ntype == "load_to_fusion":
                    res = await simulate_conversion_load(conv)
                    step["detail"] = (
                        f"passed={res.passed_count} failed={res.failed_count} "
                        f"warnings={res.warning_count}"
                    )
                else:
                    step["detail"] = "node executed"
            except Exception as e:
                step["status"] = "error"
                step["detail"] = str(e)
            summary["steps"].append(step)

        summary["completed_at"] = datetime.utcnow().isoformat()
        new_status = "success" if all(s["status"] != "error" for s in summary["steps"]) else "failed"
    except Exception as e:
        new_status = "failed"
        summary["error"] = str(e)
        summary["completed_at"] = datetime.utcnow().isoformat()

    await w.set({
        "status": new_status,
        "last_run_at": datetime.utcnow(),
        "last_run_summary": summary,
    })
    return {
        **w.model_dump(), "id": str(w.id),
        "conversion_id": str(w.conversion_id) if w.conversion_id else None,
    }


# ----- DEPENDENCY -----
dep_router = APIRouter(prefix="/api/dependencies", tags=["dependencies"])


@dep_router.get("", response_model=list[DependencyOut])
async def list_dependencies(_: User = Depends(get_current_user)):
    deps = await Dependency.find_all().to_list()
    return [{**d.model_dump(), "id": str(d.id)} for d in deps]


@dep_router.get("/impact/{conversion_id}")
async def conversion_dependency_impact(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    c = await _require_conversion(conversion_id)
    tpl = await (
        __import__("app.models.fbdi", fromlist=["FBDITemplate"]).FBDITemplate.get(c.template_id)
    ) if c.template_id else None
    target_obj = (tpl.business_object if tpl else (c.target_object or "")).lower()
    deps = await Dependency.find_all().to_list()
    relevant = [
        d for d in deps
        if target_obj in d.target_object.lower() or target_obj in d.source_object.lower()
    ]
    summary = await build_load_summary(c)
    return {
        "object": (tpl.business_object if tpl else c.target_object),
        "dependencies": [
            {
                "source_object": d.source_object,
                "target_object": d.target_object,
                "relationship_type": d.relationship_type,
                "description": d.description,
            }
            for d in relevant
        ],
        "impacts": summary["dependency_impacts"],
    }


# ----- DASHBOARD -----
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@dashboard_router.get("/kpis", response_model=DashboardKpis)
async def kpis(_: User = Depends(get_current_user)):
    return await get_kpis()
