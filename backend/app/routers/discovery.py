"""Discovery router — source connections and object discovery."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.v10 import (
    DiscoveredObject, DiscoveryRun, SourceConnection,
)

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class ConnectionCreate(BaseModel):
    project_id: Optional[str] = None
    system_type: str
    name: str
    host: Optional[str] = None
    port: Optional[int] = None
    service_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None          # plain — encrypted on save
    account_id: Optional[str] = None
    base_url: Optional[str] = None
    token_url: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None    # plain — encrypted on save


class ConnectionOut(BaseModel):
    id: str
    project_id: Optional[str] = None
    system_type: str
    name: str
    host: Optional[str] = None
    port: Optional[int] = None
    service_name: Optional[str] = None
    username: Optional[str] = None
    account_id: Optional[str] = None
    base_url: Optional[str] = None
    last_tested_at: Optional[datetime] = None
    last_test_ok: Optional[bool] = None
    last_test_error: Optional[str] = None
    created_at: datetime


class DiscoveryRunOut(BaseModel):
    id: str
    connection_id: str
    project_id: Optional[str] = None
    status: str
    modules_requested: List[str]
    objects_found: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime


class DiscoveredObjectOut(BaseModel):
    id: str
    run_id: str
    connection_id: str
    project_id: Optional[str] = None
    module: Optional[str] = None
    object_name: str
    object_type: str
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    columns: List[Dict[str, Any]]
    suggested_fbdi_object: Optional[str] = None
    suggestion_confidence: float
    selected: bool
    created_at: datetime


def _fernet_encrypt(plain: Optional[str]) -> Optional[str]:
    """Encrypt a plaintext credential string using Fernet if available."""
    if not plain:
        return None
    try:
        from cryptography.fernet import Fernet
        import os, base64
        key = os.environ.get("FERNET_KEY")
        if not key:
            # Generate a key on the fly (dev mode — not persistent)
            return f"PLAIN:{plain}"
        f = Fernet(key.encode())
        return f.encrypt(plain.encode()).decode()
    except ImportError:
        # cryptography package not installed — store marked plaintext
        return f"PLAIN:{plain}"


def _conn_to_out(c: SourceConnection) -> ConnectionOut:
    return ConnectionOut(
        id=str(c.id),
        project_id=str(c.project_id) if c.project_id else None,
        system_type=c.system_type,
        name=c.name,
        host=c.host,
        port=c.port,
        service_name=c.service_name,
        username=c.username,
        account_id=c.account_id,
        base_url=c.base_url,
        last_tested_at=c.last_tested_at,
        last_test_ok=c.last_test_ok,
        last_test_error=c.last_test_error,
        created_at=c.created_at,
    )


def _run_to_out(r: DiscoveryRun) -> DiscoveryRunOut:
    return DiscoveryRunOut(
        id=str(r.id),
        connection_id=str(r.connection_id),
        project_id=str(r.project_id) if r.project_id else None,
        status=r.status,
        modules_requested=r.modules_requested,
        objects_found=r.objects_found,
        started_at=r.started_at,
        completed_at=r.completed_at,
        error_message=r.error_message,
        created_at=r.created_at,
    )


# ── Source Connections ─────────────────────────────────────────────────────────

@router.post("/connections", response_model=ConnectionOut, status_code=201)
async def create_connection(body: ConnectionCreate):
    conn = SourceConnection(
        project_id=PydanticObjectId(body.project_id) if body.project_id else None,
        system_type=body.system_type,
        name=body.name,
        host=body.host,
        port=body.port,
        service_name=body.service_name,
        username=body.username,
        encrypted_password=_fernet_encrypt(body.password),
        account_id=body.account_id,
        base_url=body.base_url,
        token_url=body.token_url,
        client_id=body.client_id,
        encrypted_client_secret=_fernet_encrypt(body.client_secret),
    )
    await conn.insert()
    return _conn_to_out(conn)


@router.get("/connections", response_model=List[ConnectionOut])
async def list_connections(project_id: Optional[str] = None):
    if project_id:
        conns = await SourceConnection.find(
            SourceConnection.project_id == PydanticObjectId(project_id)
        ).to_list()
    else:
        conns = await SourceConnection.find_all().to_list()
    return [_conn_to_out(c) for c in conns]


@router.get("/connections/{conn_id}", response_model=ConnectionOut)
async def get_connection(conn_id: str):
    conn = await SourceConnection.get(PydanticObjectId(conn_id))
    if not conn:
        raise HTTPException(404, "Connection not found")
    return _conn_to_out(conn)


@router.delete("/connections/{conn_id}", status_code=204)
async def delete_connection(conn_id: str):
    conn = await SourceConnection.get(PydanticObjectId(conn_id))
    if not conn:
        raise HTTPException(404, "Connection not found")
    await conn.delete()


@router.post("/connections/{conn_id}/test", response_model=ConnectionOut)
async def test_connection(conn_id: str):
    """Perform a live connectivity test against the source system."""
    conn = await SourceConnection.get(PydanticObjectId(conn_id))
    if not conn:
        raise HTTPException(404, "Connection not found")

    ok = False
    error_msg = None
    try:
        if conn.system_type == "oracle_ebs":
            import oracledb
            dsn = f"{conn.host}:{conn.port or 1521}/{conn.service_name}"
            # Use oracledb thin mode (no client install required)
            c = oracledb.connect(user=conn.username, password="", dsn=dsn)
            c.close()
            ok = True
        else:
            # For REST-based sources do a trivial HTTP ping (head request)
            import httpx
            if conn.base_url:
                r = httpx.head(conn.base_url, timeout=5)
                ok = r.status_code < 500
            else:
                ok = False
                error_msg = "No base_url configured for this connection type."
    except Exception as exc:
        error_msg = str(exc)

    conn.last_tested_at = datetime.utcnow()
    conn.last_test_ok = ok
    conn.last_test_error = error_msg
    conn.updated_at = datetime.utcnow()
    await conn.save()
    return _conn_to_out(conn)


# ── Discovery Runs ─────────────────────────────────────────────────────────────

class StartRunBody(BaseModel):
    modules: List[str] = []


@router.post("/connections/{conn_id}/runs", response_model=DiscoveryRunOut, status_code=201)
async def start_discovery_run(conn_id: str, body: StartRunBody):
    conn = await SourceConnection.get(PydanticObjectId(conn_id))
    if not conn:
        raise HTTPException(404, "Connection not found")

    run = DiscoveryRun(
        connection_id=conn.id,
        project_id=conn.project_id,
        modules_requested=body.modules,
        status="running",
        started_at=datetime.utcnow(),
    )
    await run.insert()

    # Run a lightweight simulation discovery (real connectors plug in here)
    try:
        objects = _simulate_discovery(conn, body.modules)
        for obj_data in objects:
            obj = DiscoveredObject(
                run_id=run.id,
                connection_id=conn.id,
                project_id=conn.project_id,
                **obj_data,
            )
            await obj.insert()
        run.objects_found = len(objects)
        run.status = "completed"
        run.completed_at = datetime.utcnow()
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.completed_at = datetime.utcnow()

    await run.save()
    return _run_to_out(run)


def _simulate_discovery(conn: SourceConnection, modules: List[str]) -> List[Dict[str, Any]]:
    """Return mock discovered objects. Replace with real connector calls in production."""
    sample = {
        "GL": [
            {"module": "GL", "object_name": "GL_JE_HEADERS", "object_type": "table",
             "row_count": 15000, "column_count": 24, "columns": [],
             "suggested_fbdi_object": "General Ledger Journals", "suggestion_confidence": 0.91},
            {"module": "GL", "object_name": "GL_JE_LINES", "object_type": "table",
             "row_count": 85000, "column_count": 18, "columns": [],
             "suggested_fbdi_object": "General Ledger Journals", "suggestion_confidence": 0.87},
        ],
        "AP": [
            {"module": "AP", "object_name": "AP_INVOICES_ALL", "object_type": "table",
             "row_count": 42000, "column_count": 67, "columns": [],
             "suggested_fbdi_object": "Payables Standard Invoice Import", "suggestion_confidence": 0.93},
        ],
        "AR": [
            {"module": "AR", "object_name": "RA_CUSTOMER_TRX_ALL", "object_type": "table",
             "row_count": 31000, "column_count": 52, "columns": [],
             "suggested_fbdi_object": "Receivables AutoInvoice Import", "suggestion_confidence": 0.88},
        ],
        "INV": [
            {"module": "INV", "object_name": "MTL_SYSTEM_ITEMS_B", "object_type": "table",
             "row_count": 8500, "column_count": 122, "columns": [],
             "suggested_fbdi_object": "Item Import", "suggestion_confidence": 0.95},
        ],
    }
    results = []
    for mod in (modules or list(sample.keys())):
        results.extend(sample.get(mod.upper(), []))
    return results


@router.get("/connections/{conn_id}/runs", response_model=List[DiscoveryRunOut])
async def list_runs(conn_id: str):
    runs = await DiscoveryRun.find(
        DiscoveryRun.connection_id == PydanticObjectId(conn_id)
    ).sort("-created_at").to_list()
    return [_run_to_out(r) for r in runs]


@router.get("/runs/{run_id}/objects", response_model=List[DiscoveredObjectOut])
async def list_discovered_objects(run_id: str):
    objs = await DiscoveredObject.find(
        DiscoveredObject.run_id == PydanticObjectId(run_id)
    ).to_list()
    return [
        DiscoveredObjectOut(
            id=str(o.id),
            run_id=str(o.run_id),
            connection_id=str(o.connection_id),
            project_id=str(o.project_id) if o.project_id else None,
            module=o.module,
            object_name=o.object_name,
            object_type=o.object_type,
            row_count=o.row_count,
            column_count=o.column_count,
            columns=o.columns,
            suggested_fbdi_object=o.suggested_fbdi_object,
            suggestion_confidence=o.suggestion_confidence,
            selected=o.selected,
            created_at=o.created_at,
        )
        for o in objs
    ]


@router.patch("/objects/{obj_id}/select")
async def toggle_object_selection(obj_id: str, selected: bool = True):
    obj = await DiscoveredObject.get(PydanticObjectId(obj_id))
    if not obj:
        raise HTTPException(404, "Object not found")
    obj.selected = selected
    await obj.save()
    return {"id": obj_id, "selected": selected}
