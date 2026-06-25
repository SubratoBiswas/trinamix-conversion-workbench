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


def _jdbc_url_from_conn(conn: "SourceConnection") -> str:
    """Build JDBC URL using base_url as the authoritative source (user's last saved value).
    Falls back to individual host/port/service_name fields."""
    base = conn.base_url or ""
    if base:
        parts = base.split(":")
        host = parts[0].strip() if parts else (conn.host or "")
        rest = parts[1] if len(parts) > 1 else ""
        slash = rest.find("/")
        if slash != -1:
            try:
                port = int(rest[:slash]) or 1521
            except ValueError:
                port = conn.port or 1521
            svc = rest[slash + 1:] or conn.service_name or ""
        else:
            port = conn.port or 1521
            svc = conn.service_name or ""
    else:
        host = conn.host or ""
        port = conn.port or 1521
        svc = conn.service_name or ""
    return f"jdbc:oracle:thin:@{host}:{port}/{svc}"


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
        import os
        key = os.environ.get("FERNET_KEY")
        if not key:
            return f"PLAIN:{plain}"
        f = Fernet(key.encode())
        return f.encrypt(plain.encode()).decode()
    except ImportError:
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
    conn = await SourceConnection.get(PydanticObjectId(conn_id))
    if not conn:
        raise HTTPException(404, "Connection not found")

    ok = False
    error_msg = None
    try:
        if conn.system_type == "oracle_ebs":
            import jaydebeapi
            password = conn.encrypted_password or ""
            if password.startswith("PLAIN:"):
                password = password[6:]
            if password == "__mock__":
                ok = True
            else:
                jdbc_url = _jdbc_url_from_conn(conn)
                c = jaydebeapi.connect(
                    "oracle.jdbc.OracleDriver",
                    jdbc_url,
                    [conn.username, password],
                    "/app/ojdbc11.jar",
                )
                c.close()
                ok = True
        else:
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

    is_mock = (conn.encrypted_password == "__mock__" or not conn.encrypted_password)

    try:
        if is_mock:
            objects_data = _mock_ebs_objects()
        else:
            objects_data, _ = await _live_oracle_discovery(conn)

        for obj_data in objects_data:
            obj = DiscoveredObject(
                run_id=run.id,
                connection_id=conn.id,
                project_id=conn.project_id,
                columns=[],
                **{k: v for k, v in obj_data.items() if k != "columns"},
            )
            await obj.insert()
        run.objects_found = len(objects_data)
        run.status = "completed"
        run.completed_at = datetime.utcnow()
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.completed_at = datetime.utcnow()

    await run.save()
    return _run_to_out(run)


def _simulate_discovery(conn: SourceConnection, modules: List[str]) -> List[Dict[str, Any]]:
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


# ══════════════════════════════════════════════════════════════════════════════
# Project-scoped discovery (v10 frontend)
# ══════════════════════════════════════════════════════════════════════════════

from fastapi import Depends
from app.models.user import User
from app.services.auth_service import get_current_user

project_router = APIRouter(prefix="/api", tags=["discovery-project"])

# ── EBS module / pillar helpers ────────────────────────────────────────────────

# Table-name prefix → EBS module label
_EBS_PREFIX_MAP: List[tuple] = [
    ("GL_",   "General Ledger"),
    ("AP_",   "Accounts Payable"),
    ("AR_",   "Accounts Receivable"),
    ("RA_",   "Accounts Receivable"),
    ("FA_",   "Fixed Assets"),
    ("PO_",   "Procurement"),
    ("MTL_",  "Inventory"),
    ("INV_",  "Inventory"),
    ("HR_",   "Human Resources"),
    ("PER_",  "Human Resources"),
    ("PA_",   "Project Accounting"),
    ("CE_",   "Cash Management"),
    ("OE_",   "Order Management"),
    ("BOM_",  "Manufacturing"),
    ("WIP_",  "Manufacturing"),
    ("FND_",  "Configuration"),
    ("XDO_",  "BI Reports"),
    ("IBY_",  "Payments"),
    ("ICX_",  "Self Service"),
    ("AK_",   "Configuration"),
    ("BIS_",  "BI Reports"),
    ("WF_",   "Processes"),
]

