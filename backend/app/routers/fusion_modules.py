"""Read-only endpoint serving the Fusion Cloud module catalog.

Drives the Setup Wizard's "Implementation Scope" step. Each module
declares the canonical Fusion target objects an implementation team
typically migrates; the wizard surfaces them so the customer doesn't
have to invent their conversion list from scratch.
"""
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.fusion_modules import MODULES
from app.models.user import User
from app.services.auth_service import get_current_user


router = APIRouter(prefix="/api", tags=["fusion-modules"])


class FusionObjectOut(BaseModel):
    target_object: str
    label: str
    fbdi_template: str | None = None
    planned_load_order: int
    source_extracts: dict[str, str] = {}


class FusionModuleOut(BaseModel):
    code: str
    name: str
    family: str
    description: str
    objects: list[FusionObjectOut]


@router.get("/fusion-modules", response_model=list[FusionModuleOut])
async def list_fusion_modules(_: User = Depends(get_current_user)):
    return [
        FusionModuleOut(
            code=m.code, name=m.name, family=m.family,
            description=m.description,
            objects=[
                FusionObjectOut(
                    target_object=o.target_object,
                    label=o.label,
                    fbdi_template=o.fbdi_template,
                    planned_load_order=o.planned_load_order,
                    source_extracts=o.source_extracts,
                )
                for o in m.objects
            ],
        )
        for m in MODULES
    ]
