"""Chart of Accounts (COA) router (v10)."""
from datetime import datetime
from typing import List, Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.models.v10 import CoaSegment, CoaStructure, CoaValueCrosswalk

router = APIRouter(prefix="/api/coa", tags=["coa"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class StructureCreate(BaseModel):
    project_id: str
    name: str
    description: Optional[str] = None
    legacy_system: Optional[str] = None


class StructureOut(BaseModel):
    id: str
    project_id: str
    name: str
    description: Optional[str] = None
    legacy_system: Optional[str] = None
    segment_count: int
    total_values: int
    created_at: datetime


class SegmentCreate(BaseModel):
    structure_id: str
    segment_name: str
    segment_label: Optional[str] = None
    segment_order: int = 0
    value_set_name: Optional[str] = None
    description: Optional[str] = None


class SegmentOut(BaseModel):
    id: str
    structure_id: str
    project_id: str
    segment_name: str
    segment_label: Optional[str] = None
    segment_order: int
    value_set_name: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime


class CrosswalkCreate(BaseModel):
    segment_id: str
    legacy_value: str
    legacy_description: Optional[str] = None
    fusion_value: Optional[str] = None
    fusion_description: Optional[str] = None


class CrosswalkUpdate(BaseModel):
    fusion_value: Optional[str] = None
    fusion_description: Optional[str] = None
    status: Optional[str] = None
    mapped_by: Optional[str] = None


class CrosswalkOut(BaseModel):
    id: str
    segment_id: str
    structure_id: str
    project_id: str
    legacy_value: str
    legacy_description: Optional[str] = None
    fusion_value: Optional[str] = None
    fusion_description: Optional[str] = None
    status: str
    mapped_by: Optional[str] = None
    mapped_at: Optional[datetime] = None
    created_at: datetime


# ── COA Structures ─────────────────────────────────────────────────────────────

@router.post("/structures", response_model=StructureOut, status_code=201)
async def create_structure(body: StructureCreate):
    s = CoaStructure(
        project_id=PydanticObjectId(body.project_id),
        name=body.name,
        description=body.description,
        legacy_system=body.legacy_system,
    )
    await s.insert()
    return _s_out(s)


@router.get("/structures", response_model=List[StructureOut])
async def list_structures(project_id: str = Query(...)):
    items = await CoaStructure.find(
        CoaStructure.project_id == PydanticObjectId(project_id)
    ).to_list()
    return [_s_out(i) for i in items]


@router.get("/structures/{sid}", response_model=StructureOut)
async def get_structure(sid: str):
    s = await CoaStructure.get(PydanticObjectId(sid))
    if not s:
        raise HTTPException(404, "Structure not found")
    return _s_out(s)


# ── Segments ───────────────────────────────────────────────────────────────────

@router.post("/segments", response_model=SegmentOut, status_code=201)
async def create_segment(body: SegmentCreate):
    struct = await CoaStructure.get(PydanticObjectId(body.structure_id))
    if not struct:
        raise HTTPException(404, "Structure not found")
    seg = CoaSegment(
        structure_id=struct.id,
        project_id=struct.project_id,
        segment_name=body.segment_name,
        segment_label=body.segment_label,
        segment_order=body.segment_order,
        value_set_name=body.value_set_name,
        description=body.description,
    )
    await seg.insert()
    # Update segment count
    struct.segment_count = await CoaSegment.find(
        CoaSegment.structure_id == struct.id
    ).count()
    await struct.save()
    return _seg_out(seg)


@router.get("/structures/{sid}/segments", response_model=List[SegmentOut])
async def list_segments(sid: str):
    segs = await CoaSegment.find(
        CoaSegment.structure_id == PydanticObjectId(sid)
    ).sort("segment_order").to_list()
    return [_seg_out(s) for s in segs]


# ── Crosswalks ─────────────────────────────────────────────────────────────────

@router.post("/crosswalks", response_model=CrosswalkOut, status_code=201)
async def create_crosswalk(body: CrosswalkCreate):
    seg = await CoaSegment.get(PydanticObjectId(body.segment_id))
    if not seg:
        raise HTTPException(404, "Segment not found")
    cw = CoaValueCrosswalk(
        segment_id=seg.id,
        structure_id=seg.structure_id,
        project_id=seg.project_id,
        legacy_value=body.legacy_value,
        legacy_description=body.legacy_description,
        fusion_value=body.fusion_value,
        fusion_description=body.fusion_description,
    )
    await cw.insert()
    return _cw_out(cw)


@router.get("/segments/{seg_id}/crosswalks", response_model=List[CrosswalkOut])
async def list_crosswalks(
    seg_id: str,
    status: Optional[str] = Query(None),
):
    q = CoaValueCrosswalk.find(
        CoaValueCrosswalk.segment_id == PydanticObjectId(seg_id)
    )
    if status:
        q = q.find(CoaValueCrosswalk.status == status)
    items = await q.to_list()
    return [_cw_out(i) for i in items]


@router.patch("/crosswalks/{cw_id}", response_model=CrosswalkOut)
async def update_crosswalk(cw_id: str, body: CrosswalkUpdate):
    cw = await CoaValueCrosswalk.get(PydanticObjectId(cw_id))
    if not cw:
        raise HTTPException(404, "Crosswalk not found")
    if body.fusion_value is not None:
        cw.fusion_value = body.fusion_value
    if body.fusion_description is not None:
        cw.fusion_description = body.fusion_description
    if body.status is not None:
        cw.status = body.status
    if body.mapped_by is not None:
        cw.mapped_by = body.mapped_by
        cw.mapped_at = datetime.utcnow()
    await cw.save()
    return _cw_out(cw)


@router.get("/structures/{sid}/stats")
async def coa_stats(sid: str):
    segs = await CoaSegment.find(
        CoaSegment.structure_id == PydanticObjectId(sid)
    ).to_list()
    seg_ids = [s.id for s in segs]
    total = 0
    mapped = 0
    for sid_ in seg_ids:
        cws = await CoaValueCrosswalk.find(
            CoaValueCrosswalk.segment_id == sid_
        ).to_list()
        total += len(cws)
        mapped += sum(1 for c in cws if c.status == "mapped")
    return {"total": total, "mapped": mapped, "pending": total - mapped}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _s_out(s: CoaStructure) -> StructureOut:
    return StructureOut(
        id=str(s.id),
        project_id=str(s.project_id),
        name=s.name,
        description=s.description,
        legacy_system=s.legacy_system,
        segment_count=s.segment_count,
        total_values=s.total_values,
        created_at=s.created_at,
    )


def _seg_out(s: CoaSegment) -> SegmentOut:
    return SegmentOut(
        id=str(s.id),
        structure_id=str(s.structure_id),
        project_id=str(s.project_id),
        segment_name=s.segment_name,
        segment_label=s.segment_label,
        segment_order=s.segment_order,
        value_set_name=s.value_set_name,
        description=s.description,
        created_at=s.created_at,
    )


def _cw_out(c: CoaValueCrosswalk) -> CrosswalkOut:
    return CrosswalkOut(
        id=str(c.id),
        segment_id=str(c.segment_id),
        structure_id=str(c.structure_id),
        project_id=str(c.project_id),
        legacy_value=c.legacy_value,
        legacy_description=c.legacy_description,
        fusion_value=c.fusion_value,
        fusion_description=c.fusion_description,
        status=c.status,
        mapped_by=c.mapped_by,
        mapped_at=c.mapped_at,
        created_at=c.created_at,
    )
