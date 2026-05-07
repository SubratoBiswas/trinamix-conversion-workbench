"""Mapping orchestration: build AI inputs, run provider, persist suggestions."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ai import get_mapping_provider
from app.ai.base import SourceColumn, TargetField
from app.models.dataset import Dataset, DatasetColumnProfile
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.mapping import MappingSuggestion
from app.models.conversion import Conversion
from app.parsers import parse_tabular
from app.services.learning_service import apply_learned_to_conversion


def _source_columns_for(db: Session, dataset: Dataset) -> list[SourceColumn]:
    profs = (
        db.query(DatasetColumnProfile)
        .filter(DatasetColumnProfile.dataset_id == dataset.id)
        .order_by(DatasetColumnProfile.position)
        .all()
    )
    return [
        SourceColumn(
            name=p.column_name,
            inferred_type=p.inferred_type or "string",
            sample_values=[str(v) for v in (p.sample_values or [])],
            null_percent=p.null_percent or 0.0,
            distinct_count=p.distinct_count or 0,
            pattern_summary=p.pattern_summary,
        )
        for p in profs
    ]


def _target_fields_for(db: Session, template: FBDITemplate) -> list[TargetField]:
    fields = (
        db.query(FBDIField)
        .filter(FBDIField.template_id == template.id)
        .order_by(FBDIField.sequence)
        .all()
    )
    return [
        TargetField(
            id=f.id,
            field_name=f.field_name,
            description=f.description,
            data_type=f.data_type,
            max_length=f.max_length,
            required=bool(f.required),
        )
        for f in fields
    ]


def run_mapping_suggestions(db: Session, conversion: Conversion) -> list[MappingSuggestion]:
    """(Re-)generate mapping suggestions for a project. Existing suggestions
    in 'suggested' status are replaced; approved/rejected/overridden ones are
    preserved (mapping engineer's manual decisions stay sticky)."""
    sources = _source_columns_for(db, conversion.dataset)
    targets = _target_fields_for(db, conversion.template)
    provider = get_mapping_provider()
    ai_results = provider.suggest_mappings(sources, targets)

    # Index existing mappings keyed by target field id
    existing = {
        m.target_field_id: m
        for m in db.query(MappingSuggestion)
        .filter(MappingSuggestion.conversion_id == conversion.id)
        .all()
    }

    saved: list[MappingSuggestion] = []
    for s in ai_results:
        m = existing.get(s.target_field_id)
        if m and m.status in ("approved", "rejected", "overridden", "not_applicable"):
            saved.append(m)
            continue
        if m:
            m.source_column = s.source_column
            m.confidence = s.confidence
            m.reason = s.reason
            m.suggested_transformation = s.suggested_transformation
            m.review_required = 1 if s.review_required else 0
            m.status = "suggested"
            m.updated_at = datetime.utcnow()
        else:
            m = MappingSuggestion(
                conversion_id=conversion.id,
                target_field_id=s.target_field_id,
                source_column=s.source_column,
                confidence=s.confidence,
                reason=s.reason,
                suggested_transformation=s.suggested_transformation,
                review_required=1 if s.review_required else 0,
                status="suggested",
            )
            db.add(m)
        saved.append(m)

    conversion.status = "mapping_suggested"
    conversion.updated_at = datetime.utcnow()
    db.commit()

    # Replay any human-approved patterns from the learning library — same
    # business object on a new file should auto-correct without re-asking.
    apply_learned_to_conversion(db, conversion, saved)
    return saved


def enrich_mapping_with_samples(
    db: Session, conversion: Conversion, mappings: list[MappingSuggestion]
) -> list[dict[str, Any]]:
    """Attach sample source values + target field metadata for the review UI."""
    df = parse_tabular(conversion.dataset.file_path, file_type=conversion.dataset.file_type)
    fields_by_id = {f.id: f for f in conversion.template.fields}
    out: list[dict[str, Any]] = []
    for m in mappings:
        tgt = fields_by_id.get(m.target_field_id)
        sample_src: list[Any] = []
        if m.source_column and m.source_column in df.columns:
            sample_src = [
                str(v) for v in df[m.source_column].astype(str).head(5).tolist()
            ]
        out.append(
            {
                "id": m.id,
                "conversion_id": m.conversion_id,
                "target_field_id": m.target_field_id,
                "target_field_name": tgt.field_name if tgt else None,
                "target_required": bool(tgt.required) if tgt else False,
                "target_data_type": tgt.data_type if tgt else None,
                "target_max_length": tgt.max_length if tgt else None,
                "source_column": m.source_column,
                "confidence": m.confidence,
                "reason": m.reason,
                "suggested_transformation": m.suggested_transformation,
                "review_required": m.review_required,
                "status": m.status,
                "default_value": m.default_value,
                "comment": m.comment,
                "approved_by": m.approved_by,
                "approved_at": m.approved_at,
                "sample_source_values": sample_src,
                "sample_converted_values": [],  # filled later if rules attached
            }
        )
    return out
