"""Generate an Oracle HCM Data Loader (HDL) artifact from a Workday-style extract.

Produces a .zip of pipe-delimited ``.dat`` files (Location.dat, Job.dat,
Position.dat, PositionHierarchy.dat, Worker.dat) built from the loader schema in
``hdl_schema``. Each file is one or more component blocks:

    METADATA|Worker|EffectiveStartDate|PersonNumber|...
    MERGE|Worker|2017/01/01|1001898|...

Worker.dat carries the Worker block plus its six child components, all linked by
``SourceSystemId`` composite keys. Setup objects (Location/Job/Position) are
deduped to one record per unique natural key; PositionHierarchy is emitted
METADATA-only until the client supplies the parent/child structure.

This runs off the same conversion + dataset the FBDI path uses; the generator
router in ``output_service`` sends "Employee HDL" objects here instead of the CSV
fan-out.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings
from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.mapping import MappingSuggestion
from app.models.output import ConvertedOutput
from app.parsers import parse_tabular
from app.services.hdl_schema import (
    COUNTRY_ISO2, HDL_LOAD_ORDER, HDL_OBJECTS, object_label as _object_label,
)

logger = logging.getLogger(__name__)

_EXCEL_EPOCH = datetime(1899, 12, 30)  # Excel/Workday serial-date origin


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _clean(v: Any) -> str:
    """A single HDL cell: no pipes / newlines (they'd break the record), trimmed."""
    if v is None:
        return ""
    s = str(v)
    if s.strip().lower() in ("nan", "none", "nat", "null", "#n/a", "n/a"):
        return ""
    return s.replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def _hdl_date(v: Any) -> str:
    """Reformat a Workday date (Excel serial or common string) to HDL YYYY/MM/DD."""
    if v is None:
        return ""
    if isinstance(v, (datetime, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%Y/%m/%d")
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return ""
    # Excel/Workday serial number.
    try:
        if re.fullmatch(r"\d+(\.0+)?", s):
            return (_EXCEL_EPOCH + timedelta(days=int(float(s)))).strftime("%Y/%m/%d")
    except (ValueError, OverflowError):
        pass
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d",
                "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y/%m/%d")
        except ValueError:
            continue
    return s  # leave as-is; validation can flag it


def _iso_country(v: Any) -> str:
    s = _clean(v)
    if not s:
        return ""
    if len(s) == 2 and s.isalpha():
        return s.upper()
    return COUNTRY_ISO2.get(s.lower(), s)


def render_cell(spec: dict, resolve) -> str:
    """Compute one HDL cell from its field spec. ``resolve(field_name, source_name)``
    returns the raw source value (or None). Pure + module-level so it's unit-tested
    directly rather than through a DB-backed generation run."""
    kind = spec.get("kind")
    if kind == "const":
        return _clean(spec.get("value"))
    if kind == "const_if_blank":
        # The extract wins; the constant is the open-ended fallback (strategy 9.1).
        got = _clean(resolve(spec["name"], spec.get("source")))
        return got or _clean(spec.get("value"))
    if kind == "blank":
        return ""
    if kind == "key":
        base = _clean(resolve(spec["name"], spec.get("key_source")))
        return f"{spec.get('prefix', '')}{spec.get('sep', '_')}{base}" if base else ""
    if kind == "manager":
        raw = _clean(resolve(spec["name"], spec.get("source")))
        mnum = re.findall(r"(\d+)", raw)
        return f"{spec.get('prefix', 'E')}{mnum[-1]}" if mnum else ""
    val = resolve(spec["name"], spec.get("source"))
    if kind == "date":
        return _hdl_date(val)
    if kind == "valuemap":
        m = spec.get("map", {})
        if m.get("kind") == "iso_country":
            return _iso_country(val)
        s = _clean(val)
        for k, mv in m.items():
            if k in ("default", "kind"):
                continue
            if isinstance(k, str) and k.lower() == s.lower():
                return _clean(mv)
        return _clean(m.get("default", s))
    return _clean(val)  # plain source


def is_hdl_conversion(template: FBDITemplate | None, conversion: Conversion | None = None) -> bool:
    """True when this object should be generated as HDL rather than FBDI."""
    from app.services.hdl_schema import HDL_BUSINESS_OBJECT
    bo = ((template.business_object if template else None)
          or (getattr(conversion, "target_object", None) if conversion else None)
          or "").strip().lower()
    if bo == HDL_BUSINESS_OBJECT.lower():
        return True
    nm = (template.name if template else "").strip().lower()
    return "hdl" in nm and ("employee" in nm or "worker" in nm or "hcm" in nm)


async def generate_hdl_artifact(conversion: Conversion, fmt: str = "dat") -> ConvertedOutput:
    """Build the Employee HDL .dat bundle for a conversion and persist it."""
    if not conversion.dataset_id:
        raise ValueError("HDL generation needs an uploaded Workday extract (no dataset linked).")
    dataset = await Dataset.get(conversion.dataset_id)
    from app.services.dataset_file_store import materialize_dataset_file
    src_path = await materialize_dataset_file(dataset) if dataset else None
    if src_path is None:
        raise ValueError("Dataset source file not found; please re-upload the dataset.")
    src = parse_tabular(str(src_path), file_type=dataset.file_type)

    # Normalized dataset-column lookup so schema source names ("Employee ID") and
    # user-mapped source columns resolve regardless of spacing/case.
    col_by_norm = {_norm(c): c for c in src.columns}

    # Per-field-name source override from the conversion's approved mappings
    # (a human/AI mapping wins over the schema's canonical source hint).
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    override: dict[str, str] = {}
    if template:
        fields = await FBDIField.find(FBDIField.template_id == template.id).to_list()
        fname_by_id = {f.id: f.field_name for f in fields}
        maps = await MappingSuggestion.find(
            MappingSuggestion.conversion_id == conversion.id
        ).to_list()
        for m in maps:
            if m.status in ("not_applicable", "rejected"):
                continue
            fn = fname_by_id.get(m.target_field_id)
            if fn and getattr(m, "source_column", None):
                override[fn] = m.source_column

    def _resolve_col(field_name: str, schema_source) -> str | None:
        """Actual dataset column for a field: mapping override first, then the
        schema's canonical source (normalized match).

        ``schema_source`` may be a LIST of candidate spellings — the first one present
        in the dataset wins. The client's real input file is not the extract tab the
        schema was written against: JobCode's canonical source was "Business Title"
        while the file carries a proper "Job Code" holding GSSSSM_VP2 — the exact
        value their own HDL template shows. One guessed spelling binds to nothing and
        fails silently, which is the whole class of "fields are not being reflected".
        """
        ov = override.get(field_name)
        if ov and ov in src.columns:
            return ov
        if ov and _norm(ov) in col_by_norm:
            return col_by_norm[_norm(ov)]
        for cand in (schema_source if isinstance(schema_source, (list, tuple))
                     else [schema_source]):
            if cand and _norm(cand) in col_by_norm:
                return col_by_norm[_norm(cand)]
        return None

    def _cell(row: pd.Series, spec: dict) -> str:
        def _resolve(field_name: str, source_name: str | None):
            col = _resolve_col(field_name, source_name)
            return row[col] if col else None
        return render_cell(spec, _resolve)

    # ── Scope: active employees only (strategy assumptions A-02 / A-03) ──────
    # "An employee is active and in scope if ActiveStatus = Active"; inactive and
    # terminated employees are excluded from the load file. Every row was going in.
    #
    # Fails OPEN: if the column is absent or nothing matches, NOTHING is dropped.
    # Silently emitting an empty load file because a column was renamed would be
    # far worse than loading a few leavers, and the count below makes the
    # exclusion visible either way.
    _ACTIVE_VALUES = {"active", "a", "y", "yes", "true", "1"}
    _excluded_inactive = 0
    _active_col = col_by_norm.get(_norm("Active Status")) or col_by_norm.get(_norm("ActiveStatus"))
    if _active_col is not None:
        _mask = src[_active_col].map(lambda v: _clean(v).lower() in _ACTIVE_VALUES)
        _kept = int(_mask.sum())
        if _kept:
            _excluded_inactive = int(len(src) - _kept)
            src = src[_mask]
            if _excluded_inactive:
                logger.info("HDL: excluded %d inactive/terminated employee row(s) "
                         "per strategy A-02/A-03", _excluded_inactive)
        else:
            logger.warning("HDL: no row matched ActiveStatus=Active in %r — keeping "
                        "ALL rows rather than shipping an empty load file",
                        _active_col)
    else:
        logger.warning("HDL: no Active Status column found — every row kept; "
                    "strategy A-03 expects inactive employees to be excluded")

    def _rows_for(scope: str, dedup_source):
        if scope == "none":
            return []
        if scope == "distinct" and dedup_source:
            # Candidate list, same reasoning as _resolve_col: Location dedupes on
            # Location_Code in the real file and on Location in the extract tab.
            col = None
            for cand in (dedup_source if isinstance(dedup_source, (list, tuple))
                         else [dedup_source]):
                col = col_by_norm.get(_norm(cand))
                if col:
                    break
            if not col:
                return []
            seen: set[str] = set()
            out = []
            for _, r in src.iterrows():
                key = _clean(r[col])
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(r)
            return out
        return [r for _, r in src.iterrows()]  # "employee"

    out_dir = settings.output_path / f"conversion_{conversion.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    zip_name = f"Employee_HDL_{ts}.zip"
    zip_path = out_dir / zip_name

    def _write() -> tuple[int, int]:
        # row_count = the largest per-object record count (≈ employee count from
        # Worker.dat), mirroring the FBDI path's "max rows across sheets" so the UI
        # number is meaningful; column_count = total attributes across components.
        total_rows = 0
        total_attrs = 0
        # HCM Data Loader takes ONE .dat per zip, and strategy section 11 loads the
        # objects in strict sequence — each depends on records the previous one
        # created. A single zip holding all nine forces the analyst to unpack and
        # re-zip before every step, so each object gets its own zip, numbered in
        # load order, inside the download bundle (CW_Issues #26).
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as outer:
            for seq, obj in enumerate(HDL_LOAD_ORDER, start=1):
                spec = HDL_OBJECTS[obj]
                rows = _rows_for(spec["row_scope"], spec.get("dedup_source"))
                total_rows = max(total_rows, len(rows))
                lines: list[str] = []
                for comp_name, comp_fields in spec["components"]:
                    attrs = [f["name"] for f in comp_fields]
                    total_attrs += len(attrs)
                    lines.append("METADATA|" + comp_name + "|" + "|".join(attrs))
                    for r in rows:
                        vals = [_cell(r, f) for f in comp_fields]
                        lines.append("MERGE|" + comp_name + "|" + "|".join(vals))
                dat = "\n".join(lines) + "\n"

                inner = io.BytesIO()
                with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as iz:
                    iz.writestr(spec["dat"], dat)
                # The numeric prefix is the load sequence, so the upload order is
                # not something the analyst has to remember from the document. The
                # label is the client template's own sheet name, so the two Worker
                # passes are told apart at a glance — both contain Worker.dat,
                # because both load the same business object.
                outer.writestr(f"{seq:02d}_{_object_label(obj)}.zip", inner.getvalue())
        return total_rows, total_attrs

    import asyncio
    total_rows, total_attrs = await asyncio.to_thread(_write)

    artefact = ConvertedOutput(
        conversion_id=conversion.id, output_file_path=str(zip_path),
        output_file_name=zip_name, row_count=total_rows, column_count=total_attrs,
        status="generated",
    )
    await artefact.insert()
    await conversion.set({"status": "output_generated", "updated_at": datetime.utcnow()})
    logger.info("hdl generate: %s — %d .dat blocks, %d MERGE records",
                zip_name, len(HDL_LOAD_ORDER), total_rows)
    return artefact
