"""Learning library endpoints — registry of human-approved mappings/rules."""
from collections import Counter

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.models.learned import LearnedMapping
from app.models.user import User
from app.schemas.learned import LearnedMappingCreate, LearnedMappingOut, LearningStats
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


@router.post("", response_model=LearnedMappingOut)
async def create_learned(
    payload: LearnedMappingCreate,
    user: User = Depends(get_current_user),
):
    item = LearnedMapping(**payload.model_dump(), captured_by=user.email)
    await item.insert()
    return {**item.model_dump(), "id": str(item.id)}


@router.get("", response_model=list[LearnedMappingOut])
async def list_learned(
    kind: str | None = None,
    category: str | None = None,
    _: User = Depends(get_current_user),
):
    filters = []
    if kind:
        filters.append(LearnedMapping.kind == kind)
    if category:
        filters.append(LearnedMapping.category == category)
    query = LearnedMapping.find(*filters)
    items = await query.sort("-captured_at").to_list()
    return [{**item.model_dump(), "id": str(item.id)} for item in items]


@router.get("/stats", response_model=LearningStats)
async def learning_stats(_: User = Depends(get_current_user)):
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


@router.get("/knowledge-bank/stats")
async def knowledge_bank_stats(_: User = Depends(get_current_user)):
    """Per-source-ERP stats for the Knowledge Bank panel in Learning Center."""
    items = await LearnedMapping.find_all().to_list()
    from collections import Counter
    by_erp: Counter = Counter()
    for item in items:
        erp = getattr(item, "source_erp", None) or getattr(item, "captured_from", None) or "unknown"
        by_erp[erp] += 1
    return [{"source_erp": erp, "count": cnt} for erp, cnt in by_erp.most_common()]


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
