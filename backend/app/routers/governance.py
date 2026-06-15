"""Governance router — issues, risks, sign-offs, dress rehearsals, cutover tasks,
reconciliation checks (v10)."""
from datetime import datetime
from typing import List, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.models.v10 import (
    CutoverTask, DressRehearsal, Issue, ReconciliationCheck, Risk, SignOff,
)

router = APIRouter(prefix="/api/governance", tags=["governance"])


# ─────────────────────────────────────────────
# Issues
# ─────────────────────────────────────────────

class IssueCreate(BaseModel):
    project_id: str
    title: str
    description: Optional[str] = None
    severity: str = "medium"
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


class IssueOut(BaseModel):
    id: str
    project_id: str
    title: str
    description: Optional[str] = None
    severity: str
    status: str
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


@router.post("/issues", response_model=IssueOut, status_code=201)
async def create_issue(body: IssueCreate):
    issue = Issue(
        project_id=PydanticObjectId(body.project_id),
        title=body.title,
        description=body.description,
        severity=body.severity,
        owner=body.owner,
        due_date=body.due_date,
    )
    await issue.insert()
    return _issue_out(issue)


@router.get("/issues", response_model=List[IssueOut])
async def list_issues(
    project_id: str = Query(...),
    status: Optional[str] = Query(None),
):
    q = Issue.find(Issue.project_id == PydanticObjectId(project_id))
    if status:
        q = q.find(Issue.status == status)
    return [_issue_out(i) for i in await q.sort("-created_at").to_list()]


