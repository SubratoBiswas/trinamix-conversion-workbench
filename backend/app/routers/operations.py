"""Output, load, workflow, dependency, and dashboard endpoints."""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
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


def _require_conversion(db: Session, conversion_id: int) -> Conversion:
    c = db.query(Conversion).filter(Conversion.id == conversion_id).first()
    if not c:
        raise HTTPException(404, "Conversion not found")
    return c


# ----- OUTPUT -----
output_router = APIRouter(prefix="/api/conversions", tags=["output"])


@output_router.post("/{conversion_id}/generate-output", response_model=ConvertedOutputOut)
def generate_output(
    conversion_id: int,
    fmt: str = Query("csv", pattern="^(csv|xlsx)$"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    c = _require_conversion(db, conversion_id)
    if not c.dataset_id or not c.template_id:
        raise HTTPException(400, "Conversion is not fully bound")
    return generate_output_artifact(db, c, fmt=fmt)


@output_router.get("/{conversion_id}/output-preview", response_model=OutputPreviewOut)
def output_preview(
    conversion_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    c = _require_conversion(db, conversion_id)
    if not c.dataset_id or not c.template_id:
        raise HTTPException(400, "Conversion is not fully bound")
    return get_output_preview(db, c, limit=limit)


@output_router.get("/{conversion_id}/download-output")
def download_output(
    conversion_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    out = (
        db.query(ConvertedOutput)
        .filter(ConvertedOutput.conversion_id == conversion_id)
        .order_by(ConvertedOutput.generated_at.desc())
        .first()
    )
    if not out or not Path(out.output_file_path).exists():
        raise HTTPException(404, "No output artifact found — generate output first")
    return FileResponse(out.output_file_path, filename=out.output_file_name)


# ----- LOAD -----
load_router = APIRouter(prefix="/api", tags=["load"])


@load_router.post("/conversions/{conversion_id}/simulate-load", response_model=LoadRunOut)
def simulate_load_endpoint(
    conversion_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    c = _require_conversion(db, conversion_id)
    if not c.dataset_id or not c.template_id:
        raise HTTPException(400, "Conversion is not fully bound")
    return simulate_conversion_load(db, c)


@load_router.get("/conversions/{conversion_id}/load-runs", response_model=list[LoadRunOut])
def list_load_runs(
    conversion_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return (
        db.query(LoadRun)
        .filter(LoadRun.conversion_id == conversion_id)
        .order_by(LoadRun.started_at.desc())
        .all()
    )


@load_router.get("/load-runs/{run_id}/errors", response_model=list[LoadErrorOut])
def list_load_errors(
    run_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return db.query(LoadError).filter(LoadError.load_run_id == run_id).all()


@load_router.get(
    "/conversions/{conversion_id}/load-errors", response_model=list[LoadErrorOut]
)
def list_latest_load_errors(
    conversion_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Errors from the most recent load run on this conversion. Convenience
    endpoint so the Error Traceback drawer can render reference-value chains
    without first round-tripping for the run id."""
    latest = (
        db.query(LoadRun)
        .filter(LoadRun.conversion_id == conversion_id)
        .order_by(LoadRun.started_at.desc())
        .first()
    )
    if not latest:
        return []
    return db.query(LoadError).filter(LoadError.load_run_id == latest.id).all()


@load_router.get("/conversions/{conversion_id}/load-summary", response_model=LoadSummaryOut)
def load_summary(
    conversion_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    c = _require_conversion(db, conversion_id)
    return build_load_summary(db, c)


# ----- WORKFLOW -----
workflow_router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@workflow_router.post("", response_model=WorkflowOut)
def create_workflow(
    payload: WorkflowCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    w = Workflow(**payload.model_dump(), status="saved")
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


@workflow_router.get("", response_model=list[WorkflowOut])
def list_workflows(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Workflow).order_by(Workflow.updated_at.desc()).all()


@workflow_router.get("/{workflow_id}", response_model=WorkflowOut)
def get_workflow(
    workflow_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(404, "Workflow not found")
    return w


@workflow_router.put("/{workflow_id}", response_model=WorkflowOut)
def update_workflow(
    workflow_id: int,
    payload: WorkflowUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(404, "Workflow not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(w, k, v)
    db.commit()
    db.refresh(w)
    return w


@workflow_router.post("/{workflow_id}/run", response_model=WorkflowOut)
def run_workflow(
    workflow_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    """Execute workflow nodes sequentially. Each runnable node maps to a real
    backend operation against the workflow's bound Conversion."""
    w = db.query(Workflow).filter(Workflow.id == workflow_id).first()
    if not w:
        raise HTTPException(404, "Workflow not found")
    conv = (
        db.query(Conversion).filter(Conversion.id == w.conversion_id).first()
        if w.conversion_id else None
    )
    summary: dict = {"steps": [], "started_at": datetime.utcnow().isoformat()}
    w.status = "running"
    db.commit()

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
                    if not conv.dataset_id or not conv.template_id:
                        raise RuntimeError("conversion not fully bound")
                    from app.services.mapping_service import run_mapping_suggestions
                    res = run_mapping_suggestions(db, conv)
                    step["detail"] = f"{len(res)} mapping suggestions"
                elif ntype == "validate":
                    from app.services.quality_service import run_validation
                    res = run_validation(db, conv)
                    step["detail"] = f"{len(res)} validation issues"
                elif ntype == "preview_output":
                    res = get_output_preview(db, conv, limit=10)
                    step["detail"] = f"{res['total_rows']} converted rows"
                elif ntype == "load_to_fusion":
                    res = simulate_conversion_load(db, conv)
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
        w.status = "success" if all(s["status"] != "error" for s in summary["steps"]) else "failed"
    except Exception as e:
        w.status = "failed"
        summary["error"] = str(e)
        summary["completed_at"] = datetime.utcnow().isoformat()
    w.last_run_at = datetime.utcnow()
    w.last_run_summary = summary
    db.commit()
    db.refresh(w)
    return w


# ----- DEPENDENCY -----
dep_router = APIRouter(prefix="/api/dependencies", tags=["dependencies"])


@dep_router.get("", response_model=list[DependencyOut])
def list_dependencies(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Dependency).all()


@dep_router.get("/impact/{conversion_id}")
def conversion_dependency_impact(
    conversion_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    """Return global object-type dependencies relevant to a conversion's target,
    plus its load-summary impact counts."""
    c = _require_conversion(db, conversion_id)
    target_obj = (
        c.template.business_object if c.template else (c.target_object or "")
    ).lower()
    deps = db.query(Dependency).all()
    relevant = [
        d for d in deps
        if target_obj in d.target_object.lower() or target_obj in d.source_object.lower()
    ]
    summary = build_load_summary(db, c)
    return {
        "object": (c.template.business_object if c.template else c.target_object),
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
def kpis(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return get_kpis(db)
