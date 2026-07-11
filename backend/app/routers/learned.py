"""Learning library endpoints - registry of human-approved mappings/rules."""
from collections import Counter
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.conversion import Conversion
from app.models.learned import LearnedMapping
from app.models.user import User
from app.schemas.learned import (
    LearnedMappingCreate, LearnedMappingOut, LearnedMappingUpdate, LearningStats,
)
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/learned-mappings", tags=["learning"])


DEFAULT_CATEGORIES = [
    "Column Mapping Alias",
    "SKU / Item Format Alias",
    "Customer Alias",
    "Supplier Alias",
    "UOM Conversion Rule",
    "Status Value Mapping",
    "Date Format Rule",
    "Currency Mapping",
    "Organization Code Mapping",
    "Branch Code Mapping",
]


def _serialize(item: LearnedMapping) -> dict:
    d = item.model_dump()
    d["id"] = str(item.id)
    d["project_id"] = str(item.project_id) if item.project_id else None
    d["originated_in_project_id"] = (
        str(item.originated_in_project_id) if item.originated_in_project_id else None
    )
    return d


@router.post("", response_model=LearnedMappingOut)
async def create_learned(
    payload: LearnedMappingCreate,
    user: User = Depends(get_current_user),
):
    item = LearnedMapping(**payload.model_dump(), captured_by=user.email)
    await item.insert()
    return _serialize(item)


@router.get("", response_model=list[LearnedMappingOut])
async def list_learned(
    kind: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    _: User = Depends(get_current_user),
):
    filters = []
    if kind:
        filters.append(LearnedMapping.kind == kind)
    if category:
        filters.append(LearnedMapping.category == category)
    if project_id:
        filters.append(LearnedMapping.project_id == PydanticObjectId(project_id))
    query = LearnedMapping.find(*filters)
    items = await query.sort("-captured_at").to_list()
    return [_serialize(item) for item in items]


@router.get("/stats", response_model=LearningStats)
async def learning_stats(
    project_id: Optional[str] = Query(None),
    _: User = Depends(get_current_user),
):
    if project_id:
        items = await LearnedMapping.find(
            LearnedMapping.project_id == PydanticObjectId(project_id)
        ).to_list()
    else:
        items = await LearnedMapping.find_all().to_list()
    total = len(items)
    avg_boost = round(
        sum(i.confidence_boost or 0 for i in items) / total, 3
    ) if total else 0.0
    records_fixed = sum(int(i.records_auto_fixed or 0) for i in items)
    minutes_saved = total * 4

    by_cat = Counter(i.category for i in items)
    cat_rows = []
    for c in DEFAULT_CATEGORIES:
        cat_rows.append({"category": c, "count": by_cat.get(c, 0)})
    for c in by_cat:
        if c not in DEFAULT_CATEGORIES:
            cat_rows.append({"category": c, "count": by_cat[c]})

    return {
        "total": total,
        "avg_confidence_boost": avg_boost,
        "records_auto_fixed": records_fixed,
        "analyst_minutes_saved": minutes_saved,
        "by_category": cat_rows,
    }


@router.get("/reference-standards")
async def reference_standards(_: User = Depends(get_current_user)):
    """Per-object summary of the gold-derived reference standards stored in the
    DB. Once gold has been applied for an object type (e.g. Supplier Import),
    a reusable standard is on file and auto-applied to every future conversion
    of that object — so the UI can show it without the user re-uploading gold.
    Grouped by target_object across the reusable (global) learned rules."""
    items = await LearnedMapping.find(
        {"kind": {"$in": ["column_mapping", "example_default", "suppress_field"]},
         "target_object": {"$ne": None}}
    ).to_list()
    by_obj: dict[str, dict] = {}
    for it in items:
        obj = it.target_object
        row = by_obj.setdefault(obj, {
            "business_object": obj, "column_mappings": 0, "defaults": 0,
            "suppressions": 0, "captured_from": it.captured_from,
            "captured_at": it.captured_at,
        })
        if it.kind == "column_mapping":
            row["column_mappings"] += 1
        elif it.kind == "example_default":
            row["defaults"] += 1
        elif it.kind == "suppress_field":
            row["suppressions"] += 1
        if it.captured_at and (row["captured_at"] is None or it.captured_at > row["captured_at"]):
            row["captured_at"] = it.captured_at
            row["captured_from"] = it.captured_from
    rows = sorted(by_obj.values(), key=lambda r: r["business_object"])
    return {"reference_standards": rows}