# EBS module → discovery pillar code
_MODULE_PILLAR: Dict[str, str] = {
    "General Ledger":     "data",
    "Accounts Payable":   "data",
    "Accounts Receivable":"data",
    "Fixed Assets":       "data",
    "Procurement":        "data",
    "Inventory":          "data",
    "Cash Management":    "data",
    "Order Management":   "data",
    "Payments":           "data",
    "Manufacturing":      "data",
    "Project Accounting": "data",
    "Human Resources":    "processes",
    "Processes":          "processes",
    "Configuration":      "configuration",
    "Self Service":       "customisations",
    "BI Reports":         "reports",
    "Integrations":       "integrations",
    "Other":              "data",
}


def _infer_module_from_table(table_name: str) -> str:
    upper = table_name.upper()
    for prefix, module in _EBS_PREFIX_MAP:
        if upper.startswith(prefix):
            return module
    return "Other"


def _module_to_pillar(module: Optional[str]) -> str:
    return _MODULE_PILLAR.get(module or "Other", "data")


def _derive_risk(row_count: Optional[int]) -> str:
    if row_count is None:
        return "low"
    if row_count > 100_000:
        return "high"
    if row_count > 10_000:
        return "medium"
    return "low"


def _obj_to_frontend(o: DiscoveredObject) -> Dict[str, Any]:
    """Map the MongoDB DiscoveredObject to the shape the frontend expects."""
    pillar = _module_to_pillar(o.module)
    risk = _derive_risk(o.row_count)
    row_str = f"{o.row_count:,} rows" if o.row_count else "Row count unavailable"
    return {
        "id": str(o.id),
        "pillar": pillar,
        "category": o.object_type or "table",
        "name": o.object_name,
        "risk_level": risk,
        "last_used_at": None,
        "metadata_json": {
            "fusion_target": o.suggested_fbdi_object or "—",
            "row_count": o.row_count,
            "column_count": o.column_count,
            "risk_reason": row_str,
            "at_risk_group": o.module or "General",
            "context_bucket": (pillar or "data").capitalize(),
            "confidence": round(o.suggestion_confidence * 100),
        },
    }


# ── Mock discovery objects (rich set) ─────────────────────────────────────────

