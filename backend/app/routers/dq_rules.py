"""Validation & cleansing rule management (scoped by FBDI object + client).

Create rules three ways: EXTRACT (derive validation rules from an FBDI template),
UPLOAD (import a rules workbook/CSV/JSON), or MANUAL (CRUD). Plus EXPORT the current
rule set. Rules feed the Generate-time DQ step and are runnable on demand.
"""
from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.models.dq_rule import RULE_KINDS, DataQualityRule
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/dq-rules", tags=["dq-rules"])


async def _resolve_client(client_id: Optional[str]) -> Optional[PydanticObjectId]:
    if client_id:
        return PydanticObjectId(client_id)
    from app.services.client_service import ensure_default_client
    c = await ensure_default_client()
    return c.id


def _out(r: DataQualityRule) -> dict:
    return {"id": str(r.id), "kind": r.kind, "target_object": r.target_object,
            "field": r.field, "rule_type": r.rule_type, "params": r.params or {},
            "severity": r.severity, "description": r.description, "source": r.source,
            "active": r.active, "is_global": r.is_global,
            "client_id": str(r.client_id) if r.client_id else None}


@router.get("")
async def list_rules(
    target_object: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    client_id: Optional[str] = Query(None),
    _: User = Depends(get_current_user),
):
    from app.services.client_service import scope_query
    cid = await _resolve_client(client_id)
    q: dict = {**(await scope_query(cid))}
    if target_object:
        q["target_object"] = target_object
    if kind:
        q["kind"] = kind
    rows = await DataQualityRule.find(q).to_list()
    return {"rules": [_out(r) for r in rows], "count": len(rows)}


class RuleIn(BaseModel):
    kind: str                       # "validation" | "cleansing"
    target_object: str
    field: Optional[str] = None
    rule_type: str
    params: dict = {}
    severity: str = "error"
    description: Optional[str] = None
    is_global: bool = False
    client_id: Optional[str] = None


@router.post("")
async def create_rule(body: RuleIn, user: User = Depends(get_current_user)):
    if body.kind not in RULE_KINDS:
        raise HTTPException(400, f"kind must be one of {RULE_KINDS}")
    cid = None if body.is_global else await _resolve_client(body.client_id)
    r = DataQualityRule(
        kind=body.kind, target_object=body.target_object, field=body.field,
        rule_type=body.rule_type.upper(), params=body.params or {},
        severity=body.severity, description=body.description, source="manual",
        is_global=body.is_global, client_id=cid, created_by=user.email,
    )
    await r.insert()
    return _out(r)


class RulePatch(BaseModel):
    field: Optional[str] = None
    rule_type: Optional[str] = None
    params: Optional[dict] = None
    severity: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


@router.patch("/{rule_id}")
async def update_rule(rule_id: str, body: RulePatch, _: User = Depends(get_current_user)):
    r = await DataQualityRule.get(PydanticObjectId(rule_id))
    if not r:
        raise HTTPException(404, "Rule not found")
    data = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if "rule_type" in data and data["rule_type"]:
        data["rule_type"] = data["rule_type"].upper()
    data["updated_at"] = datetime.utcnow()
    await r.set(data)
    await r.sync()
    return _out(r)


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str, _: User = Depends(get_current_user)):
    r = await DataQualityRule.get(PydanticObjectId(rule_id))
    if not r:
        raise HTTPException(404, "Rule not found")
    await r.delete()
    return {"deleted": rule_id}


class ExtractIn(BaseModel):
    target_object: str
    template_id: str
    client_id: Optional[str] = None


@router.post("/extract")
async def extract_rules(body: ExtractIn, user: User = Depends(get_current_user)):
    """Derive validation rules (required / max-length / value-set / numeric) from an
    FBDI template's field metadata and store them (source='extracted', editable)."""
    from app.services.dq_rule_service import extract_rules_from_template
    cid = await _resolve_client(body.client_id)
    res = await extract_rules_from_template(body.target_object, body.template_id, cid, user.email)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


class AiProposeIn(BaseModel):
    target_object: str
    template_id: str
    sample_dataset_id: Optional[str] = None
    client_id: Optional[str] = None


