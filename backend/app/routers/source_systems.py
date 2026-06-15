"""Read-only enum endpoint backing the source-system picker in the UI.

The frontend pulls this once at app load to populate the dropdown in the
Setup Wizard, project create form, and dataset upload form. Keeping it
server-driven avoids a redundant constant on the client that drifts.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.models.user import User
from app.services.auth_service import get_current_user
from app.source_systems import SOURCE_SYSTEMS


router = APIRouter(prefix="/api", tags=["source-systems"])


class SourceSystemOut(BaseModel):
    code: str
    display_name: str
    family: str
    has_scanner_v1: bool


@router.get("/source-systems", response_model=list[SourceSystemOut])
async def list_source_systems(_: User = Depends(get_current_user)):
    return [
        SourceSystemOut(
            code=s.code,
            display_name=s.display_name,
            family=s.family,
            has_scanner_v1=s.has_scanner_v1,
        )
        for s in SOURCE_SYSTEMS
    ]
