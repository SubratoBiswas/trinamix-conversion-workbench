"""Generate the Fusion-ready FBDI output (async/Beanie)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from app.config import settings
from app.models.dataset import Dataset
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.mapping import MappingSuggestion
from app.models.output import ConvertedOutput
from app.models.conversion import Conversion
from app.models.transformation import TransformationRule
from app.parsers import parse_tabular
from app.services.learning_service import REFERENCE_KEY_FIELDS
from app.transformations import apply_pipeline


async def _get_reference_standards(target_object: str | None) -> dict:
    from app.models.learned import LearnedMapping
    if not target_object:
        return {}
    standards = await LearnedMapping.find({"kind": "reference_standard", "target_object": target_object}).to_list()
    return {s.target_field: {"rule_type": s.rule_type, "config": s.rule_config or {}} for s in standards if s.rule_type}


async def build_converted_dataframe(
    conversion: Conversion,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    dataset = await Dataset.get(conversion.dataset_id)
    src = parse_tabular(dataset.file_path, file_type=dataset.file_type)
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None

    mappings = await MappingSuggestion.find(MappingSuggestion.conversion_id == conversion.id).to_list()
    fields = await FBDIField.find(FBDIField.template_id == template.id).to_list() if template else []
    fields_by_id = {f.id: f for f in fields}

    rules_list = await TransformationRule.find(
        TransformationRule.conversion_id == conversion.id
    ).sort(+TransformationRule.sequence).to_list()

    pipelines: dict = {}
    for r in rules_list:
        if r.target_field_id:
            pipelines.setdefault(r.target_field_id, []).append(
                {"rule_type": r.rule_type, "config": r.rule_config or {}}
            )

    out_cols: dict[str, list[Any]] = {}
    lineage: dict[str, dict[str, Any]] = {}
    n_rows = len(src)
    sorted_mappings = sorted(
        mappings,
        key=lambda m: (fields_by_id.get(m.target_field_id).sequence if fields_by_id.get(m.target_field_id) else 0),
    )
    for m in sorted_mappings:
        tgt = fields_by_id.get(m.target_field_id)
        if not tgt or m.status == "not_applicable":
            continue
        rules = list(pipelines.get(tgt.id, []))
        if m.suggested_transformation and not rules and m.status != "rejected":
            rules.append({"rule_type": m.suggested_transformation.get("rule_type"),
                          "config": m.suggested_transformation.get("config", {})})
        col_values: list[Any] = []
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
            col_values = [m.default_value or ""] * n_rows
        out_cols[tgt.field_name] = col_values
        lineage[tgt.field_name] = {"source_column": m.source_column, "default_value": m.default_value,
                                    "rules": rules, "status": m.status, "confidence": m.confidence}
    out_df = pd.DataFrame(out_cols)
    return out_df, lineage


async def generate_output_artifact(conversion: Conversion, fmt: str = "csv") -> ConvertedOutput:
    df, _ = await build_converted_dataframe(conversion)
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    fmt = fmt.lower()
    out_dir = settings.output_path / f"conversion_{conversion.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    obj_name = (template.business_object if template else None) or "fbdi"
    if fmt == "xlsx":
        out_name = f"{obj_name}_{ts}.xlsx"
        out_path = out_dir / out_name
        df.to_excel(out_path, index=False)
    else:
        out_name = f"{obj_name}_{ts}.csv"
        out_path = out_dir / out_name
        df.to_csv(out_path, index=False)
    artefact = ConvertedOutput(
        conversion_id=conversion.id, output_file_path=str(out_path),
        output_file_name=out_name, row_count=len(df), column_count=len(df.columns), status="generated",
    )
    await artefact.insert()
    await conversion.set({"status": "output_generated", "updated_at": datetime.utcnow()})
    return artefact


async def get_output_preview(conversion: Conversion, limit: int = 50) -> dict[str, Any]:
    df, lineage = await build_converted_dataframe(conversion)
    head = df.head(limit)
    return {"columns": list(head.columns.astype(str)), "rows": head.fillna("").to_dict(orient="records"),
            "total_rows": int(len(df)), "lineage": lineage}
