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
        from app.services.dataset_file_store import materialize_dataset_file
        src_path = await materialize_dataset_file(dataset) if dataset else None
        if src_path is None:
            raise ValueError("Dataset source file not found; please re-upload the dataset")
        src = parse_tabular(str(src_path), file_type=dataset.file_type, nrows=max_rows)
    else:
        from app.services.mapping_service import ebs_fetch_rows
        table = getattr(conversion, "ebs_table_hint", "") or ""
        rows = await ebs_fetch_rows(table) if table else []
        src = pd.DataFrame(rows)
        if max_rows:
            src = src.head(max_rows)
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None

    mappings = await MappingSuggestion.find(MappingSuggestion.conversion_id == conversion.id).to_list()
    # Dedupe: a target field can end up with more than one MappingSuggestion (an
    # auto-map "suggested" row plus an approved/overridden one, e.g. after prompt
    # steering or learning). Keep the highest-priority mapping per target field so
    # the output uses the APPROVED source, not whichever row was processed last.
    _PRIO = {"overridden": 4, "approved": 3, "not_applicable": 2, "rejected": 1, "suggested": 0}
    _best: dict = {}
    for _m in mappings:
        _cur = _best.get(_m.target_field_id)
        if _cur is None or _PRIO.get(_m.status or "suggested", 0) > _PRIO.get(_cur.status or "suggested", 0):
            _best[_m.target_field_id] = _m
    mappings = list(_best.values())
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


# Control-field defaults + auto-numbered keys so generated files aren't blank in
# the columns Fusion requires — mirrors how a consultant fills the template
# (Import Action = CREATE, a batch id, a running supplier number, standard
# org/type values). Applied ONLY to columns the source left entirely blank.
_CONTROL_DEFAULTS: dict[str, str] = {
    # Applied by EXACT column name to blank columns only, so these are safe to
    # merge across objects (a supplier sheet has no "Transaction Type" column,
    # an item sheet has no "Supplier Type", etc.). Keys are lower-case with any
    # trailing "*" already stripped (matcher strips "*").
    # --- Supplier import (POZ_SUPPLIERS_INT) — confirmed vs the gold output ---
    "import action": "CREATE",
    "batch id": "900001",
    "tax organization type": "Corporation",
    "organization type": "Corporation",
    "supplier type": "Supplier",
    "business relationship": "PROSPECTIVE",
    "federal reportable": "N",
    "delivery channel": "EMAIL",
    # --- Supplier address (POZ_SUPPLIER_ADDRESSES_INT) ---
    "address name": "PRIMARY",
    "pay": "Y",
    "ordering": "Y",
    "rfq or bidding": "Y",
    # --- Supplier site (POZ_SUPPLIER_SITES_INT) ---
    "supplier site": "PRIMARY",
    # --- Supplier site assignment (POZ_SITE_ASSIGNMENTS_INT) ---
    # Business-unit columns are client-specific; default to the primary BU for
    # this engagement so the file is load-ready (override per-row via mapping).
    "client bu": "Nextpower LLC Business Unit",
    "procurement bu": "Nextpower LLC Business Unit",
    "bill-to bu": "Nextpower LLC Business Unit",
    # --- Supplier contacts (POZ_SUP_CONTACTS) ---
    "administrative contact": "Y",
    "user account action": "NONE",
    # --- Item (Product Hub) — sensible defaults; confirm vs a reference ---
    "transaction type": "SYNC",
    "item class": "Root Item Class",
    # --- Customer (Trading Community / AR) — confirm vs a reference ---
    "insert update flag": "I",
    "create or update record": "1",
}


def _header_label(f) -> str:
    """The header text to write for a field. Prefer the raw header captured at
    parse time (which carries Oracle's exact '*' required markers, e.g.
    'Import Action *', 'Supplier Name*'); fall back to appending a trailing '*'
    for required fields when only the cleaned name is stored (older templates)."""
    raw = (getattr(f, "display_name", None) or "").strip()
    if "*" in raw:
        return raw
    base = (f.field_name or "").strip()
    if getattr(f, "required", False) and base and "*" not in base:
        return base + " *"
    return base or raw
