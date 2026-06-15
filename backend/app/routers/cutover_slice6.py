"""Cutover & Exec layer endpoints — MongoDB/Beanie version (Slice 6)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.user import User
from app.models.v10 import (
    CutoverTask, DressRehearsal, Issue, ReconciliationCheck,
    Risk, SignOff,
)
from app.models.project import Project
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api", tags=["cutover-slice6"])


async def _get_project(project_id: str) -> Project:
    try:
        p = await Project.get(project_id)
    except Exception:
        p = None
    if not p:
        raise HTTPException(404, "Project not found")
    return p


# ─── Schemas ────────────────────────────────────────────────────────

class SafeguardOut(BaseModel):
    code: str
    name: str
    status: str   # pass | warning | fail
    message: str
    details: Dict[str, Any] = {}


class SafeguardsResponse(BaseModel):
    pass_rate: float
    safeguards: List[SafeguardOut]


class ReadinessLensOut(BaseModel):
    label: str
    value: float
    value_pct: int
    weight: float
    details: Dict[str, Any] = {}


class ReadinessResponse(BaseModel):
    total: float
    total_pct: int
    delta_2w: float
    lenses: Dict[str, ReadinessLensOut]


class IssueOut(BaseModel):
    id: str
    project_id: str
    title: str
    description: Optional[str] = None
    severity: str
    status: str
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IssueCreate(BaseModel):
    title: str
    description: Optional[str] = None
    severity: str = "medium"
    status: str = "open"
    owner: Optional[str] = None
    due_date: Optional[datetime] = None


class IssueUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    resolution_notes: Optional[str] = None


class RiskOut(BaseModel):
    id: str
    project_id: str
    title: str
    description: Optional[str] = None
    likelihood: str
    impact: str
    status: str
    mitigation_plan: Optional[str] = None
    owner: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RiskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    likelihood: str = "medium"
    impact: str = "medium"
    status: str = "identified"
    mitigation_plan: Optional[str] = None
    owner: Optional[str] = None


class RiskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    likelihood: Optional[str] = None
    impact: Optional[str] = None
    status: Optional[str] = None
    mitigation_plan: Optional[str] = None
    owner: Optional[str] = None


class RunbookTaskOut(BaseModel):
    id: str
    project_id: str
    title: str
    description: Optional[str] = None
    sequence: int
    owner: Optional[str] = None
    estimated_minutes: Optional[int] = None
    actual_minutes: Optional[int] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime


class RunbookTaskUpdate(BaseModel):
    status: Optional[str] = None
    actual_minutes: Optional[int] = None
    notes: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ReconCheckOut(BaseModel):
    id: str
    project_id: str
    conversion_id: Optional[str] = None
    check_name: str
    check_type: str
    source_value: Optional[str] = None
    fusion_value: Optional[str] = None
    tolerance: float
    passed: Optional[bool] = None
    variance: Optional[float] = None
    notes: Optional[str] = None
    checked_at: datetime


class DressRehearsalOut(BaseModel):
    id: str
    project_id: str
    name: str
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str
    outcome: Optional[str] = None
    records_processed: int = 0
    records_failed: int = 0
    issues_found: int = 0
    notes: Optional[str] = None
    conducted_by: Optional[str] = None
    created_at: datetime


class DressRehearsalCreate(BaseModel):
    name: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    notes: Optional[str] = None
    conducted_by: Optional[str] = None


class SignOffOut(BaseModel):
    id: str
    project_id: str
    conversion_id: Optional[str] = None
    checkpoint: str
    signed_off_by: Optional[str] = None
    signed_off_at: Optional[datetime] = None
    status: str
    notes: Optional[str] = None
    created_at: datetime


class SignOffCreate(BaseModel):
    kind: str
    subject: Optional[str] = None
    signer_email: Optional[str] = None
    signer_role: Optional[str] = None
    decision: str = "approved"
    comment: Optional[str] = None
    conversion_id: Optional[str] = None
    evidence_url: Optional[str] = None
    references_signoff_id: Optional[str] = None


class ExecSummaryOut(BaseModel):
    score_pct: int
    score_5: float
    safeguard_pass_rate: float
    days_to_cutover: Optional[int] = None
    open_critical_issues: int
    top_risks: List[Dict[str, Any]] = []
    top_blockers: List[Dict[str, Any]] = []
    total_recon_variance_usd: float = 0.0
    pillar_complexity: Optional[float] = None
    integrations_degraded: int = 0


# ─── Helpers ────────────────────────────────────────────────────────

def _score_for_project(p: Project) -> tuple[float, int]:
    """Simple readiness score based on project fields."""
    score = 3.0
    if p.go_live_date:
        score += 0.5
    if getattr(p, "phase", None) in ("execution", "cutover"):
        score += 0.5
    if getattr(p, "dress_rehearsal_count", 0) > 0:
        score += 0.5
    score = min(score, 5.0)
    return round(score, 1), int(score / 5.0 * 100)


def _days_until(p: Project) -> Optional[int]:
    if not p.go_live_date:
        return None
    d = p.go_live_date
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d).date()
        except Exception:
            return None
    if hasattr(d, "date"):
        d = d.date()
    delta = (d - datetime.utcnow().date()).days
    return delta


async def _compute_safeguards(project_id: str, p: Project) -> SafeguardsResponse:
    oid = PydanticObjectId(project_id)
    open_crit = await Issue.find(
        Issue.project_id == oid,
        Issue.severity == "critical",
        {"status": {"$in": ["open", "in_progress", "blocked"]}}
    ).count()
    recon_count = await ReconciliationCheck.find(ReconciliationCheck.project_id == oid).count()
    rehearsals = getattr(p, "dress_rehearsal_count", 0) or 0
    days = _days_until(p)

    gates = [
        SafeguardOut(
            code="no_critical_issues",
            name="No Critical Issues",
            status="pass" if open_crit == 0 else "fail",
            message=f"{open_crit} critical issue(s) open" if open_crit else "No critical issues",
        ),
        SafeguardOut(
            code="reconciliation",
            name="Reconciliation Checks",
            status="pass" if recon_count > 0 else "warning",
            message=f"{recon_count} check(s) run" if recon_count else "No reconciliation checks run yet",
        ),
        SafeguardOut(
            code="dress_rehearsal",
            name="Dress Rehearsal",
            status="pass" if rehearsals > 0 else "warning",
            message=f"{rehearsals} rehearsal(s) completed" if rehearsals else "No dress rehearsal completed",
        ),
        SafeguardOut(
            code="go_live_date",
            name="Go-Live Date Set",
            status="pass" if p.go_live_date else "warning",
            message=f"Go-live: {p.go_live_date}" if p.go_live_date else "Go-live date not set",
        ),
        SafeguardOut(
            code="cutover_window",
            name="Cutover Window",
            status="pass" if days is not None and days >= 7 else ("warning" if days is None else "fail"),
            message=f"{days} days to cutover" if days is not None else "No cutover date",
        ),
    ]
    passing = sum(1 for g in gates if g.status == "pass")
    return SafeguardsResponse(
        pass_rate=round(passing / len(gates), 2),
        safeguards=gates,
    )


# ─── Safeguards ─────────────────────────────────────────────────────

@router.get("/projects/{project_id}/safeguards", response_model=SafeguardsResponse)
async def get_safeguards(project_id: str, _: User = Depends(get_current_user)):
    p = await _get_project(project_id)
    return await _compute_safeguards(project_id, p)


@router.post("/projects/{project_id}/safeguards/refresh", response_model=SafeguardsResponse)
async def refresh_safeguards(project_id: str, _: User = Depends(get_current_user)):
    p = await _get_project(project_id)
    return await _compute_safeguards(project_id, p)


# ─── Readiness ──────────────────────────────────────────────────────

@router.get("/projects/{project_id}/readiness", response_model=ReadinessResponse)
async def get_readiness(project_id: str, _: User = Depends(get_current_user)):
    p = await _get_project(project_id)
    score, pct = _score_for_project(p)
    oid = PydanticObjectId(project_id)
    open_crit = await Issue.find(Issue.project_id == oid).count()
    recon = await ReconciliationCheck.find(ReconciliationCheck.project_id == oid).count()
    days = _days_until(p)

    data_pct = min(100, recon * 20)
    issue_pct = max(0, 100 - open_crit * 25)
    cutover_pct = 80 if days is not None and days > 0 else 40
    go_live_pct = 90 if p.go_live_date else 30

    return ReadinessResponse(
        total=score,
        total_pct=pct,
        delta_2w=0.2,
        lenses={
            "data_quality": ReadinessLensOut(label="Data Quality", value=data_pct / 100 * 5, value_pct=data_pct, weight=0.3),
            "issue_resolution": ReadinessLensOut(label="Issue Resolution", value=issue_pct / 100 * 5, value_pct=issue_pct, weight=0.25),
            "cutover_readiness": ReadinessLensOut(label="Cutover Readiness", value=cutover_pct / 100 * 5, value_pct=cutover_pct, weight=0.25),
            "go_live_planning": ReadinessLensOut(label="Go-Live Planning", value=go_live_pct / 100 * 5, value_pct=go_live_pct, weight=0.2),
        },
    )


# ─── Reconciliation ─────────────────────────────────────────────────

def _recon_out(r: ReconciliationCheck) -> ReconCheckOut:
    return ReconCheckOut(
        id=str(r.id),
        project_id=str(r.project_id),
        conversion_id=str(r.conversion_id) if r.conversion_id else None,
        check_name=r.check_name,
        check_type=r.check_type,
        source_value=r.source_value,
        fusion_value=r.fusion_value,
        tolerance=r.tolerance,
        passed=r.passed,
        variance=r.variance,
        notes=r.notes,
        checked_at=r.checked_at,
    )


@router.get("/projects/{project_id}/reconciliation", response_model=List[ReconCheckOut])
async def list_reconciliation(project_id: str, _: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    rows = await ReconciliationCheck.find(ReconciliationCheck.project_id == oid).sort("-checked_at").to_list()
    return [_recon_out(r) for r in rows]


@router.post("/projects/{project_id}/reconciliation/seed", response_model=List[ReconCheckOut])
async def seed_reconciliation(project_id: str, _: User = Depends(get_current_user)):
    p = await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    # Seed mock reconciliation checks
    checks = [
        ("GL Account Balances", "sum", "1,234,567.89", "1,234,567.89", True, 0.0),
        ("AP Invoice Count", "count", "4,521", "4,521", True, 0.0),
        ("AR Open Items", "count", "892", "890", False, 2.0),
        ("Fixed Assets", "count", "1,203", "1,203", True, 0.0),
        ("Employee Records", "count", "1,847", "1,847", True, 0.0),
    ]
    results = []
    for name, ctype, sv, fv, passed, variance in checks:
        row = ReconciliationCheck(
            project_id=oid,
            check_name=name,
            check_type=ctype,
            source_value=sv,
            fusion_value=fv,
            tolerance=0.01,
            passed=passed,
            variance=variance,
        )
        await row.insert()
        results.append(_recon_out(row))
    return results


# ─── Runbook ────────────────────────────────────────────────────────

def _task_out(t: CutoverTask) -> RunbookTaskOut:
    return RunbookTaskOut(
        id=str(t.id),
        project_id=str(t.project_id),
        title=t.title,
        description=t.description,
        sequence=t.sequence,
        owner=t.owner,
        estimated_minutes=t.estimated_minutes,
        actual_minutes=t.actual_minutes,
        status=t.status,
        started_at=t.started_at,
        completed_at=t.completed_at,
        notes=t.notes,
        created_at=t.created_at,
    )


_RUNBOOK_TEMPLATE = [
    ("Freeze legacy system — block new transactions", 15, "DBA"),
    ("Final data extract from legacy", 30, "Data Lead"),
    ("Transfer extract files to staging", 10, "DBA"),
    ("Run pre-load validation scripts", 20, "Data Lead"),
    ("Load GL Chart of Accounts", 45, "Fusion Admin"),
    ("Load AP Suppliers", 30, "Fusion Admin"),
    ("Load GL Opening Balances", 60, "Fusion Admin"),
    ("Load AR Customers", 30, "Fusion Admin"),
    ("Load Fixed Assets", 45, "Fusion Admin"),
    ("Run post-load reconciliation", 30, "Data Lead"),
    ("Execute UAT smoke tests", 45, "QA Lead"),
    ("Obtain business sign-off", 20, "Project Manager"),
    ("Enable Fusion for end users", 10, "Fusion Admin"),
    ("Monitor system for 2 hours", 120, "Support Lead"),
    ("Close out cutover — document lessons learned", 30, "Project Manager"),
]


@router.get("/projects/{project_id}/runbook", response_model=List[RunbookTaskOut])
async def get_runbook(project_id: str, _: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    tasks = await CutoverTask.find(CutoverTask.project_id == oid).sort("sequence").to_list()
    return [_task_out(t) for t in tasks]


@router.post("/projects/{project_id}/runbook/seed", response_model=List[RunbookTaskOut])
async def seed_runbook(project_id: str, force: bool = False, _: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    existing = await CutoverTask.find(CutoverTask.project_id == oid).count()
    if existing and not force:
        tasks = await CutoverTask.find(CutoverTask.project_id == oid).sort("sequence").to_list()
        return [_task_out(t) for t in tasks]
    results = []
    for i, (title, mins, owner) in enumerate(_RUNBOOK_TEMPLATE, 1):
        t = CutoverTask(
            project_id=oid,
            title=title,
            sequence=i,
            owner=owner,
            estimated_minutes=mins,
            status="pending",
        )
        await t.insert()
        results.append(_task_out(t))
    return results


@router.patch("/runbook-tasks/{task_id}", response_model=RunbookTaskOut)
async def update_runbook_task(
    task_id: str,
    payload: RunbookTaskUpdate,
    _: User = Depends(get_current_user),
):
    t = await CutoverTask.get(task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(t, k, v)
    await t.save()
    return _task_out(t)


# ─── Issues ─────────────────────────────────────────────────────────

def _issue_out(i: Issue) -> IssueOut:
    return IssueOut(
        id=str(i.id),
        project_id=str(i.project_id),
        title=i.title,
        description=i.description,
        severity=i.severity,
        status=i.status,
        owner=i.owner,
        due_date=i.due_date,
        created_at=i.created_at,
        updated_at=i.updated_at,
    )


@router.get("/projects/{project_id}/issues", response_model=List[IssueOut])
async def list_issues(project_id: str, status: Optional[str] = None, _: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    q = Issue.find(Issue.project_id == oid)
    if status:
        q = q.find(Issue.status == status)
    issues = await q.sort("-created_at").to_list()
    return [_issue_out(i) for i in issues]


@router.post("/projects/{project_id}/issues", response_model=IssueOut, status_code=201)
async def create_issue(project_id: str, payload: IssueCreate, user: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    issue = Issue(
        project_id=oid,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        status=payload.status,
        owner=payload.owner,
        due_date=payload.due_date,
        created_by=user.email,
    )
    await issue.insert()
    return _issue_out(issue)


@router.patch("/issues/{issue_id}", response_model=IssueOut)
async def update_issue(issue_id: str, payload: IssueUpdate, _: User = Depends(get_current_user)):
    issue = await Issue.get(issue_id)
    if not issue:
        raise HTTPException(404, "Issue not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(issue, k, v)
    issue.updated_at = datetime.utcnow()
    await issue.save()
    return _issue_out(issue)


# ─── Risks ──────────────────────────────────────────────────────────

def _risk_out(r: Risk) -> RiskOut:
    return RiskOut(
        id=str(r.id),
        project_id=str(r.project_id),
        title=r.title,
        description=r.description,
        likelihood=r.likelihood,
        impact=r.impact,
        status=r.status,
        mitigation_plan=r.mitigation_plan,
        owner=r.owner,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.get("/projects/{project_id}/risks", response_model=List[RiskOut])
async def list_risks(project_id: str, _: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    risks = await Risk.find(Risk.project_id == oid).sort("-created_at").to_list()
    return [_risk_out(r) for r in risks]


@router.post("/projects/{project_id}/risks", response_model=RiskOut, status_code=201)
async def create_risk(project_id: str, payload: RiskCreate, user: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    risk = Risk(
        project_id=oid,
        title=payload.title,
        description=payload.description,
        likelihood=payload.likelihood,
        impact=payload.impact,
        status=payload.status,
        mitigation_plan=payload.mitigation_plan,
        owner=payload.owner,
        created_by=user.email,
    )
    await risk.insert()
    return _risk_out(risk)


@router.patch("/risks/{risk_id}", response_model=RiskOut)
async def update_risk(risk_id: str, payload: RiskUpdate, _: User = Depends(get_current_user)):
    risk = await Risk.get(risk_id)
    if not risk:
        raise HTTPException(404, "Risk not found")
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(risk, k, v)
    risk.updated_at = datetime.utcnow()
    await risk.save()
    return _risk_out(risk)


# ─── Dress Rehearsals ───────────────────────────────────────────────

def _rehearsal_out(r: DressRehearsal) -> DressRehearsalOut:
    return DressRehearsalOut(
        id=str(r.id),
        project_id=str(r.project_id),
        name=r.name,
        scheduled_at=r.scheduled_at,
        started_at=r.started_at,
        completed_at=r.completed_at,
        status=r.status,
        outcome=r.outcome,
        records_processed=r.records_processed,
        records_failed=r.records_failed,
        issues_found=r.issues_found,
        notes=r.notes,
        conducted_by=r.conducted_by,
        created_at=r.created_at,
    )


@router.get("/projects/{project_id}/dress-rehearsals", response_model=List[DressRehearsalOut])
async def list_dress_rehearsals(project_id: str, _: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    rows = await DressRehearsal.find(DressRehearsal.project_id == oid).sort("-created_at").to_list()
    return [_rehearsal_out(r) for r in rows]


@router.post("/projects/{project_id}/dress-rehearsals", response_model=DressRehearsalOut, status_code=201)
async def create_dress_rehearsal(project_id: str, payload: DressRehearsalCreate, user: User = Depends(get_current_user)):
    p = await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    count = await DressRehearsal.find(DressRehearsal.project_id == oid).count()
    dr = DressRehearsal(
        project_id=oid,
        name=payload.name or f"Dress Rehearsal #{count + 1}",
        scheduled_at=payload.scheduled_at,
        notes=payload.notes,
        conducted_by=payload.conducted_by or user.email,
        status="planned",
    )
    await dr.insert()
    # Increment dress_rehearsal_count on project
    p.dress_rehearsal_count = getattr(p, "dress_rehearsal_count", 0) + 1
    await p.save()
    return _rehearsal_out(dr)


# ─── Sign-offs ──────────────────────────────────────────────────────

def _signoff_out(s: SignOff) -> SignOffOut:
    return SignOffOut(
        id=str(s.id),
        project_id=str(s.project_id),
        conversion_id=str(s.conversion_id) if s.conversion_id else None,
        checkpoint=s.checkpoint,
        signed_off_by=s.signed_off_by,
        signed_off_at=s.signed_off_at,
        status=s.status,
        notes=s.notes,
        created_at=s.created_at,
    )


@router.get("/projects/{project_id}/sign-offs", response_model=List[SignOffOut])
async def list_sign_offs(project_id: str, _: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    rows = await SignOff.find(SignOff.project_id == oid).sort("-created_at").to_list()
    return [_signoff_out(r) for r in rows]


@router.post("/projects/{project_id}/sign-offs", response_model=SignOffOut, status_code=201)
async def create_sign_off(project_id: str, payload: SignOffCreate, user: User = Depends(get_current_user)):
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    conv_oid = PydanticObjectId(payload.conversion_id) if payload.conversion_id else None
    so = SignOff(
        project_id=oid,
        conversion_id=conv_oid,
        checkpoint=payload.kind,
        signed_off_by=payload.signer_email or user.email,
        signed_off_at=datetime.utcnow() if payload.decision == "approved" else None,
        status="signed" if payload.decision == "approved" else "rejected",
        notes=payload.comment,
    )
    await so.insert()
    return _signoff_out(so)


# ─── COA readiness stub ─────────────────────────────────────────────

@router.get("/projects/{project_id}/coa-readiness")
async def get_coa_readiness(project_id: str, _: User = Depends(get_current_user)):
    await _get_project(project_id)
    return {
        "threshold_pct": 99,
        "is_ready": True,
        "worst_coverage_pct": None,
        "blocker_reason": None,
        "conversions": [],
    }


# ─── Environment promotion ──────────────────────────────────────────

class PromotePayload(BaseModel):
    target_environment: str


_ENV_ORDER = ["DEV", "QA", "UAT", "PROD"]


@router.post("/projects/{project_id}/promote-environment")
async def promote_environment(project_id: str, payload: PromotePayload, _: User = Depends(get_current_user)):
    p = await _get_project(project_id)
    target = payload.target_environment.upper()
    if target not in _ENV_ORDER or target == "DEV":
        raise HTTPException(400, f"Cannot promote to {target}")
    current = (getattr(p, "current_environment", None) or "DEV").upper()
    if _ENV_ORDER.index(target) != _ENV_ORDER.index(current) + 1:
        raise HTTPException(409, f"Out-of-order promotion: current is {current}")
    p.current_environment = target
    await p.save()
    return {"current_environment": target, "promoted_from": current}


# ─── Quality score stubs ────────────────────────────────────────────

@router.get("/conversions/{conversion_id}/quality-score")
async def conversion_quality_score(conversion_id: str, _: User = Depends(get_current_user)):
    return {
        "conversion_id": conversion_id,
        "total": 3.8,
        "lenses": [
            {"code": "mapping_coverage", "value_pct": 85, "weight": 0.4, "details": {}},
            {"code": "validation_cleanliness", "value_pct": 72, "weight": 0.35, "details": {}},
            {"code": "reconciliation", "value_pct": 60, "weight": 0.25, "details": {}},
        ],
    }


@router.post("/projects/{project_id}/quality-score/recompute")
async def recompute_quality_scores(project_id: str, _: User = Depends(get_current_user)):
    return {"project_id": project_id, "scores": {}, "average": 3.8}


# ─── Exec summary ───────────────────────────────────────────────────

@router.get("/projects/{project_id}/exec-summary", response_model=ExecSummaryOut)
async def exec_summary(project_id: str, _: User = Depends(get_current_user)):
    p = await _get_project(project_id)
    score, pct = _score_for_project(p)
    safeguards_resp = await _compute_safeguards(project_id, p)
    oid = PydanticObjectId(project_id)

    open_crit = await Issue.find(
        Issue.project_id == oid,
        Issue.severity == "critical",
        {"status": {"$in": ["open", "in_progress", "blocked"]}}
    ).count()

    top_risks = await Risk.find(
        Risk.project_id == oid,
        {"status": {"$ne": "closed"}},
    ).sort("-updated_at").limit(5).to_list()

    top_blockers = await Issue.find(
        Issue.project_id == oid,
        {"status": {"$in": ["open", "in_progress", "blocked"]}},
    ).sort("-created_at").limit(5).to_list()

    return ExecSummaryOut(
        score_pct=pct,
        score_5=score,
        safeguard_pass_rate=safeguards_resp.pass_rate,
        days_to_cutover=_days_until(p),
        open_critical_issues=open_crit,
        top_risks=[{"id": str(r.id), "title": r.title, "likelihood": r.likelihood, "impact": r.impact} for r in top_risks],
        top_blockers=[{"id": str(i.id), "title": i.title, "severity": i.severity, "status": i.status} for i in top_blockers],
        total_recon_variance_usd=0.0,
        pillar_complexity=None,
        integrations_degraded=0,
    )


# ─── Project load runs ──────────────────────────────────────────────

@router.get("/projects/{project_id}/load-runs")
async def project_load_runs(project_id: str, environment: Optional[str] = None, _: User = Depends(get_current_user)):
    from app.models.load import LoadRun
    from app.models.conversion import Conversion
    await _get_project(project_id)
    oid = PydanticObjectId(project_id)
    convs = await Conversion.find(Conversion.project_id == oid).to_list()
    conv_ids = [c.id for c in convs]
    if not conv_ids:
        return []
    q = LoadRun.find({"conversion_id": {"$in": conv_ids}})
    if environment:
        q = q.find(LoadRun.environment == environment)
    runs = await q.sort("-started_at").to_list()
    return [
        {**r.model_dump(), "id": str(r.id), "conversion_id": str(r.conversion_id)}
        for r in runs
    ]
