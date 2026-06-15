"""Mapping suggestion endpoints."""
from datetime import datetime
from typing import Any, Optional
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.mapping import MappingSuggestion
from app.models.transformation import Crosswalk, TransformationRule
from app.models.user import User
from app.parsers import parse_tabular
from app.schemas.mapping import MappingOut, MappingUpdate
from app.schemas.transformation import TransformationRuleCreate, TransformationRuleOut
from app.services.auth_service import get_current_user
from app.services.learning_service import record_learning_from_mapping, record_learning_from_rule
from app.services.mapping_service import enrich_mapping_with_samples, run_mapping_suggestions
from app.transformations.engine import apply_pipeline

router = APIRouter(prefix="/api", tags=["mapping"])


async def _require_conversion(conversion_id: str) -> Conversion:
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    if not c.dataset_id or not c.template_id:
        raise HTTPException(400, "Conversion needs both dataset and template bound first.")
    return c


@router.post("/conversions/{conversion_id}/suggest-mapping", response_model=list[MappingOut])
async def suggest_mapping(conversion_id: str, _: User = Depends(get_current_user)):
    conv = await _require_conversion(conversion_id)
    saved = await run_mapping_suggestions(conv)
    return await enrich_mapping_with_samples(conv, saved)


@router.get("/conversions/{conversion_id}/mappings", response_model=list[MappingOut])
async def list_mappings(conversion_id: str, _: User = Depends(get_current_user)):
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    items = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == PydanticObjectId(conversion_id)
    ).to_list()
    if not items:
        return []
    return await enrich_mapping_with_samples(conv, items)


@router.put("/mappings/{mapping_id}", response_model=MappingOut)
async def update_mapping(
    mapping_id: str, payload: MappingUpdate, user: User = Depends(get_current_user)
):
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("status") == "approved":
        data["approved_by"] = user.email
        data["approved_at"] = datetime.utcnow()
    await m.set(data)
    conv = await Conversion.get(m.conversion_id)
    if m.status in ("approved", "overridden") and m.source_column:
        await record_learning_from_mapping(m, conv, captured_by=user.email)
    return (await enrich_mapping_with_samples(conv, [m]))[0]


@router.put("/mappings/{mapping_id}/approve", response_model=MappingOut)
async def approve_mapping(mapping_id: str, user: User = Depends(get_current_user)):
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    await m.set({"status": "approved", "approved_by": user.email, "approved_at": datetime.utcnow()})
    conv = await Conversion.get(m.conversion_id)
    if m.source_column:
        await record_learning_from_mapping(m, conv, captured_by=user.email)
        from app.services.learning_service import propagate_rules_to_downstream
        await propagate_rules_to_downstream(conv, m)
    return (await enrich_mapping_with_samples(conv, [m]))[0]


@router.post("/conversions/{conversion_id}/rules", response_model=TransformationRuleOut)
async def add_rule(
    conversion_id: str, payload: TransformationRuleCreate, user: User = Depends(get_current_user)
):
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    seq = await TransformationRule.find(
        TransformationRule.conversion_id == PydanticObjectId(conversion_id)
    ).count()
    data = payload.model_dump()
    if data.get("target_field_id"):
        data["target_field_id"] = PydanticObjectId(data["target_field_id"])
    r = TransformationRule(conversion_id=conv.id, sequence=seq, **data)
    await r.insert()
    await record_learning_from_rule(r, conv, captured_by=user.email)
    return {"id": str(r.id), "conversion_id": str(r.conversion_id), **{k: v for k, v in r.model_dump().items() if k not in ("id","conversion_id")}}


class PreviewRule(BaseModel):
    rule_type: str
    config: dict[str, Any] = {}

class PreviewRequest(BaseModel):
    rules: list[PreviewRule]
    source_column: Optional[str] = None
    sample_size: int = 5

class PreviewSample(BaseModel):
    source: Any
    output: Any
    error: Optional[str] = None

class PreviewResponse(BaseModel):
    samples: list[PreviewSample]