@router.post("/ai-propose")
async def ai_propose(body: AiProposeIn, _: User = Depends(get_current_user)):
    """AI proposes validation + cleansing rules from the FBDI field metadata (and a
    data sample if given). Returns proposals for REVIEW — nothing is saved. Falls
    back to deterministic proposals when AI is unavailable."""
    from app.services.dq_rule_service import ai_propose_rules
    cid = await _resolve_client(body.client_id)
    res = await ai_propose_rules(body.target_object, body.template_id, cid, body.sample_dataset_id)
    if res.get("error"):
        raise HTTPException(400, res["error"])
    return res


class BulkIn(BaseModel):
    rules: list[dict]
    is_global: bool = False
    client_id: Optional[str] = None


@router.post("/bulk")
async def bulk_create(body: BulkIn, user: User = Depends(get_current_user)):
    """Save a list of reviewed proposals as rules (source='manual')."""
    cid = None if body.is_global else await _resolve_client(body.client_id)
    created = 0
    for r in body.rules:
        rt = str(r.get("rule_type") or "").strip()
        if not rt or not r.get("target_object"):
            continue
        await DataQualityRule(
            kind=str(r.get("kind") or "validation"), target_object=str(r.get("target_object")),
            field=(r.get("field") or None), rule_type=rt.upper(), params=r.get("params") or {},
            severity=str(r.get("severity") or "error"), description=(r.get("description") or None),
            source="manual", is_global=body.is_global, client_id=cid, created_by=user.email,
        ).insert()
        created += 1
    return {"created": created}


@router.post("/upload")
async def upload_rules(
    kind: str = Query("validation"),
    target_object: str = Query(...),
    client_id: Optional[str] = Query(None),
    is_global: bool = Query(False),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Import rules from a workbook/CSV/JSON. Recognised columns/keys: field,
    rule_type, params (JSON or k=v;k=v), severity, description. kind + target_object
    come from the query so a plain list of rules can be uploaded per object."""
    if kind not in RULE_KINDS:
        raise HTTPException(400, f"kind must be one of {RULE_KINDS}")
    raw = await file.read()
    name = (file.filename or "").lower()
    records: list[dict] = []
    try:
        if name.endswith(".json") or raw[:1] in (b"[", b"{"):
            doc = json.loads(raw.decode("utf-8"))
            records = doc if isinstance(doc, list) else doc.get("rules", [])
        else:
            import pandas as pd
            if name.endswith((".xlsx", ".xlsm")):
                df = pd.read_excel(io.BytesIO(raw))
            else:
                df = pd.read_csv(io.BytesIO(raw))
            df.columns = [str(c).strip().lower() for c in df.columns]
            records = df.fillna("").to_dict("records")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Could not parse rules file: {exc}")

    def _parse_params(v) -> dict:
        if isinstance(v, dict):
            return v
        s = str(v or "").strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except Exception:  # noqa: BLE001 — fall back to k=v;k=v
            out: dict = {}
            for part in s.split(";"):
                if "=" in part:
                    k, val = part.split("=", 1)
                    out[k.strip()] = val.strip()
            return out

    cid = None if is_global else await _resolve_client(client_id)
    created = 0
    for rec in records:
        rt = str(rec.get("rule_type") or rec.get("rule") or "").strip()
        if not rt:
            continue
        await DataQualityRule(
            kind=str(rec.get("kind") or kind), target_object=str(rec.get("target_object") or target_object),
            field=(str(rec.get("field")).strip() or None) if rec.get("field") else None,
            rule_type=rt.upper(), params=_parse_params(rec.get("params")),
            severity=str(rec.get("severity") or "error"), description=(rec.get("description") or None),
            source="uploaded", is_global=is_global, client_id=cid, created_by=user.email,
        ).insert()
        created += 1
    return {"created": created, "kind": kind, "target_object": target_object}


@router.get("/export")
async def export_rules(
    target_object: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    client_id: Optional[str] = Query(None),
    _: User = Depends(get_current_user),
):
    """Export the current rule set as CSV (round-trips with /upload)."""
    import csv
    from app.services.client_service import scope_query
    cid = await _resolve_client(client_id)
    q: dict = {**(await scope_query(cid))}
    if target_object:
        q["target_object"] = target_object
    if kind:
        q["kind"] = kind
    rows = await DataQualityRule.find(q).to_list()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["kind", "target_object", "field", "rule_type", "params", "severity", "description", "source", "active"])
    for r in rows:
        w.writerow([r.kind, r.target_object, r.field or "", r.rule_type,
                    json.dumps(r.params or {}), r.severity, r.description or "", r.source, r.active])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=dq_rules.csv"})
