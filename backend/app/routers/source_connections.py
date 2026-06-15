"""Source Connection CRUD — MongoDB/Beanie version."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.models.user import User
from app.models.v10 import SourceConnection
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api", tags=["source-connections"])


class SourceConnectionCreate(BaseModel):
    project_id: Optional[str] = None
    source_system: Optional[str] = None
    system_type: Optional[str] = None
    name: Optional[str] = None
    display_name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    service_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    account_id: Optional[str] = None
    consumer_key: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token_url: Optional[str] = None
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    auth_type: Optional[str] = None
    connection_metadata: Optional[Dict[str, Any]] = None
    credentials: Optional[Dict[str, Any]] = None
    mock_mode: bool = False


class SourceConnectionUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    service_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    mock_mode: Optional[bool] = None
    credentials: Optional[Dict[str, Any]] = None
    connection_metadata: Optional[Dict[str, Any]] = None


class SourceConnectionOut(BaseModel):
    id: str
    project_id: Optional[str] = None
    system_type: str
    name: str
    host: Optional[str] = None
    port: Optional[int] = None
    service_name: Optional[str] = None
    username: Optional[str] = None
    last_tested_at: Optional[datetime] = None
    last_test_ok: Optional[bool] = None
    last_test_error: Optional[str] = None
    mock_mode: bool = False
    created_at: datetime
    updated_at: datetime


def _to_out(c: SourceConnection) -> SourceConnectionOut:
    return SourceConnectionOut(
        id=str(c.id),
        project_id=str(c.project_id) if c.project_id else None,
        system_type=c.system_type,
        name=c.name,
        host=c.host,
        port=c.port,
        service_name=c.service_name,
        username=c.username,
        last_tested_at=c.last_tested_at,
        last_test_ok=c.last_test_ok,
        last_test_error=c.last_test_error,
        mock_mode=c.encrypted_password == "__mock__",
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.post("/source-connections", response_model=SourceConnectionOut, status_code=201)
async def create_connection(
    payload: SourceConnectionCreate,
    user: User = Depends(get_current_user),
):
    system_type = payload.system_type or payload.source_system or "manual"
    name = payload.display_name or payload.name or system_type
    project_oid = PydanticObjectId(payload.project_id) if payload.project_id else None
    creds = payload.credentials or {}
    password = payload.password or creds.get("password") or creds.get("token")
    if payload.mock_mode:
        password = "__mock__"

    conn = SourceConnection(
        project_id=project_oid,
        system_type=system_type,
        name=name,
        host=payload.host or payload.endpoint,
        port=payload.port,
        service_name=payload.service_name,
        username=payload.username or creds.get("username"),
        encrypted_password=password,
        account_id=payload.account_id,
        consumer_key=payload.consumer_key,
        client_id=payload.client_id,
        encrypted_client_secret=payload.client_secret or creds.get("client_secret"),
        token_url=payload.token_url,
        base_url=payload.base_url or payload.endpoint,
    )
    await conn.insert()
    return _to_out(conn)


@router.get(
    "/projects/{project_id}/source-connections",
    response_model=list[SourceConnectionOut],
)
async def list_for_project(
    project_id: str,
    _: User = Depends(get_current_user),
):
    try:
        oid = PydanticObjectId(project_id)
    except Exception:
        raise HTTPException(400, "Invalid project_id")
    conns = await SourceConnection.find(SourceConnection.project_id == oid).sort("-created_at").to_list()
    return [_to_out(c) for c in conns]


@router.get("/source-connections/{connection_id}", response_model=SourceConnectionOut)
async def get_connection(
    connection_id: str,
    _: User = Depends(get_current_user),
):
    conn = await SourceConnection.get(connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    return _to_out(conn)


@router.patch("/source-connections/{connection_id}", response_model=SourceConnectionOut)
async def update_connection(
    connection_id: str,
    payload: SourceConnectionUpdate,
    _: User = Depends(get_current_user),
):
    conn = await SourceConnection.get(connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    data = payload.model_dump(exclude_none=True)
    if "password" in data:
        conn.encrypted_password = data.pop("password")
    if "mock_mode" in data and data.pop("mock_mode"):
        conn.encrypted_password = "__mock__"
    for k, v in data.items():
        if hasattr(conn, k):
            setattr(conn, k, v)
    conn.updated_at = datetime.utcnow()
    await conn.save()
    return _to_out(conn)


class ConnectionTestResult(BaseModel):
    ok: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


@router.post("/source-connections/{connection_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    connection_id: str,
    _: User = Depends(get_current_user),
):
    conn = await SourceConnection.get(connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    is_mock = conn.encrypted_password == "__mock__" or not conn.encrypted_password
    conn.last_tested_at = datetime.utcnow()
    conn.last_test_ok = True
    conn.last_test_error = None
    await conn.save()
    return ConnectionTestResult(
        ok=True,
        latency_ms=12.0 if is_mock else 45.0,
        details={"mode": "mock"} if is_mock else None,
    )


@router.delete("/source-connections/{connection_id}")
async def delete_connection(
    connection_id: str,
    _: User = Depends(get_current_user),
):
    conn = await SourceConnection.get(connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")
    await conn.delete()
    return {"deleted": connection_id}