def _mock_ebs_objects() -> List[Dict[str, Any]]:
    """Comprehensive EBS object inventory for mock-mode scans."""
    return [
        # General Ledger — data pillar
        {"module": "General Ledger", "object_name": "GL_JE_HEADERS",        "object_type": "table", "row_count": 142_000, "column_count": 24, "suggestion_confidence": 0.91, "suggested_fbdi_object": "General Ledger Journals"},
        {"module": "General Ledger", "object_name": "GL_JE_LINES",          "object_type": "table", "row_count": 852_000, "column_count": 18, "suggestion_confidence": 0.88, "suggested_fbdi_object": "General Ledger Journals"},
        {"module": "General Ledger", "object_name": "GL_BALANCES",          "object_type": "table", "row_count": 28_500,  "column_count": 15, "suggestion_confidence": 0.85, "suggested_fbdi_object": "Opening Balances"},
        {"module": "General Ledger", "object_name": "GL_CODE_COMBINATIONS", "object_type": "table", "row_count": 4_200,   "column_count": 30, "suggestion_confidence": 0.92, "suggested_fbdi_object": "Chart of Accounts Import"},
        # Accounts Payable — data pillar
        {"module": "Accounts Payable", "object_name": "AP_INVOICES_ALL",      "object_type": "table", "row_count": 42_000,  "column_count": 67, "suggestion_confidence": 0.93, "suggested_fbdi_object": "Payables Standard Invoice Import"},
        {"module": "Accounts Payable", "object_name": "AP_INVOICE_LINES_ALL", "object_type": "table", "row_count": 118_000, "column_count": 45, "suggestion_confidence": 0.91, "suggested_fbdi_object": "Payables Standard Invoice Import"},
        {"module": "Accounts Payable", "object_name": "AP_SUPPLIERS",         "object_type": "table", "row_count": 3_200,   "column_count": 52, "suggestion_confidence": 0.94, "suggested_fbdi_object": "Supplier Import"},
        {"module": "Accounts Payable", "object_name": "AP_PAYMENT_SCHEDULES_ALL","object_type": "table","row_count": 38_000, "column_count": 22, "suggestion_confidence": 0.80, "suggested_fbdi_object": "Payables Payment Import"},
        # Accounts Receivable — data pillar
        {"module": "Accounts Receivable", "object_name": "RA_CUSTOMER_TRX_ALL", "object_type": "table", "row_count": 31_000, "column_count": 52, "suggestion_confidence": 0.88, "suggested_fbdi_object": "Receivables AutoInvoice Import"},
        {"module": "Accounts Receivable", "object_name": "AR_CUSTOMERS_V",      "object_type": "view",  "row_count": 5_600,  "column_count": 28, "suggestion_confidence": 0.90, "suggested_fbdi_object": "Customer Import"},
        {"module": "Accounts Receivable", "object_name": "AR_CASH_RECEIPTS_ALL","object_type": "table", "row_count": 14_200, "column_count": 34, "suggestion_confidence": 0.82, "suggested_fbdi_object": "Receivables Receipt Import"},
        # Fixed Assets — data pillar
        {"module": "Fixed Assets", "object_name": "FA_ADDITIONS_V",          "object_type": "view",  "row_count": 4_300, "column_count": 35, "suggestion_confidence": 0.87, "suggested_fbdi_object": "Fixed Asset Additions"},
        {"module": "Fixed Assets", "object_name": "FA_DEPRN_SUMMARY",        "object_type": "table", "row_count": 8_600, "column_count": 18, "suggestion_confidence": 0.79, "suggested_fbdi_object": "Asset Depreciation Import"},
        # Procurement — data pillar
        {"module": "Procurement", "object_name": "PO_HEADERS_ALL",  "object_type": "table", "row_count": 9_800,  "column_count": 42, "suggestion_confidence": 0.89, "suggested_fbdi_object": "Purchase Order Import"},
        {"module": "Procurement", "object_name": "PO_LINES_ALL",    "object_type": "table", "row_count": 24_500, "column_count": 38, "suggestion_confidence": 0.87, "suggested_fbdi_object": "Purchase Order Import"},
        {"module": "Procurement", "object_name": "PO_VENDORS",      "object_type": "table", "row_count": 2_100,  "column_count": 28, "suggestion_confidence": 0.84, "suggested_fbdi_object": "Supplier Import"},
        # Inventory — data pillar
        {"module": "Inventory", "object_name": "MTL_SYSTEM_ITEMS_B",            "object_type": "table", "row_count": 8_500,  "column_count": 122,"suggestion_confidence": 0.95, "suggested_fbdi_object": "Item Import"},
        {"module": "Inventory", "object_name": "MTL_ONHAND_QUANTITIES_DETAIL",  "object_type": "table", "row_count": 2_400,  "column_count": 18, "suggestion_confidence": 0.83, "suggested_fbdi_object": "Inventory Balance Import"},
        {"module": "Inventory", "object_name": "MTL_TRANSACTION_TYPES",         "object_type": "table", "row_count": 320,    "column_count": 12, "suggestion_confidence": 0.70, "suggested_fbdi_object": "Inventory Transaction Type"},
        # Human Resources — processes pillar
        {"module": "Human Resources", "object_name": "PER_ALL_PEOPLE_F",       "object_type": "table", "row_count": 1_847, "column_count": 48, "suggestion_confidence": 0.91, "suggested_fbdi_object": "Worker Import"},
        {"module": "Human Resources", "object_name": "PER_ALL_ASSIGNMENTS_F",  "object_type": "table", "row_count": 2_100, "column_count": 55, "suggestion_confidence": 0.89, "suggested_fbdi_object": "Worker Import"},
        {"module": "Human Resources", "object_name": "PER_JOBS",               "object_type": "table", "row_count": 480,   "column_count": 18, "suggestion_confidence": 0.85, "suggested_fbdi_object": "Job Import"},
        # Configuration — configuration pillar
        {"module": "Configuration", "object_name": "FND_FLEX_VALUE_SETS",  "object_type": "table", "row_count": 280,    "column_count": 12, "suggestion_confidence": 0.82, "suggested_fbdi_object": "Value Sets Import"},
        {"module": "Configuration", "object_name": "FND_LOOKUP_VALUES",    "object_type": "table", "row_count": 15_200, "column_count": 14, "suggestion_confidence": 0.85, "suggested_fbdi_object": "Lookup Values Import"},
        {"module": "Configuration", "object_name": "FND_CURRENCIES",       "object_type": "table", "row_count": 180,    "column_count": 10, "suggestion_confidence": 0.88, "suggested_fbdi_object": "Currency Import"},
        # BI Reports — reports pillar
        {"module": "BI Reports", "object_name": "XDO_DS_DEFINITIONS_B",  "object_type": "table", "row_count": 142, "column_count": 8,  "suggestion_confidence": 0.70, "suggested_fbdi_object": "BI Publisher Reports"},
        {"module": "BI Reports", "object_name": "XDO_TEMPLATES_B",       "object_type": "table", "row_count": 89,  "column_count": 10, "suggestion_confidence": 0.68, "suggested_fbdi_object": "BI Publisher Templates"},
        # Customisations — customisations pillar (Self Service / APPS custom tables)
        {"module": "Self Service", "object_name": "XX_CUSTOM_HEADERS",    "object_type": "table", "row_count": 5_200, "column_count": 18, "suggestion_confidence": 0.55, "suggested_fbdi_object": None},
        {"module": "Self Service", "object_name": "XX_CUSTOM_LINES",      "object_type": "table", "row_count": 14_800,"column_count": 14, "suggestion_confidence": 0.52, "suggested_fbdi_object": None},
    ]


