"""Generate the Fusion-ready FBDI output (async/Beanie)."""
from __future__ import annotations

import io
import re
import zipfile
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
    conversion: Conversion, max_rows: int | None = None,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    # Source rows come from the uploaded file (dataset mode) or are streamed
    # live from Oracle EBS (EBS mode — dataset_id is null). The column names in
    # either DataFrame match the mappings' source_column values. ``max_rows`` caps
    # the work for previews (only the shown rows are generated).
    if conversion.dataset_id:
        dataset = await Dataset.get(conversion.dataset_id)
        src = parse_tabular(dataset.file_path, file_type=dataset.file_type, nrows=max_rows)
    else:
        from app.services.mapping_service import ebs_fetch_rows
        table = getattr(conversion, "ebs_table_hint", "") or ""
        rows = await ebs_fetch_rows(table) if table else []
        src = pd.DataFrame(rows)
        if max_rows:
            src = src.head(max_rows)
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
    # Precompute ONCE (not per-mapping): a fast per-column value cache, and build
    # per-row dicts lazily only when a transformation rule needs sibling-column
    # context. The old code rebuilt a dict of every column for every row for every
    # mapping via slow .iloc scalar access - O(mappings x rows x cols).
    sorted_mappings = sorted(
        mappings,
        key=lambda m: (fields_by_id.get(m.target_field_id).sequence if fields_by_id.get(m.target_field_id) else 0),
    )
    # Memory: only materialize the source columns the mappings actually use, not
    # every column in a wide extract. On a 258-column dataset this is the
    # difference between caching ~18 columns and ~258 — the latter can OOM a
    # small container on large row counts (the FBDI output for wide files was
    # 503-ing). Columns referenced only by transformation-rule context are picked
    # up lazily via the ``records`` fallback below.
    needed_cols = {
        m.source_column for m in sorted_mappings
        if m.source_column and m.source_column in src.columns
    }
    col_cache: dict[str, list[Any]] = {c: src[c].tolist() for c in needed_cols}
    records: list[dict] | None = None
    for m in sorted_mappings:
        tgt = fields_by_id.get(m.target_field_id)
        if not tgt or m.status == "not_applicable":
            continue
        rules = list(pipelines.get(tgt.id, []))
        if m.suggested_transformation and not rules and m.status != "rejected":
            rules.append({"rule_type": m.suggested_transformation.get("rule_type"),
                          "config": m.suggested_transformation.get("config", {})})
        dv = m.default_value
        if m.source_column and m.source_column in col_cache:
            src_vals = col_cache[m.source_column]
            if rules:
                if records is None:
                    # Row context for transformation rules — restrict to the
                    # mapped columns so we don't materialize a dict of every
                    # column for every row (memory: avoids OOM on wide extracts).
                    ctx_cols = [c for c in needed_cols if c in src.columns]
                    records = src[ctx_cols].to_dict("records") if ctx_cols else src.to_dict("records")
                col_values = []
                for i in range(n_rows):
                    v = apply_pipeline(rules, src_vals[i], row=records[i])
                    if (v is None or str(v).strip() == "") and dv is not None:
                        v = dv
                    col_values.append(v)
            else:
                col_values = [
                    (dv if (v is None or str(v).strip() == "") and dv is not None else v)
                    for v in src_vals
                ]
        else:
            col_values = [dv or ""] * n_rows
        out_cols[tgt.field_name] = col_values
        lineage[tgt.field_name] = {"source_column": m.source_column, "default_value": m.default_value,
                                    "rules": rules, "status": m.status, "confidence": m.confidence}
    out_df = pd.DataFrame(out_cols)
    return out_df, lineage


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column headers to UPPER_UNDERSCORE (Oracle FBDI format)."""
    df.columns = [c.strip().upper().replace(" ", "_").replace("-", "_") for c in df.columns]
    return df


def _format_date_columns(df: pd.DataFrame, fields: list) -> pd.DataFrame:
    """Reformat any date/Date columns to YYYYMMDD as required by Oracle FBDI."""
    date_field_names = {
        f.field_name
        for f in fields
        if (f.data_type or "").lower() in ("date", "datetime")
    }
    for col in df.columns:
        if col in date_field_names:
            def _reformat(v: Any) -> Any:
                if v is None or str(v).strip() == "":
                    return v
                s = str(v).strip()
                for fmt_in in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
                               "%Y%m%d", "%Y/%m/%d %H:%M:%S"):
                    try:
                        return datetime.strptime(s, fmt_in).strftime("%Y%m%d")
                    except ValueError:
                        pass
                return v
            df[col] = df[col].apply(_reformat)
    return df


def _dedup(cols: list[str]) -> list[str]:
    seen: set[str] = set()
    return [c for c in cols if not (c in seen or seen.add(c))]


def _safe_sheet_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "sheet"


async def generate_output_artifact(conversion: Conversion, fmt: str = "csv") -> ConvertedOutput:
    from app.models.fbdi import FBDISheet
    df, _ = await build_converted_dataframe(conversion)
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None

    # Fetch fields (interface sequence) + sheets so we can emit exactly the
    # template's columns and, for multi-sheet workbooks, one file per sheet.
    fields = await FBDIField.find(
        FBDIField.template_id == template.id
    ).sort(+FBDIField.sequence).to_list() if template else []
    sheets = await FBDISheet.find(
        FBDISheet.template_id == template.id
    ).sort(+FBDISheet.sequence).to_list() if template else []

    # Group fields by their interface sheet (preserving field sequence).
    fields_by_sheet: dict[Any, list] = {}
    for f in fields:
        fields_by_sheet.setdefault(f.sheet_id, []).append(f)
    sheets_with_fields = [s for s in sheets if s.id in fields_by_sheet]

    fmt = fmt.lower()
    out_dir = settings.output_path / f"conversion_{conversion.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    obj_name = (template.business_object if template else None) or "fbdi"

    def _sheet_frame(sfields: list) -> pd.DataFrame:
        # Req 8 — exactly this sheet's interface columns, in sequence, real
        # headers, blanks where unmapped, no instruction rows.
        cols = _dedup([f.field_name for f in sfields])
        sdf = df.reindex(columns=cols, fill_value="")
        return _format_date_columns(sdf, sfields)

    # Multi-sheet FBDI workbook (Customer/Item/…): emit ONE CSV per interface
    # sheet — "all sheets populated" — numbered in load order, bundled in a zip.
    if fmt == "csv" and len(sheets_with_fields) > 1:
        buf = io.BytesIO()
        total_rows = 0
        total_cols = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, s in enumerate(sheets_with_fields, start=1):
                sdf = _sheet_frame(fields_by_sheet[s.id])
                zf.writestr(f"{i:02d}_{_safe_sheet_name(s.sheet_name)}.csv", sdf.to_csv(index=False))
                total_rows = max(total_rows, len(sdf))
                total_cols += len(sdf.columns)
        out_name = f"{obj_name}_{ts}.zip"
        out_path = out_dir / out_name
        out_path.write_bytes(buf.getvalue())
        artefact = ConvertedOutput(
            conversion_id=conversion.id, output_file_path=str(out_path),
            output_file_name=out_name, row_count=total_rows, column_count=total_cols,
            status="generated",
        )
        await artefact.insert()
        await conversion.set({"status": "output_generated", "updated_at": datetime.utcnow()})
        return artefact

    # Single-sheet (or xlsx): one flat file with all interface columns in order.
    if fields:
        df = df.reindex(columns=_dedup([f.field_name for f in fields]), fill_value="")
    df = _format_date_columns(df, fields)
    sheet_name = (sheets_with_fields[0].sheet_name if sheets_with_fields else None) or "SCM Items"
    if fmt == "xlsx":
        out_name = f"{obj_name}_{ts}.xlsx"
        out_path = out_dir / out_name
        df.to_excel(out_path, index=False, sheet_name=sheet_name[:31])  # Excel max 31 chars
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
    # Only generate the rows we actually show — previews were converting the whole
    # file (tens of thousands of rows) just to display 50.
    df, lineage = await build_converted_dataframe(conversion, max_rows=limit)
    head = df.head(limit)
    total = int(len(df))
    if conversion.dataset_id:
        ds = await Dataset.get(conversion.dataset_id)
        if ds and ds.row_count:
            total = int(ds.row_count)  # true dataset size, not the capped preview
    return {"columns": list(head.columns.astype(str)), "rows": head.fillna("").to_dict(orient="records"),
            "total_rows": total, "lineage": lineage}
