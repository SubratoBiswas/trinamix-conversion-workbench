"""Generate the Fusion-ready FBDI output by applying mappings + rules to the source dataset."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings
from app.models.fbdi import FBDIField
from app.models.mapping import MappingSuggestion
from app.models.output import ConvertedOutput
from app.models.conversion import Conversion
from app.models.transformation import TransformationRule
from app.parsers import parse_tabular
from app.services.learning_service import (
    REFERENCE_KEY_FIELDS,
    list_reference_standards_for_object,
)
from app.transformations import apply_pipeline


def _resolve_inherited_reference_standards(
    db: Session,
    conversion: Conversion,
    fields_by_id: dict[int, FBDIField],
) -> dict[int, list[dict[str, Any]]]:
    """For each FK column on this conversion, fetch any active Reference
    Standards taught on the master entity and return them as rule dicts to
    *prepend* to that column's pipeline. The master conversion itself never
    inherits — its own column *is* the source of truth.
    """
    out: dict[int, list[dict[str, Any]]] = {}
    for fid, f in fields_by_id.items():
        for master_obj, key_fields in REFERENCE_KEY_FIELDS.items():
            if conversion.target_object == master_obj:
                continue  # the master shouldn't inherit from itself
            if f.field_name not in key_fields:
                continue
            standards = list_reference_standards_for_object(db, master_obj)
            if standards:
                out[fid] = [
                    {
                        "rule_type": s.rule_type,
                        "config": s.rule_config or {},
                    }
                    for s in standards
                    if s.rule_type
                ]
            break
    return out


def _build_field_pipelines(
    db: Session, conversion: Conversion, fields_by_id: dict[int, FBDIField]
) -> dict[int, list[dict[str, Any]]]:
    """Returns {target_field_id: [rule_dicts]} — Reference Standards inherited
    from the master entity prepended, then conversion-local rules ordered by
    sequence. Last-applied wins, so a downstream conversion can still override
    an inherited standard with its own rule on the same column.
    """
    rules = (
        db.query(TransformationRule)
        .filter(TransformationRule.conversion_id == conversion.id)
        .order_by(TransformationRule.target_field_id, TransformationRule.sequence)
        .all()
    )
    inherited = _resolve_inherited_reference_standards(db, conversion, fields_by_id)
    out: dict[int, list[dict[str, Any]]] = {k: list(v) for k, v in inherited.items()}
    for r in rules:
        if r.target_field_id is None:
            continue
        out.setdefault(r.target_field_id, []).append(
            {"rule_type": r.rule_type, "config": r.rule_config or {}}
        )
    return out


def build_converted_dataframe(
    db: Session, conversion: Conversion
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Apply approved/overridden mappings + transformation rules + suggested
    transformations to produce the converted DataFrame.

    Returns (df, lineage). Lineage maps target_column -> {source_column, rules[]}.
    """
    src = parse_tabular(conversion.dataset.file_path, file_type=conversion.dataset.file_type)

    mappings = (
        db.query(MappingSuggestion)
        .filter(MappingSuggestion.conversion_id == conversion.id)
        .all()
    )
    fields_by_id: dict[int, FBDIField] = {f.id: f for f in conversion.template.fields}
    pipelines = _build_field_pipelines(db, conversion, fields_by_id)

    out_cols: dict[str, list[Any]] = {}
    lineage: dict[str, dict[str, Any]] = {}
    n_rows = len(src)

    # Sort by sequence so the output respects FBDI column order
    sorted_mappings = sorted(
        mappings,
        key=lambda m: (fields_by_id.get(m.target_field_id).sequence if fields_by_id.get(m.target_field_id) else 0),
    )

    for m in sorted_mappings:
        tgt = fields_by_id.get(m.target_field_id)
        if not tgt:
            continue
        if m.status == "not_applicable":
            continue

        col_values: list[Any] = []
        rules: list[dict[str, Any]] = list(pipelines.get(tgt.id, []))
        # If suggested_transformation exists and no explicit rule, include it
        if m.suggested_transformation and not rules and m.status != "rejected":
            rules.append(
                {
                    "rule_type": m.suggested_transformation.get("rule_type"),
                    "config": m.suggested_transformation.get("config", {}),
                }
            )

        if m.source_column and m.source_column in src.columns:
            for i in range(n_rows):
                row = {c: src.iloc[i][c] for c in src.columns}
                v = src.iloc[i][m.source_column]
                if rules:
                    v = apply_pipeline(rules, v, row=row)
                if (v is None or str(v).strip() == "") and m.default_value is not None:
                    v = m.default_value
                col_values.append(v)
        else:
            # Use default value if provided; otherwise empty
            default = m.default_value or ""
            col_values = [default] * n_rows

        out_cols[tgt.field_name] = col_values
        lineage[tgt.field_name] = {
            "source_column": m.source_column,
            "default_value": m.default_value,
            "rules": rules,
            "status": m.status,
            "confidence": m.confidence,
        }

    out_df = pd.DataFrame(out_cols)
    return out_df, lineage


def generate_output_artifact(
    db: Session, conversion: Conversion, fmt: str = "csv"
) -> ConvertedOutput:
    df, _lineage = build_converted_dataframe(db, conversion)
    fmt = fmt.lower()
    out_dir = settings.output_path / f"project_{conversion.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    if fmt == "xlsx":
        out_name = f"{conversion.template.business_object or 'fbdi'}_{ts}.xlsx"
        out_path = out_dir / out_name
        df.to_excel(out_path, index=False)
    else:
        out_name = f"{conversion.template.business_object or 'fbdi'}_{ts}.csv"
        out_path = out_dir / out_name
        df.to_csv(out_path, index=False)

    artefact = ConvertedOutput(
        conversion_id=conversion.id,
        output_file_path=str(out_path),
        output_file_name=out_name,
        row_count=len(df),
        column_count=len(df.columns),
        status="generated",
    )
    db.add(artefact)
    conversion.status = "output_generated"
    conversion.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(artefact)
    return artefact


def get_output_preview(
    db: Session, conversion: Conversion, limit: int = 50
) -> dict[str, Any]:
    df, lineage = build_converted_dataframe(db, conversion)
    head = df.head(limit)
    return {
        "columns": list(head.columns.astype(str)),
        "rows": head.fillna("").to_dict(orient="records"),
        "total_rows": int(len(df)),
        "lineage": lineage,
    }