_SEQ_FIELDS: set[str] = {
    "suppliernumber", "supplierpartynumber",   # supplier
    "partynumber", "customeraccountnumber", "customernumber",  # customer
}

# Control fields that are CONSTANTS in the Oracle gold templates. These are set
# AUTHORITATIVELY — the standard value is written even if auto-map filled the
# column with a (usually wrong) source guess, e.g. Address Name / Supplier Site
# landing a street address or "No", or User Account Action getting a phone
# fragment. Excludes fields whose value is genuinely per-row (Business Unit,
# currency) or correctly source-mapped (names, addresses, tax ids, web site).
_AUTHORITATIVE: set[str] = {
    "import action", "batch id",
    "tax organization type", "organization type", "supplier type",
    "business relationship", "federal reportable", "delivery channel",
    "address name", "pay", "ordering", "rfq or bidding",
    "supplier site", "user account action",
}


def _apply_control_defaults(df: pd.DataFrame, seq_start: int = 100000) -> pd.DataFrame:
    n = len(df)
    if n == 0:
        return df
    for col in df.columns:
        key = str(col).strip().lower().rstrip("*").strip()
        keyc = key.replace(" ", "")
        if keyc in _SEQ_FIELDS:
            # Running key column — authoritative: Fusion needs a clean sequential
            # id, not whatever source column auto-map happened to guess.
            df[col] = [str(seq_start + i) for i in range(n)]
        elif key in _AUTHORITATIVE:
            # Gold-constant control field — always write the standard value,
            # overriding any wrong auto-mapped source guess.
            df[col] = _CONTROL_DEFAULTS[key]
        elif key in _CONTROL_DEFAULTS and bool((df[col].astype(str).str.strip() == "").all()):
            # Other defaults only fill columns the source left entirely blank.
            df[col] = _CONTROL_DEFAULTS[key]
    return df


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
    # Name the output after the object: prefer the template's business object,
    # then the conversion's target object (always set, e.g. "Supplier Site"),
    # then the template name — only fall back to "fbdi" if nothing is available.
    obj_name = (
        (template.business_object if template else None)
        or getattr(conversion, "target_object", None)
        or (template.name if template else None)
        or "fbdi"
    )

    def _finalize(sfields: list) -> pd.DataFrame:
        # Req 8 — exactly this sheet's interface columns, in sequence, blanks
        # where unmapped, no instruction rows. Data ops (date reformat, control
        # defaults) run while columns are still keyed by cleaned field_name; the
        # LAST step renames columns to Oracle's exact header labels (with the
        # '*' required markers) so the file matches the shipped template.
        cols = _dedup([f.field_name for f in sfields])
        sdf = df.reindex(columns=cols, fill_value="")
        sdf = _format_date_columns(sdf, sfields)
        sdf = _apply_control_defaults(sdf)
        hdr: dict[str, str] = {}
        for f in sfields:
            hdr.setdefault(f.field_name, _header_label(f))
        sdf.columns = [hdr.get(c, c) for c in sdf.columns]
        return sdf

    # Emit a POPULATED .xlsx template — the interface sheet(s) with headers +
    # data, matching the Oracle template format the team ships (not stripped
    # CSVs). Control columns the source didn't map get sensible defaults + a
    # running key number so the file is load-ready.
    out_name = f"{obj_name}_{ts}.xlsx"
    out_path = out_dir / out_name
    total_rows = 0
    total_cols = 0
    with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
        if sheets_with_fields:
            for s in sheets_with_fields:
                sdf = _finalize(fields_by_sheet[s.id])
                sdf.to_excel(xw, index=False, sheet_name=_safe_sheet_name(s.sheet_name)[:31])
                total_rows = max(total_rows, len(sdf))
                total_cols += len(sdf.columns)
        else:
            fdf = _finalize(fields) if fields else _apply_control_defaults(df)
            fdf.to_excel(xw, index=False, sheet_name=(_safe_sheet_name(obj_name)[:31] or "Sheet1"))
            total_rows = len(fdf)
            total_cols = len(fdf.columns)
    artefact = ConvertedOutput(
        conversion_id=conversion.id, output_file_path=str(out_path),
        output_file_name=out_name, row_count=total_rows, column_count=total_cols,
        status="generated",
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
