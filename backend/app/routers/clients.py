"""Clients (tenants) — CRUD + per-client knowledge counts.

Powers the client selector and the "grouped by client" views. Learnings, gold and
templates already carry client_id / is_global (model fields), so those list
endpoints return the scope inline; this router adds the client roster + the counts
used to render the groups.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.user import User
from app.models.client import Client
from app.models.project import Project
from app.models.learned import LearnedMapping
from app.models.fbdi import FBDITemplate, GoldStandard
from app.services.auth_service import get_current_user
from app.services.client_service import ensure_default_client

router = APIRouter(prefix="/api/clients", tags=["clients"])


class ClientIn(BaseModel):
    name: str
    code: str | None = None
    description: str | None = None


class ClientPatch(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    active: bool | None = None
    is_default: bool | None = None


async def _safe_count(query) -> int:
    """A count that never raises — a single failing sub-query must not 500 the
    whole clients page (and get masked as a CORS error in the browser)."""
    try:
        return await query.count()
    except Exception:  # noqa: BLE001
        return 0


async def _counts(client_id) -> dict:
    return {
        "learnings": await _safe_count(LearnedMapping.find(LearnedMapping.client_id == client_id)),
        "gold": await _safe_count(GoldStandard.find(GoldStandard.client_id == client_id)),
        "projects": await _safe_count(Project.find(Project.client_id == client_id)),
        "templates": await _safe_count(FBDITemplate.find(FBDITemplate.client_id == client_id)),
    }


@router.get("")
async def list_clients(_: User = Depends(get_current_user)):
    # Whole body guarded: a runtime error here previously surfaced in the browser as
    # an opaque CORS failure (Starlette serves 500s outside the CORS middleware).
    try:
        await ensure_default_client()
        clients = await Client.find_all().to_list()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Clients are still initialising — retry shortly ({exc}).")
    out = []
    for c in clients:
        out.append({
            "id": str(c.id), "name": c.name, "code": c.code,
            "description": c.description, "is_default": c.is_default,
            "active": c.active, "counts": await _counts(c.id),
        })
    # Global (client-agnostic) knowledge counts — shown as its own group.
    glob = {
        "learnings": await _safe_count(LearnedMapping.find(LearnedMapping.is_global == True)),  # noqa: E712
        "templates": await _safe_count(FBDITemplate.find(FBDITemplate.is_global == True)),  # noqa: E712
    }
    return {"clients": out, "global": glob}


@router.post("")
async def create_client(body: ClientIn, _: User = Depends(get_current_user)):
    if not body.name.strip():
        raise HTTPException(400, "Client name is required")
    existing = await Client.find_one(Client.name == body.name.strip())
    if existing:
        raise HTTPException(409, f"A client named '{body.name}' already exists")
    c = Client(name=body.name.strip(), code=(body.code or None), description=body.description)
    await c.insert()
    return {"id": str(c.id), "name": c.name, "code": c.code,
            "description": c.description, "is_default": c.is_default, "active": c.active}


@router.patch("/{client_id}")
async def update_client(client_id: str, body: ClientPatch, _: User = Depends(get_current_user)):
    from beanie import PydanticObjectId
    try:
        oid = PydanticObjectId(client_id)
    except Exception:
        raise HTTPException(400, "Invalid client id")
    c = await Client.get(oid)
    if not c:
        raise HTTPException(404, "Client not found")
    data = body.model_dump(exclude_unset=True)
    # Only one default: if this client becomes default, clear the flag elsewhere.
    if data.get("is_default"):
        for other in await Client.find(Client.is_default == True).to_list():  # noqa: E712
            if other.id != c.id:
                other.is_default = False
                await other.save()
    for k, v in data.items():
        setattr(c, k, v)
    from datetime import datetime
    c.updated_at = datetime.utcnow()
    await c.save()
    return {"id": str(c.id), "name": c.name, "code": c.code,
            "description": c.description, "is_default": c.is_default, "active": c.active}