# ── Live Oracle EBS discovery ──────────────────────────────────────────────────

async def _live_oracle_discovery(conn: SourceConnection) -> tuple:
    """
    Connect to Oracle EBS via JDBC and inventory visible tables.
    Returns (objects_list, scan_notes).
    JDBC handles legacy 10g password hashes (DPY-3015 bypass).
    """
    import jaydebeapi

    password = conn.encrypted_password or ""
    if password.startswith("PLAIN:"):
        password = password[6:]

    jdbc_url = _jdbc_url_from_conn(conn)
    db = jaydebeapi.connect(
        "oracle.jdbc.OracleDriver",
        jdbc_url,
        [conn.username, password],
        "/app/ojdbc11.jar",
    )
    cur = db.cursor()

    # Query all tables visible to the connected user, ordered by row count desc
    cur.execute("""
        SELECT table_name, num_rows, last_analyzed
        FROM all_tables
        WHERE owner = ?
        ORDER BY num_rows DESC NULLS LAST
        FETCH FIRST 300 ROWS ONLY
    """, [(conn.username or "APPS").upper()])

    rows = cur.fetchall()
    cur.close()
    db.close()

    objects: List[Dict[str, Any]] = []
    for table_name, num_rows, _ in rows:
        module = _infer_module_from_table(table_name)
        objects.append({
            "module":                module,
            "object_name":           table_name,
            "object_type":           "table",
            "row_count":             num_rows,
            "column_count":          None,
            "suggestion_confidence": 0.75,
            "suggested_fbdi_object": None,
        })

    scan_notes = (
        f"Live scan completed — {len(objects)} tables inventoried from "
        f"{conn.host}:{conn.port or 1521}/{conn.service_name} as {conn.username}."
    )
    return objects, scan_notes


# ── Response schemas (matching frontend DiscoveryRun / DiscoveryLatest types) ──

class DiscoveryRunDetail(BaseModel):
    """Matches the frontend DiscoveryRun interface."""
    id: str
    status: str = "completed"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    total_objects: int = 0
    pillar_counts: Dict[str, int] = {}
    integration_health: Dict[str, Any] = {}
    complexity_score: float = 0.0
    scan_notes: Optional[str] = None
    is_mock: bool = False


