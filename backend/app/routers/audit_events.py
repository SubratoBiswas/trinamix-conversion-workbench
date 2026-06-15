"""Audit events at /api/audit-events — wraps the v10 AuditEvent model."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.models.user import User
from app.models.v10 import AuditEvent
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api", tags=["audit-events"])


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


@router.get("/audit-events", response_model=List[AuditEventOut])
async def list_audit_events(
    project_id: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    action_prefix: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    limit: int = Query(200, le=1000),
    _: User = Depends(get_current_user),
):
    query = AuditEvent.find()
    if project_id:
        query = query.find(AuditEvent.project_id == PydanticObjectId(project_id))
    if actor:
        query = query.find(AuditEvent.actor == actor)
    if target_type:
        query = query.find(AuditEvent.entity_type == target_type)
    events = await query.sort("-occurred_at").limit(limit).to_list()
    if action_prefix:
        events = [e for e in events if e.action.startswith(action_prefix)]
    return [_to_out(e) for e in events]