@router.post("/conversions/{conversion_id}/rules/preview", response_model=PreviewResponse)
async def preview_rules(
    conversion_id: str, payload: PreviewRequest, user: User = Depends(get_current_user)
):
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv or not conv.dataset_id:
        raise HTTPException(404, "Conversion or dataset not found")
    ds = await Dataset.get(conv.dataset_id)
    if not ds:
        raise HTTPException(404, "Dataset not found")
    df = parse_tabular(ds.file_path, file_type=ds.file_type)
    cws = await Crosswalk.find(
        Crosswalk.conversion_id == PydanticObjectId(conversion_id)
    ).to_list()
    crosswalks: dict[str, dict[str, str]] = {}
    for cw in cws:
        crosswalks.setdefault(cw.name, {})[cw.source_value] = cw.target_value
    rules = [{"rule_type": r.rule_type, "config": r.config} for r in payload.rules]
    out: list[PreviewSample] = []
    n = max(1, min(int(payload.sample_size), 20))
    for idx, row in df.head(n).iterrows():
        row_dict = {k: ("" if v is None else v) for k, v in row.to_dict().items()}
        src_value = row_dict.get(payload.source_column) if payload.source_column else None
        ctx = {"row_index": int(idx)+1, "current_user": user.email, "now": datetime.utcnow(), "crosswalks": crosswalks}
        try:
            transformed = apply_pipeline(rules, src_value, row=row_dict, ctx=ctx)
            out.append(PreviewSample(source=src_value, output=transformed))
        except Exception as exc:
            out.append(PreviewSample(source=src_value, output=None, error=str(exc)))
    return PreviewResponse(samples=out)


@router.get("/conversions/{conversion_id}/rules", response_model=list[TransformationRuleOut])
async def list_rules(conversion_id: str, _: User = Depends(get_current_user)):
    rules = await TransformationRule.find(
        TransformationRule.conversion_id == PydanticObjectId(conversion_id)
    ).sort("sequence").to_list()
    return [{"id": str(r.id), "conversion_id": str(r.conversion_id), **{k: v for k, v in r.model_dump().items() if k not in ("id","conversion_id")}} for r in rules]


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, _: User = Depends(get_current_user)):
    r = await TransformationRule.get(PydanticObjectId(rule_id))
    if not r:
        raise HTTPException(404, "Rule not found")
    await r.delete()
    return {"deleted": rule_id}


@router.post("/mappings/{mapping_id}/propagate")
async def propagate_mapping_rule(mapping_id: str, user: User = Depends(get_current_user)):
    from app.services.learning_service import propagate_rules_to_downstream
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    conv = await Conversion.get(m.conversion_id)
    if not conv:
        raise HTTPException(404, "Conversion not found")
    propagated = await propagate_rules_to_downstream(conv, m)
    return {"mapping_id": mapping_id, "propagated": propagated, "count": len(propagated)}


@router.get("/conversions/{conversion_id}/propagation-candidates")
async def propagation_candidates(conversion_id: str, _: User = Depends(get_current_user)):
    from app.services.learning_service import REFERENCE_KEY_FIELDS
    from app.models.fbdi import FBDIField, FBDITemplate
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    tpl = await FBDITemplate.get(conv.template_id) if conv.template_id else None
    master_obj = (tpl.business_object if tpl else None) or conv.target_object
    key_names = REFERENCE_KEY_FIELDS.get(master_obj or "", [])
    if not key_names:
        return {"source_conversion": conversion_id, "candidates": []}
    siblings = await Conversion.find(
        Conversion.project_id == conv.project_id,
        Conversion.id != conv.id,
    ).to_list()
    candidates = []
    for sib in siblings:
        if not sib.template_id:
            continue
        sib_fields = await FBDIField.find(FBDIField.template_id == sib.template_id).to_list()
        matching = [f.field_name for f in sib_fields if f.field_name in key_names]
        if matching:
            candidates.append({"conversion_id": str(sib.id), "conversion_name": sib.name,
                                "target_object": sib.target_object, "fk_fields": matching})
    return {"source_conversion": co