class DiscoveryLatestOut(BaseModel):
    """Matches the frontend DiscoveryLatest interface."""
    run: Optional[DiscoveryRunDetail] = None
    integrations: List[Dict[str, Any]] = []


# ── Project-scoped run endpoint ────────────────────────────────────────────────

@project_router.post("/projects/{project_id}/discovery/run", response_model=DiscoveryRunDetail)
async def run_discovery(
    project_id: str,
    _: User = Depends(get_current_user),
):
    """
    Trigger a discovery scan for a project.
    - Mock connection  → deterministic fixture objects (instant)
    - Live connection  → queries Oracle EBS all_tables (real data)
    """
    try:
        pid = PydanticObjectId(project_id)
    except Exception:
        raise HTTPException(400, "Invalid project_id")

    # Find the project's source connection
    conn = await SourceConnection.find_one(SourceConnection.project_id == pid)
    if not conn:
        # Auto-create a mock connection so Discovery still works on new projects
        conn = SourceConnection(
            project_id=pid,
            system_type="manual",
            name="Auto-created mock connection",
            encrypted_password="__mock__",
        )
        await conn.insert()

    is_mock = (conn.encrypted_password == "__mock__" or not conn.encrypted_password)

    # Delete previous runs + objects for this project (keep only the latest scan)
    old_runs = await DiscoveryRun.find(DiscoveryRun.project_id == pid).to_list()
    for old_run in old_runs:
        await DiscoveredObject.find(DiscoveredObject.run_id == old_run.id).delete()
    if old_runs:
        await DiscoveryRun.find(DiscoveryRun.project_id == pid).delete()

    # Create the new run record
    run = DiscoveryRun(
        connection_id=conn.id,
        project_id=pid,
        status="running",
        modules_requested=[],
        started_at=datetime.utcnow(),
    )
    await run.insert()

    try:
        if is_mock:
            objects_data = _mock_ebs_objects()
            scan_notes = (
                "Mock mode — deterministic EBS fixture objects. "
                "Uncheck 'Use mock mode' on the source connection to scan the live instance."
            )
        else:
            objects_data, scan_notes = await _live_oracle_discovery(conn)

        # Persist discovered objects
        for obj_data in objects_data:
            obj = DiscoveredObject(
                run_id=run.id,
                connection_id=conn.id,
                project_id=pid,
                columns=[],
                **{k: v for k, v in obj_data.items() if k != "columns"},
            )
            await obj.insert()

        # Build pillar counts from persisted objects
        all_objs = await DiscoveredObject.find(DiscoveredObject.run_id == run.id).to_list()
        pillar_counts: Dict[str, int] = {}
        for o in all_objs:
            p = _module_to_pillar(o.module)
            pillar_counts[p] = pillar_counts.get(p, 0) + 1

        # Complexity score: normalised 0-99 based on object count + risk mix
        high_risk  = sum(1 for o in all_objs if _derive_risk(o.row_count) == "high")
        mid_risk   = sum(1 for o in all_objs if _derive_risk(o.row_count) == "medium")
        complexity = min(99.0, round(
            (len(all_objs) * 0.5) + (high_risk * 3.0) + (mid_risk * 1.0), 1
        ))

        run.objects_found = len(all_objs)
        run.status = "completed"
        run.completed_at = datetime.utcnow()

    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.completed_at = datetime.utcnow()
        await run.save()
        raise HTTPException(500, f"Discovery scan failed: {exc}")

    await run.save()

    return DiscoveryRunDetail(
        id=str(run.id),
        status=run.status,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        total_objects=run.objects_found,
        pillar_counts=pillar_counts,
        integration_health={"healthy": max(0, pillar_counts.get("integrations", 0) - 1),
                            "degraded": 1 if pillar_counts.get("integrations", 0) > 0 else 0,
                            "not_tested": 0},
        complexity_score=complexity,
        scan_notes=scan_notes,
        is_mock=is_mock,
    )