@router.patch("/issues/{iid}", response_model=IssueOut)
async def update_issue(iid: str, body: IssueUpdate):
    issue = await Issue.get(PydanticObjectId(iid))
    if not issue:
        raise HTTPException(404, "Issue not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(issue, field, val)
    if body.status in ("resolved", "closed") and not issue.resolved_at:
        issue.resolved_at = datetime.utcnow()
    issue.updated_at = datetime.utcnow()
    await issue.save()
    return _issue_out(issue)


@router.delete("/issues/{iid}", status_code=204)
async def delete_issue(iid: str):
    issue = await Issue.get(PydanticObjectId(iid))
    if not issue:
        raise HTTPException(404, "Issue not found")
    await issue.delete()


# ─────────────────────────────────────────────
# Risks
# ─────────────────────────────────────────────

class RiskCreate(BaseModel):
    project_id: str
    title: str
    description: Optional[str] = None
    likelihood: str = "medium"
    impact: str = "medium"
    mitigation_plan: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[datetime] = None


class RiskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    likelihood: Optional[str] = None
    impact: Optional[str] = None
    status: Optional[str] = None
    mitigation_plan: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[datetime] = None


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
    due_date: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


@router.post("/risks", response_model=RiskOut, status_code=201)
async def create_risk(body: RiskCreate):
    risk = Risk(
        project_id=PydanticObjectId(body.project_id),
        title=body.title,
        description=body.description,
        likelihood=body.likelihood,
        impact=body.impact,
        mitigation_plan=body.mitigation_plan,
        owner=body.owner,
        due_date=body.due_date,
    )
    await risk.insert()
    return _risk_out(risk)


@router.get("/risks", response_model=List[RiskOut])
async def list_risks(project_id: str = Query(...), status: Optional[str] = Query(None)):
    q = Risk.find(Risk.project_id == PydanticObjectId(project_id))
    if status:
        q = q.find(Risk.status == status)
    return [_risk_out(r) for r in await q.sort("-created_at").to_list()]


@router.patch("/risks/{rid}", response_model=RiskOut)
async def update_risk(rid: str, body: RiskUpdate):
    risk = await Risk.get(PydanticObjectId(rid))
    if not risk:
        raise HTTPException(404, "Risk not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(risk, field, val)
    risk.updated_at = datetime.utcnow()
    await risk.save()
    return _risk_out(risk)


@router.delete("/risks/{rid}", status_code=204)
async def delete_risk(rid: str):
    risk = await Risk.get(PydanticObjectId(rid))
    if not risk:
        raise HTTPException(404, "Risk not found")
    await risk.delete()


# ─────────────────────────────────────────────
# Sign-offs
# ─────────────────────────────────────────────

class SignOffCreate(BaseModel):
    project_id: str
    conversion_id: Optional[str] = None
    checkpoint: str
    notes: Optional[str] = None


class SignOffUpdate(BaseModel):
    status: Optional[str] = None
    signed_off_by: Optional[str] = None
    notes: Optional[str] = None


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


@router.post("/sign-offs", response_model=SignOffOut, status_code=201)
async def create_sign_off(body: SignOffCreate):
    so = SignOff(
        project_id=PydanticObjectId(body.project_id),
        conversion_id=PydanticObjectId(body.conversion_id) if body.conversion_id else None,
        checkpoint=body.checkpoint,
        notes=body.notes,
    )
    await so.insert()
    return _so_out(so)


@router.get("/sign-offs", response_model=List[SignOffOut])
async def list_sign_offs(project_id: str = Query(...)):
    items = await SignOff.find(
        SignOff.project_id == PydanticObjectId(project_id)
    ).to_list()
    return [_so_out(i) for i in items]


@router.patch("/sign-offs/{soid}", response_model=SignOffOut)
async def update_sign_off(soid: str, body: SignOffUpdate):
    so = await SignOff.get(PydanticObjectId(soid))
    if not so:
        raise HTTPException(404, "Sign-off not found")
    if body.status:
        so.status = body.status
    if body.signed_off_by:
        so.signed_off_by = body.signed_off_by
        so.signed_off_at = datetime.utcnow()
    if body.notes is not None:
        so.notes = body.notes
    await so.save()
    return _so_out(so)


# ─────────────────────────────────────────────
# Dress Rehearsals + Cutover Tasks
# ─────────────────────────────────────────────

class RehearsalCreate(BaseModel):
    project_id: str
    name: str
    scheduled_at: Optional[datetime] = None
    notes: Optional[str] = None
    conducted_by: Optional[str] = None


class RehearsalUpdate(BaseModel):
    name: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    status: Optional[str] = None
    outcome: Optional[str] = None
    records_processed: Optional[int] = None
    records_failed: Optional[int] = None
    issues_found: Optional[int] = None
    notes: Optional[str] = None
    conducted_by: Optional[str] = None


class RehearsalOut(BaseModel):
    id: str
    project_id: str
    name: str
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str
    outcome: Optional[str] = None
    records_processed: int
    records_failed: int
    issues_found: int
    notes: Optional[str] = None
    conducted_by: Optional[str] = None
    created_at: datetime


class TaskCreate(BaseModel):
    project_id: str
    rehearsal_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    sequence: int = 0
    owner: Optional[str] = None
    estimated_minutes: Optional[int] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    sequence: Optional[int] = None
    owner: Optional[str] = None
    estimated_minutes: Optional[int] = None
    actual_minutes: Optional[int] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class CutoverTaskOut(BaseModel):
    id: str
    project_id: str
    rehearsal_id: Optional[str] = None
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


@router.post("/rehearsals", response_model=RehearsalOut, status_code=201)
async def create_rehearsal(body: RehearsalCreate):
    r = DressRehearsal(
        project_id=PydanticObjectId(body.project_id),
        name=body.name,
        scheduled_at=body.scheduled_at,
        notes=body.notes,
        conducted_by=body.conducted_by,
    )
    await r.insert()
    return _reh_out(r)


@router.get("/rehearsals", response_model=List[RehearsalOut])
async def list_rehearsals(project_id: str = Query(...)):
    items = await DressRehearsal.find(
        DressRehearsal.project_id == PydanticObjectId(project_id)
    ).sort("-created_at").to_list()
    return [_reh_out(i) for i in items]


@router.patch("/rehearsals/{rid}", response_model=RehearsalOut)
async def update_rehearsal(rid: str, body: RehearsalUpdate):
    r = await DressRehearsal.get(PydanticObjectId(rid))
    if not r:
        raise HTTPException(404, "Rehearsal not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(r, field, val)
    if body.status == "running" and not r.started_at:
        r.started_at = datetime.utcnow()
    if body.status in ("completed", "failed") and not r.completed_at:
        r.completed_at = datetime.utcnow()
    await r.save()
    return _reh_out(r)


@router.post("/cutover-tasks", response_model=CutoverTaskOut, status_code=201)
async def create_cutover_task(body: TaskCreate):
    t = CutoverTask(
        project_id=PydanticObjectId(body.project_id),
        rehearsal_id=PydanticObjectId(body.rehearsal_id) if body.rehearsal_id else None,
        title=body.title,
        description=body.description,
        sequence=body.sequence,
        owner=body.owner,
        estimated_minutes=body.estimated_minutes,
    )
    await t.insert()
    return _task_out(t)


@router.get("/cutover-tasks", response_model=List[CutoverTaskOut])
async def list_cutover_tasks(
    project_id: str = Query(...),
    rehearsal_id: Optional[str] = Query(None),
):
    q = CutoverTask.find(CutoverTask.project_id == PydanticObjectId(project_id))
    if rehearsal_id:
        q = q.find(CutoverTask.rehearsal_id == PydanticObjectId(rehearsal_id))
    return [_task_out(t) for t in await q.sort("sequence").to_list()]


@router.patch("/cutover-tasks/{tid}", response_model=CutoverTaskOut)
async def update_cutover_task(tid: str, body: TaskUpdate):
    t = await CutoverTask.get(PydanticObjectId(tid))
    if not t:
        raise HTTPException(404, "Task not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(t, field, val)
    if body.status == "in_progress" and not t.started_at:
        t.started_at = datetime.utcnow()
    if body.status in ("completed", "failed", "skipped") and not t.completed_at:
        t.completed_at = datetime.utcnow()
    await t.save()
    return _task_out(t)


# ─────────────────────────────────────────────
# Reconciliation Checks
# ─────────────────────────────────────────────

class ReconCreate(BaseModel):
    project_id: str
    conversion_id: Optional[str] = None
    load_run_id: Optional[str] = None
    check_name: str
    check_type: str = "count"
    source_value: Optional[str] = None
    fusion_value: Optional[str] = None
    tolerance: float = 0.0
    notes: Optional[str] = None


class ReconOut(BaseModel):
    id: str
    project_id: str
    conversion_id: Optional[str] = None
    load_run_id: Optional[str] = None
    check_name: str
    check_type: str
    source_value: Optional[str] = None
    fusion_value: Optional[str] = None
    tolerance: float
    passed: Optional[bool] = None
    variance: Optional[float] = None
    notes: Optional[str] = None
    checked_at: datetime


@router.post("/reconciliation", response_model=ReconOut, status_code=201)
async def create_recon_check(body: ReconCreate):
    # Compute pass/fail automatically if both values are provided
    passed = None
    variance = None
    if body.source_value is not None and body.fusion_value is not None:
        try:
            sv = float(body.source_value)
            fv = float(body.fusion_value)
            variance = abs(sv - fv)
            passed = variance <= body.tolerance
        except ValueError:
            passed = body.source_value == body.fusion_value

    rc = ReconciliationCheck(
        project_id=PydanticObjectId(body.project_id),
        conversion_id=PydanticObjectId(body.conversion_id) if body.conversion_id else None,
        load_run_id=PydanticObjectId(body.load_run_id) if body.load_run_id else None,
        check_name=body.check_name,
        check_type=body.check_type,
        source_value=body.source_value,
        fusion_value=body.fusion_value,
        tolerance=body.tolerance,
        passed=passed,
        variance=variance,
        notes=body.notes,
    )
    await rc.insert()
    return _recon_out(rc)


@router.get("/reconciliation", response_model=List[ReconOut])
async def list_recon_checks(
    project_id: str = Query(...),
    conversion_id: Optional[str] = Query(None),
):
    q = ReconciliationCheck.find(
        ReconciliationCheck.project_id == PydanticObjectId(project_id)
    )
    if conversion_id:
        q = q.find(ReconciliationCheck.conversion_id == PydanticObjectId(conversion_id))
    return [_recon_out(r) for r in await q.sort("-checked_at").to_list()]


@router.get("/summary/{project_id}")
async def governance_summary(project_id: str):
    pid = PydanticObjectId(project_id)
    open_issues = await Issue.find(Issue.project_id == pid, Issue.status == "open").count()
    high_issues = await Issue.find(Issue.project_id == pid, Issue.severity.in_(["high","critical"]), Issue.status == "open").count()  # type: ignore
    open_risks = await Risk.find(Risk.project_id == pid, Risk.status == "identified").count()
    pending_so = await SignOff.find(SignOff.project_id == pid, SignOff.status == "pending").count()
    rehearsal_count = await DressRehearsal.find(DressRehearsal.project_id == pid).count()
    recon_total = await ReconciliationCheck.find(ReconciliationCheck.project_id == pid).count()
    recon_passed = await ReconciliationCheck.find(ReconciliationCheck.project_id == pid, ReconciliationCheck.passed == True).count()  # noqa: E712
    return {
        "open_issues": open_issues,
        "high_priority_issues": high_issues,
        "open_risks": open_risks,
        "pending_sign_offs": pending_so,
        "dress_rehearsals": rehearsal_count,
        "reconciliation_checks": recon_total,
        "reconciliation_passed": recon_passed,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _issue_out(i: Issue) -> IssueOut:
    return IssueOut(
        id=str(i.id), project_id=str(i.project_id),
        title=i.title, description=i.description,
        severity=i.severity, status=i.status,
        owner=i.owner, due_date=i.due_date,
        resolved_at=i.resolved_at, resolution_notes=i.resolution_notes,
        created_at=i.created_at, updated_at=i.updated_at,
    )


def _risk_out(r: Risk) -> RiskOut:
    return RiskOut(
        id=str(r.id), project_id=str(r.project_id),
        title=r.title, description=r.description,
        likelihood=r.likelihood, impact=r.impact, status=r.status,
        mitigation_plan=r.mitigation_plan, owner=r.owner,
        due_date=r.due_date,
        created_at=r.created_at, updated_at=r.updated_at,
    )


def _so_out(s: SignOff) -> SignOffOut:
    return SignOffOut(
        id=str(s.id), project_id=str(s.project_id),
        conversion_id=str(s.conversion_id) if s.conversion_id else None,
        checkpoint=s.checkpoint,
        signed_off_by=s.signed_off_by, signed_off_at=s.signed_off_at,
        status=s.status, notes=s.notes, created_at=s.created_at,
    )


def _reh_out(r: DressRehearsal) -> RehearsalOut:
    return RehearsalOut(
        id=str(r.id), project_id=str(r.project_id),
        name=r.name, scheduled_at=r.scheduled_at,
        started_at=r.started_at, completed_at=r.completed_at,
        status=r.status, outcome=r.outcome,
        records_processed=r.records_processed, records_failed=r.records_failed,
        issues_found=r.issues_found, notes=r.notes,
        conducted_by=r.conducted_by, created_at=r.created_at,
    )


def _task_out(t: CutoverTask) -> CutoverTaskOut:
    return CutoverTaskOut(
        id=str(t.id), project_id=str(t.project_id),
        rehearsal_id=str(t.rehearsal_id) if t.rehearsal_id else None,
        title=t.title, description=t.description,
        sequence=t.sequence, owner=t.owner,
        estimated_minutes=t.estimated_minutes, actual_minutes=t.actual_minutes,
        status=t.status, started_at=t.started_at, completed_at=t.completed_at,
        notes=t.notes, created_at=t.created_at,
    )


def _recon_out(r: ReconciliationCheck) -> ReconOut:
    return ReconOut(
        id=str(r.id), project_id=str(r.project_id),
        conversion_id=str(r.conversion_id) if r.conversion_id else None,
        load_run_id=str(r.load_run_id) if r.load_run_id else None,
        check_name=r.check_name, check_type=r.check_type,
        source_value=r.source_value, fusion_value=r.fusion_value,
        tolerance=r.tolerance, passed=r.passed, variance=r.variance,
        notes=r.notes, checked_at=r.checked_at,
    )
