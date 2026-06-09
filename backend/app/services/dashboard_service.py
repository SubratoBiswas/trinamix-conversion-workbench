"""Dashboard aggregation service."""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.fbdi import FBDITemplate
from app.models.load import LoadRun
from app.models.project import Project
from app.models.workflow import Workflow


async def get_kpis() -> dict[str, Any]:
    total_datasets = await Dataset.count()
    total_templates = await FBDITemplate.count()
    total_projects = await Project.count()
    total_conversions = await Conversion.count()
    total_workflows = await Workflow.count()
    total_load_runs = await LoadRun.count()

    runs = await LoadRun.find_all().to_list()
    total_records = sum(r.total_records for r in runs) or 0
    total_passed = sum(r.passed_count for r in runs) or 0
    total_failed = sum(r.failed_count for r in runs) or 0
    pass_rate = round((total_passed / total_records * 100), 1) if total_records else 0.0
    fail_rate = round((total_failed / total_records * 100), 1) if total_records else 0.0

    recent_projects = await Project.find_all().sort(-Project.updated_at).limit(5).to_list()
    recent_conversions = await Conversion.find_all().sort(-Conversion.updated_at).limit(5).to_list()
    recent_load_runs = await LoadRun.find_all().sort(-LoadRun.started_at).limit(5).to_list()

    all_projects = await Project.find_all().to_list()
    all_conversions = await Conversion.find_all().to_list()

    proj_status = Counter(p.status for p in all_projects)
    conv_status = Counter(c.status for c in all_conversions)
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
                "id": str(p.id),
                "name": p.name,
                "client": p.client,
                "status": p.status,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in recent_projects
        ],
        "recent_conversions": [
            {
                "id": str(c.id),
                "name": c.name,
                "project_id": str(c.project_id),
                "status": c.status,
                "target_object": c.target_object,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                "dataset_id": str(c.dataset_id) if c.dataset_id else None,
                "template_id": str(c.template_id) if c.template_id else None,
            }
            for c in recent_conversions
        ],
        "recent_load_runs": [
            {
                "id": str(r.id),
                "conversion_id": str(r.conversion_id),
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