# ── Project-scoped latest endpoint ────────────────────────────────────────────

@project_router.get("/projects/{project_id}/discovery/latest", response_model=DiscoveryLatestOut)
async def discovery_latest(
    project_id: str,
    _: User = Depends(get_current_user),
):
    """Return the most recent completed discovery run for a project."""
    try:
        pid = PydanticObjectId(project_id)
    except Exception:
        raise HTTPException(400, "Invalid project_id")

    run = await DiscoveryRun.find(
        DiscoveryRun.project_id == pid,
        DiscoveryRun.status == "completed",
    ).sort("-created_at").first_or_none()

    if not run:
        return DiscoveryLatestOut()

    objects = await DiscoveredObject.find(DiscoveredObject.run_id == run.id).to_list()

    # Build pillar counts using proper pillar codes
    pillar_counts: Dict[str, int] = {}
    for o in objects:
        p = _module_to_pillar(o.module)
        pillar_counts[p] = pillar_counts.get(p, 0) + 1

    # Check if the run used a mock connection
    conn = await SourceConnection.get(run.connection_id)
    is_mock = conn is None or conn.encrypted_password == "__mock__" or not conn.encrypted_password

    # Complexity score
    high_risk  = sum(1 for o in objects if _derive_risk(o.row_count) == "high")
    mid_risk   = sum(1 for o in objects if _derive_risk(o.row_count) == "medium")
    complexity = min(99.0, round(
        (len(objects) * 0.5) + (high_risk * 3.0) + (mid_risk * 1.0), 1
    ))

    scan_notes: Optional[str] = None
    if is_mock:
        scan_notes = (
            "Mock mode — deterministic EBS fixture objects. "
            "Uncheck 'Use mock mode' on the source connection to scan the live instance."
        )
    elif conn and conn.host:
        scan_notes = (
            f"Live scan from {conn.host}:{conn.port or 1521}/{conn.service_name} "
            f"— {len(objects)} tables inventoried."
        )

    run_detail = DiscoveryRunDetail(
        id=str(run.id),
        status=run.status,
        started_at=run.started_at.isoformat() if run.started_at else None,
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        total_objects=run.objects_found or len(objects),
        pillar_counts=pillar_counts,
        integration_health={
            "healthy": max(0, pillar_counts.get("integrations", 0) - 1),
            "degraded": 1 if pillar_counts.get("integrations", 0) > 0 else 0,
            "not_tested": 0,
        },
        complexity_score=complexity,
        scan_notes=scan_notes,
        is_mock=is_mock,
    )

    return DiscoveryLatestOut(run=run_detail, integrations=[])


# ── Project-scoped objects endpoint ───────────────────────────────────────────

@project_router.get("/discovery-runs/{run_id}/objects")
async def list_run_objects(
    run_id: str,
    pillar: Optional[str] = None,
    _: User = Depends(get_current_user),
):
    """
    Return discovered objects for a run in the frontend DiscoveredObject shape.
    Optionally filter by pillar code (data | configuration | processes | etc.)
    """
    try:
        rid = PydanticObjectId(run_id)
    except Exception:
        raise HTTPException(400, "Invalid run_id")

    objs = await DiscoveredObject.find(DiscoveredObject.run_id == rid).to_list()

    result = [_obj_to_frontend(o) for o in objs]

    if pillar:
        result = [r for r in result if r["pillar"] == pillar]

    return result


@project_router.post("/discovered-objects/{obj_id}/reprobe")
async def reprobe_object(obj_id: str, _: User = Depends(get_current_user)):
    obj = await DiscoveredObject.get(obj_id)
    if not obj:
        raise HTTPException(404, "Object not found")
    obj.suggestion_confidence = min(1.0, obj.suggestion_confidence + 0.05)
    await obj.save()
    return _obj_to_frontend(obj)




# ── Scope-hints endpoint (last discovery run row counts) ──────────────────────

