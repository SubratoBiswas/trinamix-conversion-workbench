"""Audit log router — immutable event log (v10)."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.models.v10 import AuditEvent

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditEventOut(BaseModel):
    id: str
    project_id: Optional[str] = None
    conversion_id: Optional[str] = None
    actor: str
    action: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    occurred_at: datetime


def _to_out(e: AuditEvent) -> AuditEventOut:
    return AuditEventOut(
        id=str(e.id),
        project_id=str(e.project_id) if e.project_id else None,
        conversion_id=str(e.conversion_id) if e.conversion_id else None,
        actor=e.actor,
        action=e.action,
        entity_type=e.entity_type,
        entity_id=e.entity_id,
        detail=e.detail,
        ip_address=e.ip_address,
        occurred_at=e.occurred_at,
    )


@router.get("/events", response_model=List[AuditEventOut])
async def list_events(
    project_id: Optional[str] = Query(None),
    conversion_id: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(200, le=1000),
):
    query = AuditEvent.find()
    if project_id:
        query = query.find(AuditEvent.project_id == PydanticObjectId(project_id))
    if conversion_id:
        query = query.find(AuditEvent.conversion_id == PydanticObjectId(conversion_id))
    if actor:
        query = query.find(AuditEvent.actor == actor)
    if action:
        query = query.find(AuditEvent.action == action)
    events = await query.sort("-occurred_at").limit(limit).to_list()
    return [_to_out(e) for e in events]


@router.post("/events", response_model=AuditEventOut, status_code=201)
async def create_event(body: Dict[str, Any]):
    """Internal endpoint — called by other services to append audit events."""
    event = AuditEvent(
        project_id=PydanticObjectId(body["project_id"]) if body.get("project_id") else None,
        conversion_id=PydanticObjectId(body["conversion_id"]) if body.get("conversion_id") else None,
        actor=body.get("actor", "system"),
        action=body["action"],
        entity_type=body.get("entity_type"),
        entity_id=body.get("entity_id"),
        detail=body.get("detail"),
        ip_address=body.get("ip_address"),
    )
    await event.insert()
    return _to_out(event)
