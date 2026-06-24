"""Source Connection CRUD — MongoDB/Beanie version."""
from __future__ import annotations

import socket
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.user import User
from app.models.v10 import SourceConnection
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api", tags=["source-connections"])


# ─── Request schemas ───────────────────────────────────────────────────────────

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
    display_name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    service_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    mock_mode: Optional[bool] = None
    auth_type: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    connection_metadata: Optional[Dict[str, Any]] = None


# ─── Response schemas (matching frontend SourceConnection type) ────────────────

class ProbeResult(BaseModel):
    name: str
    status: str          # "ok" | "failed" | "skipped"
    latency_ms: Optional[float] = None
    message: Optional[str] = None


class ConnectionTestResult(BaseModel):
    overall_status: str  # "ok" | "failed"
    latency_ms: Optional[float] = None
    version: Optional[str] = None
    detected_metadata: Optional[Dict[str, Any]] = None
    probes: List[ProbeResult] = []
    message: Optional[str] = None
    tested_at: str


class SourceConnectionOut(BaseModel):
    id: str
    project_id: Optional[str] = None
    source_system: str
    display_name: str
    endpoint: Optional[str] = None
    auth_type: str = "mock"
    has_credentials: bool = False
    mock_mode: bool = False
    status: str = "draft"          # "draft" | "ok" | "failed"
    last_test_at: Optional[str] = None
    last_test_details: Optional[Dict[str, Any]] = None
    created_at: str
    updated_at: str


def _to_out(c: SourceConnection) -> SourceConnectionOut:
    is_mock = c.encrypted_password == "__mock__" or not c.encrypted_password
    has_creds = bool(c.encrypted_password and c.encrypted_password != "__mock__")

    # Derive auth_type
    stored_auth = getattr(c, "auth_type", None)
    if stored_auth:
        auth_type = stored_auth
    elif is_mock:
        auth_type = "mock"
    elif c.username:
        auth_type = "db_basic"
    elif c.client_id:
        auth_type = "oauth2_client_credentials"
    elif c.consumer_key:
        auth_type = "oauth1_tba"
    else:
        auth_type = "mock"

    # Derive status
    if c.last_test_ok is True:
        status = "ok"
    elif c.last_test_ok is False:
        status = "failed"
    else:
        status = "draft"

    # Structured last_test_details
    last_test_details = getattr(c, "last_test_details", None)
    if not last_test_details and c.last_tested_at:
        last_test_details = {
            "overall_status": status,
            "message": c.last_test_error if c.last_test_error else ("Connection healthy" if status == "ok" else None),
        }

    return SourceConnectionOut(
        id=str(c.id),
        project_id=str(c.project_id) if c.project_id else None,
        source_system=c.system_type,
        display_name=c.name,
        endpoint=c.base_url,
        auth_type=auth_type,
        has_credentials=has_creds,
        mock_mode=is_mock,
        status=status,
        last_test_at=c.last_tested_at.isoformat() if c.last_tested_at else None,
        last_test_details=last_test_details,
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
    )


