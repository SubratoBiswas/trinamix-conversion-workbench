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
    is_ebs = getattr(c, "source_type", "dataset") == "ebs"
    if not c.template_id or (not is_ebs and not c.dataset_id):
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


@router.get("/conversions/{conversion_id}/source-columns")
async def source_columns(conversion_id: str, _: User = Depends(get_current_user)):
    """Unified source-column list for the Mapping Review canvas.

    Works for both source modes:
      * dataset mode (``dataset_id`` set) → profiled columns from the upload
      * EBS live mode (``dataset_id`` is null) → live ``ALL_TAB_COLUMNS``
        metadata for the conversion's ``ebs_table_hint``.

    Returns column dicts shaped like the frontend ``DatasetColumnProfile`` so
    the canvas can render identically regardless of source.
    """
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")

    # UI rule (mirrors ConversionDetailPage): dataset_id presence — not
    # source_type — decides which source the canvas shows.
    is_ebs = not conv.dataset_id
    columns: list[dict[str, Any]] = []

    if is_ebs:
        from app.services.mapping_service import _source_columns_for_ebs
        table = getattr(conv, "ebs_table_hint", "") or ""
        srcs = await _source_columns_for_ebs(table) if table else []
        for i, s in enumerate(srcs):
            columns.append({
                "id": i + 1,
                "column_name": s.name,
                "position": i,
                "inferred_type": s.inferred_type,
                "null_count": 0,
                "null_percent": s.null_percent,
                "distinct_count": s.distinct_count,
                "sample_values": s.sample_values,
                "min_value": None,
                "max_value": None,
                "pattern_summary": s.pattern_summary,
                "contains_pii": None,
                "pii_category": None,
            })
    else:
        from app.models.dataset import DatasetColumnProfile
        profs = await DatasetColumnProfile.find(
            DatasetColumnProfile.dataset_id == conv.dataset_id
        ).sort(+DatasetColumnProfile.position).to_list()
        for p in profs:
            columns.append({
                "id": int(str(p.id)[-8:], 16) if not isinstance(p.id, int) else p.id,
                "column_name": p.column_name,
                "position": p.position,
                "inferred_type": p.inferred_type,
                "null_count": getattr(p, "null_count", 0) or 0,
                "null_percent": p.null_percent or 0.0,
                "distinct_count": p.distinct_count or 0,
                "sample_values": p.sample_values or [],
                "min_value": getattr(p, "min_value", None),
                "max_value": getattr(p, "max_value", None),
                "pattern_summary": p.pattern_summary,
                "contains_pii": getattr(p, "contains_pii", None),
                "pii_category": getattr(p, "pii_category", None),
            })

    return {
        "source_type": "ebs" if is_ebs else "dataset",
        "table": getattr(conv, "ebs_table_hint", None) if is_ebs else None,
        "columns": columns,
    }


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
    return {"id": str(r.id), "conversion_id": str(r.conversion_id), **{k: v for k, v in r.model_dump().items() if k not in ("id", "conversion_id")}}


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
        ctx = {"row_index": int(idx) + 1, "current_user": user.email, "now": datetime.utcnow(), "crosswalks": crosswalks}
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
    return [{"id": str(r.id), "conversion_id": str(r.conversion_id), **{k: v for k, v in r.model_dump().items() if k not in ("id", "conversion_id")}} for r in rules]


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
            candidates.append({
                "conversion_id": str(sib.id),
                "conversion_name": sib.name,
                "target_object": sib.target_object,
                "fk_fields": matching,
            })
    return {
        "source_conversion": conversion_id,
        "master_object": master_obj,
        "key_fields": key_names,
        "candidates": candidates,
    }


@router.get("/conversions/{conversion_id}/inherited-standards")
async def inherited_standards(conversion_id: str, _: User = Depends(get_current_user)):
    return []
