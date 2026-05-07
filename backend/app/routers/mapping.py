"""Mapping suggestion endpoints — scoped to a Conversion (object)."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.database import get_db
from app.models.conversion import Conversion
from app.models.mapping import MappingSuggestion
from app.models.transformation import Crosswalk, TransformationRule
from app.models.user import User
from app.parsers import parse_tabular
from app.schemas.mapping import MappingOut, MappingUpdate
from app.schemas.transformation import TransformationRuleCreate, TransformationRuleOut
from app.services.auth_service import get_current_user
from app.services.learning_service import (
    record_learning_from_mapping,
    record_learning_from_rule,
)
from app.services.mapping_service import enrich_mapping_with_samples, run_mapping_suggestions
from app.transformations.engine import apply_pipeline

router = APIRouter(prefix="/api", tags=["mapping"])


def _require_conversion(db: Session, conversion_id: int) -> Conversion:
    c = db.query(Conversion).filter(Conversion.id == conversion_id).first()
    if not c:
        raise HTTPException(404, "Conversion not found")
    if not c.dataset_id or not c.template_id:
        raise HTTPException(
            400,
            "Conversion is not fully bound — set both a source dataset and a target FBDI template first.",
        )
    return c


@router.post(
    "/conversions/{conversion_id}/suggest-mapping", response_model=list[MappingOut]
)
def suggest_mapping(
    conversion_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    conv = _require_conversion(db, conversion_id)
    saved = run_mapping_suggestions(db, conv)
    return enrich_mapping_with_samples(db, conv, saved)


@router.get(
    "/conversions/{conversion_id}/mappings", response_model=list[MappingOut]
)
def list_mappings(
    conversion_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    conv = _require_conversion(db, conversion_id)
    items = (
        db.query(MappingSuggestion)
        .filter(MappingSuggestion.conversion_id == conversion_id)
        .all()
    )
    return enrich_mapping_with_samples(db, conv, items)


@router.put("/mappings/{mapping_id}", response_model=MappingOut)
def update_mapping(
    mapping_id: int,
    payload: MappingUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    m = db.query(MappingSuggestion).filter(MappingSuggestion.id == mapping_id).first()
    if not m:
        raise HTTPException(404, "Mapping not found")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] == "approved":
        m.approved_by = user.email
        m.approved_at = datetime.utcnow()
    for k, v in data.items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    conv = db.query(Conversion).filter(Conversion.id == m.conversion_id).first()
    if m.status in ("approved", "overridden") and m.source_column:
        record_learning_from_mapping(db, m, conv, captured_by=user.email)
    return enrich_mapping_with_samples(db, conv, [m])[0]


@router.put("/mappings/{mapping_id}/approve", response_model=MappingOut)
def approve_mapping(
    mapping_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    m = db.query(MappingSuggestion).filter(MappingSuggestion.id == mapping_id).first()
    if not m:
        raise HTTPException(404, "Mapping not found")
    m.status = "approved"
    m.approved_by = user.email
    m.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(m)
    conv = db.query(Conversion).filter(Conversion.id == m.conversion_id).first()
    if m.source_column:
        record_learning_from_mapping(db, m, conv, captured_by=user.email)
    return enrich_mapping_with_samples(db, conv, [m])[0]


@router.post(
    "/conversions/{conversion_id}/rules", response_model=TransformationRuleOut
)
def add_rule(
    conversion_id: int,
    payload: TransformationRuleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conv = db.query(Conversion).filter(Conversion.id == conversion_id).first()
    if not conv:
        raise HTTPException(404, "Conversion not found")
    seq = (
        db.query(TransformationRule)
        .filter(TransformationRule.conversion_id == conversion_id)
        .count()
    )
    r = TransformationRule(
        conversion_id=conversion_id, sequence=seq, **payload.model_dump()
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    # A manually-authored rule is just as authoritative as one approved on a
    # mapping — surface it in the Rule Library so future cycles can reuse it.
    record_learning_from_rule(db, r, conv, captured_by=user.email)
    return r


class PreviewRule(BaseModel):
    rule_type: str
    config: dict[str, Any] = {}


class PreviewRequest(BaseModel):
    rules: list[PreviewRule]
    source_column: str | None = None
    sample_size: int = 5


class PreviewSample(BaseModel):
    source: Any
    output: Any
    error: str | None = None


class PreviewResponse(BaseModel):
    samples: list[PreviewSample]


@router.post(
    "/conversions/{conversion_id}/rules/preview", response_model=PreviewResponse
)
def preview_rules(
    conversion_id: int,
    payload: PreviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Dry-run a rule pipeline against the conversion's dataset and return
    source/output pairs for the first N rows. Powers the Studio live preview.
    """
    conv = db.query(Conversion).filter(Conversion.id == conversion_id).first()
    if not conv or not conv.dataset:
        raise HTTPException(404, "Conversion or dataset not found")

    df = parse_tabular(conv.dataset.file_path, file_type=conv.dataset.file_type)

    crosswalks: dict[str, dict[str, str]] = {}
    for cw in (
        db.query(Crosswalk).filter(Crosswalk.conversion_id == conversion_id).all()
    ):
        crosswalks.setdefault(cw.name, {})[cw.source_value] = cw.target_value

    rules = [{"rule_type": r.rule_type, "config": r.config} for r in payload.rules]

    out: list[PreviewSample] = []
    n = max(1, min(int(payload.sample_size), 20))
    for idx, row in df.head(n).iterrows():
        row_dict = {k: ("" if v is None else v) for k, v in row.to_dict().items()}
        src_value = (
            row_dict.get(payload.source_column)
            if payload.source_column
            else None
        )
        ctx = {
            "row_index": int(idx) + 1,
            "current_user": user.email,
            "now": datetime.utcnow(),
            "crosswalks": crosswalks,
        }
        try:
            transformed = apply_pipeline(rules, src_value, row=row_dict, ctx=ctx)
            out.append(PreviewSample(source=src_value, output=transformed))
        except Exception as exc:  # surface engine errors to the UI
            out.append(
                PreviewSample(source=src_value, output=None, error=str(exc))
            )
    return PreviewResponse(samples=out)


@router.get(
    "/conversions/{conversion_id}/rules", response_model=list[TransformationRuleOut]
)
def list_rules(
    conversion_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return (
        db.query(TransformationRule)
        .filter(TransformationRule.conversion_id == conversion_id)
        .order_by(TransformationRule.sequence)
        .all()
    )


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    r = db.query(TransformationRule).filter(TransformationRule.id == rule_id).first()
    if not r:
        raise HTTPException(404, "Rule not found")
    db.delete(r)
    db.commit()
    return {"deleted": rule_id}
