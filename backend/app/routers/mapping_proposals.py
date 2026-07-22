"""Upload a mapping document, review what it changes, then apply it."""
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.models.mapping_proposal import MappingProposal
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/mapping-proposals", tags=["mapping-proposals"])

_ALLOWED = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls")


def _out(p: MappingProposal) -> dict:
    d = p.model_dump()
    d["id"] = str(p.id)
    d["client_id"] = str(p.client_id) if p.client_id else None
    for r in d.get("rows", []):
        if r.get("existing_learning_id"):
            r["existing_learning_id"] = str(r["existing_learning_id"])
    return d


@router.post("/analyze")
async def analyze(
    files: list[UploadFile] = File(...),
    client_id: Optional[str] = Form(None),
    target_object: Optional[str] = Form(None),
    source_system: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
):
    """Parse uploaded mapping documents and classify every row. Writes nothing."""
    import tempfile
    from pathlib import Path as _P

    from app.services.mapping_ingest_service import analyze_mapping_file

    files = [f for f in files if f and f.filename]
    if not files:
        raise HTTPException(400, "No files uploaded.")
    cid = PydanticObjectId(client_id) if client_id else None

    out = []
    for f in files:
        suffix = _P(f.filename).suffix.lower()
        if suffix not in _ALLOWED:
            out.append({"file_name": f.filename, "error": "Not a CSV or Excel file."})
            continue
        data = await f.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            p = await analyze_mapping_file(
                tmp_path, file_name=f.filename, client_id=cid,
                default_object=(target_object or None),
                source_system=(source_system or None), uploaded_by=user.email,
            )
            out.append(_out(p))
        except Exception as exc:                                # noqa: BLE001
            out.append({"file_name": f.filename, "error": str(exc)[:300]})
        finally:
            _P(tmp_path).unlink(missing_ok=True)
    return {"proposals": out}


@router.get("")
async def list_proposals(
    status: Optional[str] = Query(None),
    client_id: Optional[str] = Query(None),
    _: User = Depends(get_current_user),
):
    """Summaries only — the row payload is large and rarely needed for a list."""
    q: dict = {}
    if status:
        q["status"] = status
    if client_id:
        q["client_id"] = PydanticObjectId(client_id)
    items = await MappingProposal.find(q).sort("-uploaded_at").to_list()
    return [{
        "id": str(p.id), "file_name": p.file_name, "target_object": p.target_object,
        "source_system": p.source_system, "status": p.status,
        "layout_method": p.layout_method, "layout_note": p.layout_note,
        "count_new": p.count_new, "count_unchanged": p.count_unchanged,
        "count_conflict": p.count_conflict, "count_skipped": p.count_skipped,
        "learnings_written": p.learnings_written,
        "conversions_touched": p.conversions_touched,
        "uploaded_by": p.uploaded_by, "uploaded_at": p.uploaded_at,
        "applied_at": p.applied_at,
    } for p in items]


@router.get("/{proposal_id}")
async def get_proposal(proposal_id: str, _: User = Depends(get_current_user)):
    p = await MappingProposal.get(PydanticObjectId(proposal_id))
    if not p:
        raise HTTPException(404, "Proposal not found.")
    return _out(p)


@router.post("/{proposal_id}/decide")
async def decide(
    proposal_id: str,
    payload: dict,
    _: User = Depends(get_current_user),
):
    """Approve or reject rows.

    ``{"decision": "approved"|"rejected"|"pending", "row_nos": [...]}`` — omit
    ``row_nos`` to apply the decision to every conflicting row at once, which is the
    common case: a reviewer reads the contradictions and accepts or refuses the
    document wholesale.
    """
    p = await MappingProposal.get(PydanticObjectId(proposal_id))
    if not p:
        raise HTTPException(404, "Proposal not found.")
    if p.status == "applied":
        raise HTTPException(400, "This proposal has already been applied.")
    decision = (payload or {}).get("decision")
    if decision not in ("approved", "rejected", "pending"):
        raise HTTPException(400, "decision must be approved, rejected or pending.")
    row_nos = (payload or {}).get("row_nos")
    targets = set(row_nos) if row_nos else None

    changed = 0
    for r in p.rows:
        if targets is None and r.status != "conflict":
            continue
        if targets is not None and r.row_no not in targets:
            continue
        r.decision = decision
        changed += 1
    await p.save()
    return {"updated": changed, "decision": decision}


@router.post("/{proposal_id}/apply")
async def apply(proposal_id: str, user: User = Depends(get_current_user)):
    """Write approved rows to the library and roll them onto existing conversions."""
    p = await MappingProposal.get(PydanticObjectId(proposal_id))
    if not p:
        raise HTTPException(404, "Proposal not found.")
    if p.status == "applied":
        raise HTTPException(400, "This proposal has already been applied.")
    pending = [r.row_no for r in p.rows if r.status == "conflict" and r.decision == "pending"]
    if pending:
        raise HTTPException(400, (
            f"{len(pending)} conflicting row(s) still need a decision. Approve or "
            f"reject them first — contradictions are never applied silently."))
    from app.services.mapping_ingest_service import apply_proposal
    return await apply_proposal(p, applied_by=user.email)


@router.delete("/{proposal_id}")
async def discard(proposal_id: str, _: User = Depends(get_current_user)):
    p = await MappingProposal.get(PydanticObjectId(proposal_id))
    if not p:
        raise HTTPException(404, "Proposal not found.")
    await p.delete()
    return {"deleted": True}