# ─── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("/source-connections", response_model=SourceConnectionOut, status_code=201)
async def create_connection(
    payload: SourceConnectionCreate,
    user: User = Depends(get_current_user),
):
    system_type = payload.source_system or payload.system_type or "manual"
    name = payload.display_name or payload.name or system_type
    project_oid = PydanticObjectId(payload.project_id) if payload.project_id else None

    creds = payload.credentials or {}
    password = payload.password or creds.get("password") or creds.get("token")
    if payload.mock_mode:
        password = "__mock__"

    # Extract host/port/service from connection_metadata if not provided directly
    meta = payload.connection_metadata or {}
    host = payload.host or meta.get("host")
    service_name = payload.service_name or meta.get("service_name")
    port_raw = meta.get("port")
    port = payload.port or (int(port_raw) if port_raw and str(port_raw).isdigit() else None)

    # Parse endpoint string "host:port/service" if individual fields still missing
    endpoint = payload.endpoint or payload.base_url or ""
    if not host and endpoint:
        parts = endpoint.split(":")
        host = parts[0] if parts else None
        if len(parts) > 1:
            rest = parts[1]
            slash = rest.find("/")
            if slash != -1:
                try:
                    port = port or int(rest[:slash])
                except ValueError:
                    pass
                service_name = service_name or rest[slash + 1:]

    conn = SourceConnection(
        project_id=project_oid,
        system_type=system_type,
        name=name,
        host=host,
        port=port,
        service_name=service_name,
        username=payload.username or creds.get("username"),
        encrypted_password=password,
        account_id=payload.account_id,
        consumer_key=payload.consumer_key,
        client_id=payload.client_id,
        encrypted_client_secret=payload.client_secret or creds.get("client_secret"),
        token_url=payload.token_url,
        base_url=endpoint or None,
        auth_type=payload.auth_type,
        connection_metadata=meta or None,
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
    if "display_name" in data:
        conn.name = data.pop("display_name")
    if "username" in data:
        conn.username = data.pop("username")
    if "host" in data:
        conn.host = data.pop("host")
    if "port" in data:
        conn.port = data.pop("port")
    if "service_name" in data:
        conn.service_name = data.pop("service_name")
    # Rebuild the display endpoint whenever any of host/port/service_name were touched
    if conn.host and conn.service_name:
        conn.base_url = f"{conn.host}:{conn.port or 1521}/{conn.service_name}"
    creds = data.pop("credentials", {}) or {}
    if creds.get("password"):
        conn.encrypted_password = creds["password"]
    if creds.get("username"):
        conn.username = creds["username"]
    for k, v in data.items():
        if hasattr(conn, k):
            setattr(conn, k, v)
    conn.updated_at = datetime.utcnow()
    await conn.save()
    return _to_out(conn)


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


# ─── Connection test ───────────────────────────────────────────────────────────

@router.post("/source-connections/{connection_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    connection_id: str,
    _: User = Depends(get_current_user),
):
    conn = await SourceConnection.get(connection_id)
    if not conn:
        raise HTTPException(404, "Connection not found")

    is_mock = conn.encrypted_password == "__mock__" or not conn.encrypted_password
    now = datetime.utcnow()

    # ── Mock mode ──────────────────────────────────────────────────────────────
    if is_mock:
        probes = [
            ProbeResult(name="TCP connect",     status="skipped", message="Mock mode — no live traffic"),
            ProbeResult(name="Oracle auth",     status="skipped", message="Mock mode — deterministic fixture"),
            ProbeResult(name="Schema probe",    status="skipped", message="Mock mode — fixture data in use"),
        ]
        conn.last_tested_at = now
        conn.last_test_ok = True
        conn.last_test_error = None
        conn.last_test_details = {
            "overall_status": "ok", "latency_ms": 12.0, "message": "Mock mode active",
            "probes": [p.model_dump() for p in probes],
        }
        await conn.save()
        return ConnectionTestResult(
            overall_status="ok", latency_ms=12.0,
            probes=probes,
            message="Mock mode: deterministic fixtures active. Uncheck 'Use mock mode' to probe the live instance.",
            tested_at=now.isoformat(),
        )

    # ── Live mode — Oracle EBS ─────────────────────────────────────────────────
    host = conn.host
    port = conn.port or 1521
    service_name = conn.service_name
    username = conn.username
    # Password stored as plain in this router (no Fernet here)
    password = conn.encrypted_password
    if password and password.startswith("PLAIN:"):
        password = password[6:]

    probes: list[ProbeResult] = []
    overall_ok = True
    total_latency = 0.0
    detected_metadata: Dict[str, Any] = {}
    db_version: Optional[str] = None

    # Probe 1: TCP connect
    t0 = time.monotonic()
    try:
        s = socket.create_connection((host, port), timeout=8)
        s.close()
        ms = round((time.monotonic() - t0) * 1000, 1)
        total_latency += ms
        probes.append(ProbeResult(name="TCP connect", status="ok", latency_ms=ms,
                                  message=f"{host}:{port} reachable"))
    except Exception as e:
        ms = round((time.monotonic() - t0) * 1000, 1)
        overall_ok = False
        probes.append(ProbeResult(name="TCP connect", status="failed", latency_ms=ms,
                                  message=str(e)))

    # Probe 2: Oracle authentication via JDBC (handles legacy 10g password hashes)
    if overall_ok:
        t0 = time.monotonic()
        try:
            import jaydebeapi
            jdbc_url = f"jdbc:oracle:thin:@{host}:{port}/{service_name}"
            db_conn = jaydebeapi.connect(
                "oracle.jdbc.OracleDriver",
                jdbc_url,
                [username, password],
                "/app/ojdbc11.jar",
            )
            ms = round((time.monotonic() - t0) * 1000, 1)
            total_latency += ms
            probes.append(ProbeResult(name="Oracle auth", status="ok", latency_ms=ms,
                                      message=f"Authenticated as {username}"))

            # Probe 3: Schema probe
            t0 = time.monotonic()
            try:
                cur = db_conn.cursor()
                # Oracle version
                cur.execute("SELECT banner FROM v$version WHERE rownum = 1")
                row = cur.fetchone()
                db_version = row[0].strip() if row else None
                # Accessible tables (JDBC uses ? for positional params)
                cur.execute(
                    "SELECT COUNT(*) FROM all_tables WHERE owner = ?",
                    [(username or "").upper()],
                )
                row2 = cur.fetchone()
                table_count = int(row2[0]) if row2 else 0
                cur.close()
                db_conn.close()
                ms = round((time.monotonic() - t0) * 1000, 1)
                total_latency += ms
                probes.append(ProbeResult(name="Schema probe", status="ok", latency_ms=ms,
                                          message=f"{table_count} tables visible to {username}"))
                detected_metadata = {
                    "db_version": db_version,
                    "visible_table_count": table_count,
                    "username": username,
                    "service_name": service_name,
                }
            except Exception as e2:
                ms = round((time.monotonic() - t0) * 1000, 1)
                probes.append(ProbeResult(name="Schema probe", status="failed", latency_ms=ms,
                                          message=str(e2)))
                try:
                    db_conn.close()
                except Exception:
                    pass

        except ImportError:
            overall_ok = False
            probes.append(ProbeResult(name="Oracle auth", status="failed",
                                      message="jaydebeapi not installed on server"))
            probes.append(ProbeResult(name="Schema probe", status="skipped",
                                      message="Skipped — auth failed"))
        except Exception as e:
            ms = round((time.monotonic() - t0) * 1000, 1)
            overall_ok = False
            probes.append(ProbeResult(name="Oracle auth", status="failed", latency_ms=ms,
                                      message=str(e)))
            probes.append(ProbeResult(name="Schema probe", status="skipped",
                                      message="Skipped — auth failed"))
    else:
        probes.append(ProbeResult(name="Oracle auth", status="skipped",
                                  message="Skipped — TCP connect failed"))
        probes.append(ProbeResult(name="Schema probe", status="skipped",
                                  message="Skipped — TCP connect failed"))

    # Persist result
    err_probe = next((p for p in probes if p.status == "failed"), None)
    conn.last_tested_at = now
    conn.last_test_ok = overall_ok
    conn.last_test_error = err_probe.message if err_probe else None
    conn.last_test_details = {
        "overall_status": "ok" if overall_ok else "failed",
        "latency_ms": round(total_latency, 1),
        "version": db_version,
        "detected_metadata": detected_metadata or None,
        "message": "Connection healthy" if overall_ok else (err_probe.message if err_probe else "Failed"),
        "probes": [p.model_dump() for p in probes],
    }
    await conn.save()

    return ConnectionTestResult(
        overall_status="ok" if overall_ok else "failed",
        latency_ms=round(total_latency, 1),
        version=db_version,
        detected_metadata=detected_metadata or None,
        probes=probes,
        message="Connection healthy" if overall_ok else f"Connection failed: {err_probe.message if err_probe else 'unknown error'}",
        tested_at=now.isoformat(),
    )
