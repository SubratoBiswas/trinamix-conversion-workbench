"""Oracle Fusion Cloud target — connection, test, FBDI targets, and load."""
from datetime import datetime
from typing import Any, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.conversion import Conversion
from app.models.user import User
from app.models.v10 import SourceConnection
from app.services.auth_service import get_current_user
from app.services.fusion_service import (
    get_load_status, interface_tables_for, load_meta_for, load_to_fusion,
    preflight_fusion, test_fusion_connection, work_area_for,
)

router = APIRouter(prefix="/api/fusion", tags=["fusion"])
conv_router = APIRouter(prefix="/api/conversions", tags=["fusion"])


# ── schemas ──────────────────────────────────────────────────────────────────
class FusionConnIn(BaseModel):
    base_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None       # omit to keep the stored one
    name: Optional[str] = "Oracle Fusion Cloud"


class FusionConnOut(BaseModel):
    id: Optional[str] = None
    base_url: Optional[str] = None
    username: Optional[str] = None
    has_credentials: bool = False
    last_test_ok: Optional[bool] = None
    last_tested_at: Optional[datetime] = None
    last_test_error: Optional[str] = None


async def _get_conn() -> Optional[SourceConnection]:
    return await SourceConnection.find_one(SourceConnection.system_type == "oracle_fusion")


def _plain(conn: Optional[SourceConnection]) -> Optional[str]:
    if not conn or not conn.encrypted_password:
        return None
    pw = conn.encrypted_password
    return pw[6:] if pw.startswith("PLAIN:") else pw


def _out(conn: Optional[SourceConnection]) -> FusionConnOut:
    if not conn:
        return FusionConnOut()
    return FusionConnOut(
        id=str(conn.id), base_url=conn.base_url, username=conn.username,
        has_credentials=bool(conn.encrypted_password),
        last_test_ok=conn.last_test_ok, last_tested_at=conn.last_tested_at,
        last_test_error=conn.last_test_error,
    )


# ── connection management ────────────────────────────────────────────────────
@router.get("/connection", response_model=FusionConnOut)
async def get_connection(_: User = Depends(get_current_user)):
    return _out(await _get_conn())


@router.post("/connection", response_model=FusionConnOut)
async def save_connection(body: FusionConnIn, _: User = Depends(get_current_user)):
    if not body.base_url or not body.username:
        raise HTTPException(400, "Fusion URL and username are required.")
    conn = await _get_conn()
    if conn is None:
        conn = SourceConnection(
            system_type="oracle_fusion", name=body.name or "Oracle Fusion Cloud",
            base_url=body.base_url, username=body.username, auth_type="basic",
            encrypted_password=("PLAIN:" + body.password) if body.password else None,
        )
        await conn.insert()
    else:
        upd: dict[str, Any] = {
            "base_url": body.base_url, "username": body.username,
            "updated_at": datetime.utcnow(),
        }
        if body.password:
            upd["encrypted_password"] = "PLAIN:" + body.password
        await conn.set(upd)
    return _out(await _get_conn())


@router.post("/connection/test")
async def test_connection(body: Optional[FusionConnIn] = None, _: User = Depends(get_current_user)):
    conn = await _get_conn()
    base_url = (body.base_url if body and body.base_url else None) or (conn.base_url if conn else None)
    username = (body.username if body and body.username else None) or (conn.username if conn else None)
    password = (body.password if body and body.password else None) or _plain(conn)
    if not (base_url and username and password):
        raise HTTPException(400, "Provide Fusion URL, username and password (or save them first).")
    res = await test_fusion_connection(base_url, username, password)
    if conn:
        await conn.set({
            "last_test_ok": res["ok"], "last_tested_at": datetime.utcnow(),
            "last_test_error": None if res["ok"] else res["message"],
        })
    return res


# ── per-conversion target tables + load ──────────────────────────────────────
async def _business_object(conv: Conversion) -> Optional[str]:
    from app.models.fbdi import FBDITemplate
    tpl = await FBDITemplate.get(conv.template_id) if conv.template_id else None
    return (tpl.business_object if tpl else None) or conv.target_object


