"""Dashboard aggregation service."""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.fbdi import FBDITemplate
from app.models.load import LoadRun
from app.models.project import Project
from app.models.workflow import Workflow


def get_kpis(db: Session) -> dict[str, Any]:
    total_datasets = db.query(Dataset).count()
    total_templates = db.query(FBDITemplate).count()
    total_projects = db.query(Project).count()
    total_conversions = db.query(Conversion).count()
    total_workflows = db.query(Workflow).count()
    total_load_runs = db.query(LoadRun).count()

    runs = db.query(LoadRun).all()
    total_records = sum(r.total_records for r in runs) or 0
    total_passed = sum(r.passed_count for r in runs) or 0
    total_failed = sum(r.failed_count for r in runs) or 0
    pass_rate = round((total_passed / total_records * 100), 1) if total_records else 0.0
    fail_rate = round((total_failed / total_records * 100), 1) if total_records else 0.0

    recent_projects = (
        db.query(Project).order_by(Project.updated_at.desc()).limit(5).all()
    )
    recent_conversions = (
        db.query(Conversion).order_by(Conversion.updated_at.desc()).limit(5).all()
    )
    recent_load_runs = (
        db.query(LoadRun).order_by(LoadRun.started_at.desc()).limit(5).all()
    )

    proj_status = Counter(p.status for p in db.query(Project).all())
    conv_status = Counter(c.status for c in db.query(Conversion).all())
    load_status = Counter(r.status for r in runs)

    return {
        "total_datasets": total_datasets,
        "total_templates": total_templates,
        "total_projects": total_projects,
        "total_conversions": total_conversions,
        "total_workflows": total_workflows,
        "total_load_runs": total_load_runs,
        "pass_rate": pass_rate,
        "fail_rate": fail_rate,
        "recent_projects": [
            {
                "id": p.id,
                "name": p.name,
                "client": p.client,
                "status": p.status,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in recent_projects
        ],
        "recent_conversions": [
            {
                "id": c.id,
                "name": c.name,
                "project_id": c.project_id,
                "status": c.status,
                "target_object": c.target_object,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                "dataset_id": c.dataset_id,
                "template_id": c.template_id,
            }
            for c in recent_conversions
        ],
        "recent_load_runs": [
            {
                "id": r.id,
                "conversion_id": r.conversion_id,
                "status": r.status,
                "total_records": r.total_records,
                "passed_count": r.passed_count,
                "failed_count": r.failed_count,
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
            for r in recent_load_runs
        ],
        "project_status_breakdown": [
            {"status": k, "count": v} for k, v in proj_status.items()
        ],
        "conversion_status_breakdown": [
            {"status": k, "count": v} for k, v in conv_status.items()
        ],
        "load_status_breakdown": [
            {"status": k, "count": v} for k, v in load_status.items()
        ],
    }