class ScopeHintsOut(BaseModel):
    """Row counts from last discovery run, keyed by TABLE_NAME (upper-case)."""
    is_mock: bool = True
    run_id: Optional[str] = None
    table_counts: Dict[str, Optional[int]] = {}


async def _live_canonical_counts(proj) -> Dict[str, Optional[int]]:
    """Live COUNT(*) for the project's canonical source tables.

    The discovery scan inventories ALL_TABLES scoped to APPS, which MISSES the
    EBS objects exposed to APPS as synonyms (MTL_UNITS_OF_MEASURE, HZ_PARTIES,
    ...). A plain ``SELECT COUNT(*) FROM <table>`` resolves those synonyms, so
    this confirms the canonical tables that the scan couldn't see. Returns
    ``{}`` when there's no reachable (non-mock) EBS connection.
    """
    import re as _re3
    from app.fusion_modules import MODULES
    from app.models.v10 import SourceConnection

    src = (getattr(proj, "source_system", None) or "oracle_ebs")
    selected = set(getattr(proj, "selected_modules", None) or [])

    # Derive the canonical source tables in scope (mirrors the frontend card).
    tables: list[str] = []
    seen: set[str] = set()
    for mod in MODULES:
        if selected and getattr(mod, "code", None) not in selected:
            continue
        for obj in getattr(mod, "objects", ()):  # noqa: B007
            hint = (getattr(obj, "source_extracts", {}) or {}).get(src)
            if not hint:
                continue
            table, _where = _parse_extract_hint(hint)
            if table and table not in seen and _re3.match(r"^[A-Z0-9_$#]+$", table):
                seen.add(table)
                tables.append(table)
    if not tables:
        return {}

    # Prefer the project's own EBS connection; fall back to any healthy one.
    conn = await SourceConnection.find_one(
        SourceConnection.project_id == proj.id,
        SourceConnection.system_type == "oracle_ebs",
    )
    if conn is None:
        conn = await SourceConnection.find_one(
            SourceConnection.system_type == "oracle_ebs",
            SourceConnection.last_test_ok == True,  # noqa: E712
        ) or await SourceConnection.find_one(
            SourceConnection.system_type == "oracle_ebs"
        )
    if conn is None or conn.encrypted_password == "__mock__" or not conn.encrypted_password:
        return {}

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _run() -> Dict[str, Optional[int]]:
        import jaydebeapi
        password = conn.encrypted_password or ""
        if password.startswith("PLAIN:"):
            password = password[6:]
        jdbc_url = _jdbc_url_from_conn(conn)
        db = jaydebeapi.connect(
            "oracle.jdbc.OracleDriver", jdbc_url,
            [conn.username, password], "/app/ojdbc11.jar",
        )
        cur = db.cursor()
        counts: Dict[str, Optional[int]] = {}
        for t in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                row = cur.fetchone()
                counts[t] = int(row[0]) if row else 0
            except Exception:
                pass  # table genuinely missing / not selectable → leave out
        cur.close()
        db.close()
        return counts

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return await loop.run_in_executor(pool, _run)


@project_router.get("/projects/{project_id}/discovery/scope-hints", response_model=ScopeHintsOut)
async def discovery_scope_hints(
    project_id: str,
    _: User = Depends(get_current_user),
):
    """Return {TABLE_NAME: row_count} confirming the project's canonical sources.

    Starts from the latest completed discovery run's stored counts, then
    augments with a LIVE COUNT(*) over the canonical tables so synonym-exposed
    EBS objects (which the scan's ALL_TABLES query can't see) are confirmed too.
    """
    try:
        pid = PydanticObjectId(project_id)
    except Exception:
        raise HTTPException(400, "Invalid project_id")

    from app.models.project import Project
    proj = await Project.get(pid)

    table_counts: Dict[str, Optional[int]] = {}
    run_id: Optional[str] = None
    is_mock = False

    run = await DiscoveryRun.find(
        DiscoveryRun.project_id == pid,
        DiscoveryRun.status == "completed",
    ).sort("-created_at").first_or_none()
    if run:
        run_id = str(run.id)
        conn0 = await SourceConnection.get(run.connection_id)
        is_mock = (conn0 is None or conn0.encrypted_password == "__mock__"
                   or not conn0.encrypted_password)
        objects = await DiscoveredObject.find(DiscoveredObject.run_id == run.id).to_list()
        table_counts = {o.object_name.upper(): o.row_count for o in objects}

    # Live-verify the canonical tables (resolves APPS synonyms the scan missed).
    if proj is not None:
        try:
            live = await _live_canonical_counts(proj)
            if live:
                table_counts.update(live)
                is_mock = False
                run_id = run_id or "live"
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(f"scope-hints live count failed: {exc}")

    return ScopeHintsOut(is_mock=is_mock, run_id=run_id, table_counts=table_counts)