@conv_router.get("/{conversion_id}/fusion-targets")
async def fusion_targets(conversion_id: str, _: User = Depends(get_current_user)):
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    bo = await _business_object(conv)
    conn = await _get_conn()
    return {
        "business_object": bo,
        "interface_tables": interface_tables_for(bo),
        "loadable": bool(load_meta_for(bo)),
        "work_area": work_area_for(bo),
        "pod_url": (conn.base_url if conn else None),
    }


@router.get("/load-runs/{run_id}/status")
async def fusion_load_status(run_id: str, _: User = Depends(get_current_user)):
    """Poll Oracle for the live status of a previously-submitted Fusion load."""
    from app.models.load import LoadRun
    run = await LoadRun.get(PydanticObjectId(run_id))
    if not run:
        raise HTTPException(404, "Load run not found")
    if not run.fusion_request_id:
        return {"ok": False, "state": "unknown", "request_id": None,
                "message": "This run has no Fusion request id — it was a simulate run or predates status tracking."}
    conn = await _get_conn()
    if conn is None or not conn.base_url or not conn.encrypted_password:
        raise HTTPException(400, "Configure the Oracle Fusion connection first.")
    res = await get_load_status(conn, run.fusion_request_id)
    upd: dict[str, Any] = {"fusion_state": res.get("state")}
    if res.get("state") == "error":
        upd["status"] = "failed"
    elif res.get("state") in ("succeeded", "warning"):
        upd["status"] = "completed"
    await run.set(upd)
    return {**res, "request_id": run.fusion_request_id}


@conv_router.get("/{conversion_id}/fusion-preflight")
async def fusion_preflight(conversion_id: str, _: User = Depends(get_current_user)):
    """Check whether this pod/user can run an import for the conversion's object."""
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    conn = await _get_conn()
    if conn is None or not conn.base_url or not conn.encrypted_password:
        raise HTTPException(400, "Configure the Oracle Fusion connection first.")
    bo = await _business_object(conv)
    res = await preflight_fusion(conn, bo)
    return {**res, "business_object": bo}


@conv_router.post("/{conversion_id}/load-to-fusion")
async def load_to_fusion_endpoint(conversion_id: str, _: User = Depends(get_current_user)):
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    if not conv.template_id:
        raise HTTPException(400, "Conversion has no FBDI template bound.")

    conn = await _get_conn()
    if conn is None or not conn.base_url or not conn.encrypted_password:
        raise HTTPException(400, "Configure the Oracle Fusion connection first (URL, username, password).")

    # Build the FBDI artifact in memory (same pipeline as Generate Output).
    from app.services.output_service import build_converted_dataframe
    from app.domain.frames import (
        format_date_columns as _format_date_columns,
        normalize_columns as _normalize_columns,
    )
    from app.models.fbdi import FBDIField, FBDITemplate
    df, _lineage = await build_converted_dataframe(conv)
    tpl = await FBDITemplate.get(conv.template_id)
    fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list() if tpl else []
    df = _normalize_columns(df)
    df = _format_date_columns(df, fields)
    csv_bytes = df.to_csv(index=False).encode("utf-8")

    bo = (tpl.business_object if tpl else None) or conv.target_object
    filename = ((bo or "fbdi").replace(" ", "") + ".csv")

    res = await load_to_fusion(conn, bo, csv_bytes, filename)

    # Record a load run + flip the conversion to 'loaded' on success.
    from app.models.load import LoadRun
    n = int(len(df))
    run = LoadRun(
        conversion_id=conv.id, run_type="fusion",
        status="completed" if res["ok"] else "failed",
        total_records=n,
        passed_count=n if res["ok"] else 0,
        failed_count=0 if res["ok"] else n,
        warning_count=0, error_count=0 if res["ok"] else 1,
        started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
        fusion_request_id=(res.get("request_id") or None),
        # submitted but not yet imported — Check status will poll the real phase
        fusion_state=("running" if res["ok"] else "error"),
        business_object=bo,
        fusion_tables=interface_tables_for(bo),
        fusion_work_area=work_area_for(bo),
        fusion_response=(str(res.get("response"))[:2000] if res.get("response") is not None else None),
    )
    await run.insert()
    if res["ok"]:
        await conv.set({"status": "loaded", "updated_at": datetime.utcnow()})

    return {**res, "rows": n, "load_run_id": str(run.id)}
