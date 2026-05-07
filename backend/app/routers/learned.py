"""Learning library endpoints — registry of human-approved mappings/rules."""
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.learned import LearnedMapping
from app.models.user import User
from app.schemas.learned import LearnedMappingCreate, LearnedMappingOut, LearningStats
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/learned-mappings", tags=["learning"])


# Default category seeds shown in the empty-state grid (mirrors CHRM AI's pattern)
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
def create_learned(
    payload: LearnedMappingCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = LearnedMapping(**payload.model_dump(), captured_by=user.email)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=list[LearnedMappingOut])
def list_learned(
    kind: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(LearnedMapping)
    if kind:
        q = q.filter(LearnedMapping.kind == kind)
    if category:
        q = q.filter(LearnedMapping.category == category)
    return q.order_by(LearnedMapping.captured_at.desc()).all()


@router.get("/stats", response_model=LearningStats)
def learning_stats(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    items = db.query(LearnedMapping).all()
    total = len(items)
    avg_boost = round(
        sum(i.confidence_boost or 0 for i in items) / total, 3
    ) if total else 0.0
    records_fixed = sum(int(i.records_auto_fixed or 0) for i in items)

    # Heuristic — assume each captured rule saves ~4 minutes of analyst time
    minutes_saved = total * 4

    by_cat = Counter(i.category for i in items)
    # Always include the seed categories so the UI shows the empty buckets too
    cat_rows = []
    for c in DEFAULT_CATEGORIES:
        cat_rows.append({"category": c, "count": by_cat.get(c, 0)})
    # Plus any extras captured from approvals not in the default set
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


@router.delete("/{learned_id}")
def delete_learned(
    learned_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    item = db.query(LearnedMapping).filter(LearnedMapping.id == learned_id).first()
    if not item:
        raise HTTPException(404, "Not found")
    db.delete(item)
    db.commit()
    return {"deleted": learned_id}