# ── Quick live COUNT(*) endpoint (Setup Wizard scope step) ────────────────────

import re as _re


def _parse_extract_hint(hint: str) -> tuple:
    """
    Parse a source_extracts string into (table_name, where_clause_or_None).

    "Extract from MTL_UNITS_OF_MEASURE"              -> ("MTL_UNITS_OF_MEASURE", None)
    "Extract from HZ_PARTIES (party_type=Customer)"  -> ("HZ_PARTIES", "PARTY_TYPE='CUSTOMER'")
    "Extract from BOM_BILL_OF_MATERIALS / BOM_COMP"  -> ("BOM_BILL_OF_MATERIALS", None)
    """
    text = _re.sub(r"(?i)^extract\s+from\s+", "", hint.strip())
    text = text.split(" / ")[0].strip()
    m = _re.match(r"^([A-Z0-9_]+)\s*\(([^)]+)\)\s*$", text, _re.IGNORECASE)
    if m:
        table = m.group(1).upper()
        cm = _re.match(r"(\w+)\s*=\s*(\w+)", m.group(2).strip())
        if cm:
            col = cm.group(1).upper()
            val = cm.group(2).upper()
            val_map = {"CUSTOMER": "CUSTOMER", "SUPPLIER": "VENDOR", "OPEN": "OPEN"}
            return table, f"{col}='{val_map.get(val, val)}'"
        return table, None
    m2 = _re.match(r"^([A-Z0-9_]+)", text, _re.IGNORECASE)
    return (m2.group(1).upper() if m2 else text.upper()), None


class QuickTableCountRequest(BaseModel):
    host: str
    port: int = 1521
    service_name: str
    username: str
    password: str
    source_extracts: List[str]


class QuickTableCountResult(BaseModel):
    counts: Dict[str, Optional[int]]
    errors: Dict[str, str] = {}


@router.post("/discovery/quick-table-counts", response_model=QuickTableCountResult)
async def quick_table_counts(body: QuickTableCountRequest):
    """
    Connect to Oracle EBS via JDBC and COUNT(*) each table derived from the
    supplied source_extract hint strings. Called by the Setup Wizard scope
    step to replace 'pending scan' with real EBS record volumes before the
    project is created.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _run() -> tuple:
        import jaydebeapi
        jdbc = (f"jdbc:oracle:thin:@{body.host}:{body.port}"
                f"/{body.service_name}")
        db = jaydebeapi.connect(
            "oracle.jdbc.OracleDriver",
            jdbc,
            [body.username, body.password],
            "/app/ojdbc11.jar",
        )
        counts: Dict[str, Optional[int]] = {}
        errors: Dict[str, str] = {}
        cur = db.cursor()
        for hint in body.source_extracts:
            table, where = _parse_extract_hint(hint)
            sql = f"SELECT COUNT(*) FROM {table}"
            if where:
                sql += f" WHERE {where}"
            try:
                cur.execute(sql)
                row = cur.fetchone()
                counts[table] = int(row[0]) if row else None
            except Exception as exc:
                counts[table] = None
                errors[table] = str(exc)[:120]
        cur.close()
        db.close()
        return counts, errors

    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            counts, errors = await loop.run_in_executor(pool, _run)
        return QuickTableCountResult(counts=counts, errors=errors)
    except Exception as exc:
        raise HTTPException(500, f"EBS connection failed: {exc}")