@router.get("/knowledge-bank/stats")
async def knowledge_bank_stats(_: User = Depends(get_current_user)):
    items = await LearnedMapping.find_all().to_list()
    by_erp: Counter = Counter()
    for item in items:
        erp = getattr(item, "source_erp", None) or getattr(item, "captured_from", None) or "unknown"
        by_erp[erp] += 1
    return [{"source_erp": erp, "count": cnt} for erp, cnt in by_erp.most_common()]


@router.get("/catalog-status")
async def catalog_status(_: User = Depends(get_current_user)):
    """Counts of the seeded metadata-catalog mappings (source→FBDI column rules),
    grouped by source system and target object — lets the UI confirm the Mapping
    Knowledge Base is populated."""
    items = await LearnedMapping.find(
        LearnedMapping.captured_from == "metadata catalog"
    ).to_list()
    by_src: Counter = Counter()
    by_obj: Counter = Counter()
    rows = []
    for it in items:
        by_src[it.source_erp or "unknown"] += 1
        by_obj[it.target_object or "unknown"] += 1
        rows.append({
            "source_system": it.source_erp, "target_object": it.target_object,
            "source_field": it.original_value, "fbdi_column": it.target_field,
            "fbdi_sheet": (it.rule_config or {}).get("fbdi_sheet"),
        })
    return {
        "total": len(items),
        "by_source_system": [{"source_system": k, "count": v} for k, v in by_src.most_common()],
        "by_target_object": [{"target_object": k, "count": v} for k, v in by_obj.most_common()],
        "rows": sorted(rows, key=lambda r: (r["source_system"] or "", r["target_object"] or "", r["fbdi_column"] or "")),
    }


@router.post("/reseed-catalog")
async def reseed_catalog(_: User = Depends(get_current_user)):
    """Re-run the metadata-catalog seed on demand (idempotent, additive)."""
    from app.services.catalog_seed_service import seed_mapping_catalog
    return await seed_mapping_catalog()


@router.post("/backfill-projects")
async def backfill_project_ids(_: User = Depends(get_current_user)):
    """
    One-time migration: stamp project_id on learned mappings that are missing it.
    Infers project from the conversion name embedded in captured_from.
    """
    items = await LearnedMapping.find(
        LearnedMapping.project_id == None  # noqa: E711
    ).to_list()

    # Build a lookup: conversion name -> project_id
    all_convs = await Conversion.find_all().to_list()
    name_to_project: dict[str, PydanticObjectId] = {
        c.name: c.project_id for c in all_convs if c.project_id
    }

    updated = 0
    skipped = 0
    for lm in items:
        if not lm.captured_from:
            skipped += 1
            continue
        # captured_from format: "ConversionName -- field_name" or "ConversionName -- field_name (manual)"
        conv_name = lm.captured_from.split(" -- ")[0].strip()
        pid = name_to_project.get(conv_name)
        if pid:
            await lm.set({"project_id": pid})
            updated += 1
        else:
            skipped += 1

    return {
        "total_without_project": len(items),
        "updated": updated,
        "skipped_no_match": skipped,
    }


@router.patch("/{learned_id}", response_model=LearnedMappingOut)
async def update_learned(
    learned_id: str,
    payload: LearnedMappingUpdate,
    _: User = Depends(get_current_user),
):
    item = await LearnedMapping.get(PydanticObjectId(learned_id))
    if not item:
        raise HTTPException(404, "Not found")
    updates = payload.model_dump(exclude_unset=True)
    if updates:
        await item.set(updates)
        item = await LearnedMapping.get(PydanticObjectId(learned_id))
    return _serialize(item)


@router.delete("/{learned_id}")
async def delete_learned(
    learned_id: str,
    _: User = Depends(get_current_user),
):
    item = await LearnedMapping.get(PydanticObjectId(learned_id))
    if not item:
        raise HTTPException(404, "Not found")
    await item.delete()
    return {"deleted": learned_id}
