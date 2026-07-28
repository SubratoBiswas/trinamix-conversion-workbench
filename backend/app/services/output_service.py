"""Generate the Fusion-ready FBDI output (async/Beanie)."""
from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from pathlib import Path
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


# Rows per chunk for the streaming transform. The transform is row-local, so we
# process the source in windows and concat — this bounds peak memory (the per-row
# ``records`` context + column caches stay chunk-sized instead of whole-file) and
# keeps the CPU work in slices we can hand to a worker thread.
_TRANSFORM_CHUNK_ROWS = 25_000


def _rule_referenced_columns(rules: list[dict]) -> set[str]:
    """Source columns a rule reads OTHER than the cell's own mapped column —
    CASE_WHEN/CONDITIONAL ``if_column`` and CONCAT/COALESCE ``columns``. These must
    survive source-column pruning and be present in the per-row context, otherwise a
    derivation from a non-mapped column (e.g. Delivery Method from Email/Fax
    Transaction flags) silently sees blanks."""
    cols: set[str] = set()
    for r in rules or []:
        cfg = r.get("config") or {}
        rt = (r.get("rule_type") or "").upper()
        if rt in ("CONCAT", "COALESCE"):
            cols.update(c for c in (cfg.get("columns") or []) if c)
        elif rt == "CONDITIONAL" and cfg.get("if_column"):
            cols.add(cfg["if_column"])
        elif rt == "CASE_WHEN":
            for br in cfg.get("branches") or []:
                if br.get("if_column"):
                    cols.add(br["if_column"])
    return cols


def _transform_frame(
    src: pd.DataFrame, sorted_mappings: list, fields_by_id: dict, pipelines: dict,
    context_cols: set[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Pure, row-local transform of one source (chunk) frame → target columns.

    Synchronous and CPU-bound (run via ``asyncio.to_thread``). Contains NO
    cross-row state — running it per chunk and concatenating is byte-identical to
    running it on the whole frame. Sequence numbering and whole-column-blank
    control defaults are applied later on the full frame in ``_apply_control_defaults``.

    ``context_cols`` are extra source columns (referenced by CASE_WHEN/CONCAT/
    COALESCE rules but not themselves a mapped cell) that must appear in the per-row
    context so those rules can read them.
    """
    out_cols: dict[str, list[Any]] = {}
    lineage: dict[str, dict[str, Any]] = {}
    n_rows = len(src)
    needed_cols = {
        m.source_column for m in sorted_mappings
        if m.source_column and m.source_column in src.columns
    }
    ctx_all = list(needed_cols | {c for c in (context_cols or set()) if c in src.columns})
    col_cache: dict[str, list[Any]] = {c: src[c].tolist() for c in needed_cols}
    records: list[dict] | None = None
    for m in sorted_mappings:
        tgt = fields_by_id.get(m.target_field_id)
        if not tgt:
            continue
        # Statuses that DISCARD the mapped source column:
        #   not_applicable — the gold/user marked the field "leave blank".
        #   rejected       — the user threw the suggested source column away.
        # Both are normally skipped entirely, UNLESS an explicit default_value was
        # attached (e.g. Invoice Match Option = "Receipt"); an explicit default is
        # intent to populate, so it is emitted as a CONSTANT.
        # Critically, a discarded mapping must never read from source_column: it
        # still wins the per-target dedup (so a stale "suggested" row can't
        # resurrect it), and before this guard a "rejected" mapping was selected as
        # the field's mapping and then still wrote that column's values into the
        # FBDI — the UI and the mapping CSV said "rejected" while the output
        # carried the rejected column.
        _discarded = m.status in ("not_applicable", "rejected")
        if _discarded and not (m.default_value and str(m.default_value).strip()):
            continue
        rules = list(pipelines.get(tgt.id, []))
        if m.suggested_transformation and not rules and m.status != "rejected":
            rules.append({"rule_type": m.suggested_transformation.get("rule_type"),
                          "config": m.suggested_transformation.get("config", {})})
        dv = m.default_value
        has_src = bool(m.source_column) and m.source_column in col_cache and not _discarded
        if rules:
            # A transform rule (CASE_WHEN / COALESCE / CONCAT / …) can derive its
            # value from OTHER columns via the per-row context, so it must run even
            # when THIS target has no single source_column. The rule — including its
            # own configured default (e.g. a CASE_WHEN default of "") — is
            # authoritative: we do NOT overlay the mapping-level default_value on a
            # rule result, so an intentional blank is never clobbered by a stray
            # constant default (which was turning a Delivery Channel/Method
            # CASE_WHEN into a constant "EMAIL" whenever source_column was null).
            if records is None:
                records = src[ctx_all].to_dict("records") if ctx_all else src.to_dict("records")
            src_vals = col_cache[m.source_column] if has_src else None
            col_values = [
                apply_pipeline(rules, (src_vals[i] if src_vals is not None else ""), row=records[i])
                for i in range(n_rows)
            ]
        elif has_src:
            src_vals = col_cache[m.source_column]
            col_values = [
                (dv if (v is None or str(v).strip() == "") and dv is not None else v)
                for v in src_vals
            ]
        else:
            col_values = [dv or ""] * n_rows
        out_cols[tgt.field_name] = col_values
        lineage[tgt.field_name] = {"source_column": m.source_column, "default_value": m.default_value,
                                   "rules": rules, "status": m.status, "confidence": m.confidence}
    return pd.DataFrame(out_cols), lineage


from app.services.merge_dedupe import merge_dedupe as _merge_dedupe  # noqa: E402


def _merge_dedupe_frames(frames: list[pd.DataFrame], target_object: str | None) -> pd.DataFrame:
    """Converge per-source converted frames into one, de-duplicated by the object's
    natural business key with source priority. Delegates to the unit-tested
    merge_dedupe module, passing the natural-key registry."""
    return _merge_dedupe(frames, target_object, REFERENCE_KEY_FIELDS)


async def build_converted_dataframe(
    conversion: Conversion, max_rows: int | None = None,
    collect_frames: dict | None = None,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """``collect_frames``: when a dict is passed, it is populated with
    ``{dataset_id: (converted_frame, source_columns)}`` for every bound source —
    BEFORE they are merged. Generation uses this to route each Oracle interface
    sheet to the source sheet that actually feeds it, which a merged frame cannot
    express when the sources have different row grains (e.g. 5,489 customers vs
    22,505 addresses in one workbook). All other callers ignore it and keep the
    existing merged-frame behaviour.
    """
    # Source rows come from the uploaded file(s) (dataset mode) or are streamed
    # live from Oracle EBS (EBS mode — no dataset). A module/target object can now
    # be fed by SEVERAL source files (priority order): each is converted with the
    # same mappings, then the converted frames are merged + de-duplicated into one.
    # ``max_rows`` caps the work for previews (only the shown rows are generated).
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

    # Memory: the source extract can be very wide (e.g. Customer is 234 columns),
    # but the transform only reads the columns that are actually mapped. Drop the
    # rest right after load so peak memory is bounded to the mapped slice — a wide
    # source held in full is what pushed the heavy Customer generate over the
    # free-tier limit (the request died at the gateway and the browser reported it
    # as a CORS error). Rule contexts reference source columns too, so keep any
    # column named by a mapping OR a transformation rule config.
    # Columns referenced by a seeded/applied transform (CASE_WHEN if_column,
    # CONCAT/COALESCE columns) arrive on the mapping's suggested_transformation and
    # may not be a mapped cell themselves — keep them so the derivation can read them.
    _ref_from_sugg: set[str] = set()
    for _m in mappings:
        _st = getattr(_m, "suggested_transformation", None)
        if _st:
            _ref_from_sugg |= _rule_referenced_columns(
                [{"rule_type": _st.get("rule_type"), "config": _st.get("config", {})}]
            )
    needed_src = {m.source_column for m in mappings if m.source_column} | _ref_from_sugg

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

    # Extra per-row context columns for rule evaluation: suggested-transform refs
    # plus any referenced by explicit transformation-rule pipelines.
    _ctx_cols: set[str] = set(_ref_from_sugg)
    for _rules in pipelines.values():
        _ctx_cols |= _rule_referenced_columns(_rules)

    # Order mappings by target field sequence once (metadata — cheap, row-count
    # independent). The heavy per-column transform runs on row CHUNKS in a worker
    # thread (asyncio.to_thread) so it never blocks the event loop and peak memory
    # stays bounded to one chunk. The transform is row-local, so chunk-then-concat
    # is byte-identical to a single pass.
    sorted_mappings = sorted(
        mappings,
        key=lambda m: (fields_by_id.get(m.target_field_id).sequence if fields_by_id.get(m.target_field_id) else 0),
    )

    async def _convert_source(src: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        """Prune to the mapped/referenced columns, then chunk-transform ONE source
        frame into the target field-keyed output frame."""
        try:
            if needed_src and len(src.columns) > len(needed_src) + 4:
                keep = [c for c in src.columns if c in needed_src]
                if keep:
                    src = src[keep].copy()
        except Exception:  # noqa: BLE001 — pruning is an optimization, never fatal
            pass
        n_total = len(src)
        if n_total <= _TRANSFORM_CHUNK_ROWS:
            return await asyncio.to_thread(
                _transform_frame, src, sorted_mappings, fields_by_id, pipelines, _ctx_cols)
        parts: list[pd.DataFrame] = []
        lin0: dict = {}
        for start in range(0, n_total, _TRANSFORM_CHUNK_ROWS):
            chunk = src.iloc[start:start + _TRANSFORM_CHUNK_ROWS]
            odf, lin = await asyncio.to_thread(
                _transform_frame, chunk, sorted_mappings, fields_by_id, pipelines, _ctx_cols)
            parts.append(odf)
            if not lin0:
                lin0 = lin
        return (pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]), lin0

    lineage: dict = {}
    source_ids = conversion.source_dataset_ids
    if source_ids:
        from app.services.dataset_file_store import materialize_dataset_file
        frames: list[pd.DataFrame] = []
        for did in source_ids:
            dataset = await Dataset.get(did)
            src_path = await materialize_dataset_file(dataset) if dataset else None
            if src_path is None:
                if len(source_ids) == 1:
                    raise ValueError("Dataset source file not found; please re-upload the dataset")
                continue  # skip an unreadable secondary source rather than fail the merge
            src = parse_tabular(str(src_path), file_type=dataset.file_type, nrows=max_rows)
            odf, lin = await _convert_source(src)
            frames.append(odf)
            if collect_frames is not None:
                # Keep the source's own column list: sheet routing decides which
                # source feeds an interface sheet by counting how many of that
                # sheet's mapped source columns this file actually contains.
                collect_frames[str(did)] = (odf, [str(c) for c in src.columns])
            if not lineage:
                lineage = lin
        if not frames:
            raise ValueError("No readable source files for this conversion")
        if len(frames) == 1:
            out_df = frames[0]
        else:
            # Multi-source: converge the per-source outputs, then de-duplicate by
            # the object's natural business key with SOURCE PRIORITY (earlier source
            # wins). Master objects (unique key per source) dedupe on the key; child
            # interfaces (many rows per entity) fall back to exact-row de-dup only.
            obj = (template.business_object if template else None) or conversion.target_object
            out_df = _merge_dedupe_frames(frames, obj)
    else:
        from app.services.mapping_service import ebs_fetch_rows
        table = getattr(conversion, "ebs_table_hint", "") or ""
        rows = await ebs_fetch_rows(table) if table else []
        src = pd.DataFrame(rows)
        if max_rows:
            src = src.head(max_rows)
        out_df, lineage = await _convert_source(src)

    # Coded (LOV) columns last, on the assembled (merged) frame so the audit counts
    # distinct values across the whole file. Row-local, so it stays chunk-safe.
    if fields:
        lov_report = await asyncio.to_thread(enforce_coded_values, out_df, fields)
        for fname, rep in lov_report.items():
            lineage.setdefault(fname, {})["lov"] = rep

    return out_df, lineage


# Coded-value (LOV) enforcement lives in lov_service alongside the code that mines
# the accepted values out of the template descriptions. Re-exported here because
# this is where it's applied.
from app.services.lov_service import enforce_coded_values  # noqa: E402


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


# Sentinel strings that legacy/SQL exports (SyteLine, NetSuite saved searches,
# etc.) write for "no value". Loaded verbatim into Oracle they'd become the
# literal text "NULL"/"N/A" instead of an empty cell, so blank them at generate.
_NULL_SENTINELS = {"null", "(null)", "#n/a", "n/a", "nan", "none", "\\n"}


def _blank_null_sentinels(df: pd.DataFrame) -> pd.DataFrame:
    """Replace whole-cell null sentinels (case-insensitive) with empty strings.
    Whole-cell match only, so a real value like a description containing the word
    is never touched."""
    for col in df.columns:
        s = df[col]
        if s.dtype != object:
            continue
        mask = s.astype(str).str.strip().str.lower().isin(_NULL_SENTINELS)
        if mask.any():
            df.loc[mask, col] = ""
    return df


def _dedup(cols: list[str]) -> list[str]:
    seen: set[str] = set()
    return [c for c in cols if not (c in seen or seen.add(c))]


# Supplier email masking: on a test / migration supplier load, real e-mail
# addresses in the file can make Oracle fire supplier/contact notifications. We
# neutralise them by prefixing "xx" (so "ap@x.com" -> "xxap@x.com", an invalid
# address that won't route). Applied to any email-named column of a Supplier
# object at generate. Idempotent (won't double-prefix) and skips blanks.
_SUPPLIER_EMAIL_PREFIX = "xx"


def _mask_supplier_emails(df: pd.DataFrame, prefix: str = _SUPPLIER_EMAIL_PREFIX) -> pd.DataFrame:
    for col in df.columns:
        key = re.sub(r"[^a-z0-9]", "", str(col).lower())
        if "email" not in key:
            continue
        s = df[col].astype(str)
        mask = s.str.strip().ne("") & ~s.str.strip().str.lower().isin(_NULL_SENTINELS) \
            & ~s.str.startswith(prefix)
        if mask.any():
            df.loc[mask, col] = prefix + s[mask]
    return df


def _safe_sheet_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "sheet"


# Supplier FBDI layout + Oracle file naming live in a pure, dependency-light
# module so they can be unit tested without the Beanie/Mongo stack. The generator
# just delegates to them (reorder to the analyst tab sequence + END terminator;
# zip/CSV names per the Tejaswi spec).
from app.services.supplier_fbdi_layout import (  # noqa: E402
    apply_supplier_layout as _supplier_layout,
    csv_name_for as _csv_name_for,
    zip_name_for as _zip_name_for,
)


# Control-field defaults + auto-numbered keys so generated files aren't blank in
# the columns Fusion requires — mirrors how a consultant fills the template
# (Import Action = CREATE, a batch id, a running supplier number, standard
# org/type values). Applied ONLY to columns the source left entirely blank.
_CONTROL_DEFAULTS: dict[str, str] = {
    # Applied by EXACT column name to blank columns only, so these are safe to
    # merge across objects (a supplier sheet has no "Transaction Type" column,
    # an item sheet has no "Supplier Type", etc.). Keys are lower-case with any
    # trailing "*" already stripped (matcher strips "*").
    # --- Supplier import (POZ_SUPPLIERS_INT) — analyst-confirmed lookup codes.
    # Oracle expects the internal code casing here, not the display label:
    # Tax Org Type = CORPORATION, Supplier Type = SUPPLIER, Business
    # Relationship = SPEND_AUTHORIZED (so sites/POs can transact immediately).
    "import action": "CREATE",
    "batch id": "900001",
    "tax organization type": "CORPORATION",
    "organization type": "CORPORATION",
    "supplier type": "SUPPLIER",
    "business relationship": "SPEND_AUTHORIZED",
    "federal reportable": "N",
    # NOTE: "delivery channel" is intentionally NOT a control default — it is
    # DERIVED per-row from the Email/Fax Transaction flags (CASE_WHEN: Email=Yes ->
    # EMAIL, Fax=Yes -> FAX, else blank). A blanket constant here was forcing every
    # row to EMAIL and overwriting the derivation.
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
    # "user account action" removed — it is VALUE-MAPPED per row from Login Access
    # (Yes -> CREATE_USER_ACCOUNT, No -> blank). A blanket constant "NONE" here was
    # forcing every row to NONE (authoritative overwrite) so "No" rendered as NONE.
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

# Sequence fields where a REAL source value must win over the running number.
# The analyst confirmed the supplier's legacy "Number" is the number of record,
# so when the source maps one in we keep it and only auto-number the blanks. The
# customer party/account keys stay authoritative sequences because the 19-sheet
# linkage glue references that generated running number.
_SEQ_PREFER_SOURCE: set[str] = {"suppliernumber", "supplierpartynumber"}

# Control fields that are CONSTANTS in the Oracle gold templates. These are set
# AUTHORITATIVELY — the standard value is written even if auto-map filled the
# column with a (usually wrong) source guess, e.g. Address Name / Supplier Site
# landing a street address or "No", or User Account Action getting a phone
# fragment. Excludes fields whose value is genuinely per-row (Business Unit,
# currency) or correctly source-mapped (names, addresses, tax ids, web site).
_AUTHORITATIVE: set[str] = {
    "import action", "batch id",
    "federal reportable",
    # "delivery channel" removed — it is per-row derived (Email/Fax -> EMAIL/FAX/
    # blank), not a forced constant.
    "pay", "ordering", "rfq or bidding",
    # "address name" and "supplier site" REMOVED from the authoritative set. The
    # signed NextPower Supplier Conversion Strategy (v1.0, section 7.2/7.3) states
    # Address Name = City Name (e.g. "Austin") and Supplier Site = BU + City
    # (e.g. "US-Austin"). Forcing the constant "PRIMARY" over a mapped city was
    # QA issue #8 — the UI showed the correct mapping and the generated file
    # contained PRIMARY. They stay in _CONTROL_DEFAULTS as a last-resort fill for
    # a column the source left completely empty, but they no longer overwrite.
    # "user account action" removed — per-row VALUE_MAP from Login Access must win
    # (No -> blank, not the forced constant "NONE").
}
# Tax Organization Type, Supplier Type and Business Relationship were previously
# forced constants. Per the analyst supplier mapping doc they are value-MAPPED from
# source (Entity Type, Category, Vendor Approval Status → e.g. Approved =
# SPEND_AUTHORIZED). So they are NO LONGER authoritative: when the source maps them
# (with a crosswalk) that value wins; the _CONTROL_DEFAULTS constant now only fills
# the column when the source left it entirely blank (a safe fallback).


def _apply_control_defaults(df: pd.DataFrame, seq_start: int = 100000,
                            suppressed: set | None = None,
                            effective: dict | None = None,
                            explicitly_mapped: set | None = None) -> pd.DataFrame:
    """``explicitly_mapped``: normalized field names the analyst deliberately bound
    to a real source column (curated seed, approved or overridden mapping). Those
    are never overwritten by an authoritative constant — QA issue #8, where the
    seeded eBOS ``Address Name <- city`` mapping was shown correctly in the UI but
    the generated file contained the forced constant ``PRIMARY``. Mirrors
    ``defaults_service._effective`` which already skips fields with a source column.
    """
    n = len(df)
    if n == 0:
        return df
    suppressed = suppressed or set()
    effective = effective or {}
    explicitly_mapped = explicitly_mapped or set()
    for col in df.columns:
        key = str(col).strip().lower().rstrip("*").strip()
        # The user's gold example / prompt marked this field as intentionally
        # blank — never fill it with a control default or authoritative constant.
        if key in suppressed:
            continue
        keyc = key.replace(" ", "")
        if keyc in _SEQ_FIELDS:
            if keyc in _SEQ_PREFER_SOURCE:
                # Supplier number: keep the mapped legacy "Number" where present,
                # only auto-number the rows the source left blank.
                cur = df[col].astype(str).str.strip()
                blanks = {"", "nan", "none", "null", "na", "<na>"}
                df[col] = [
                    cur.iat[i] if cur.iat[i].lower() not in blanks else str(seq_start + i)
                    for i in range(n)
                ]
            else:
                # Running key column — authoritative: Fusion needs a clean
                # sequential id, not whatever source column auto-map guessed.
                df[col] = [str(seq_start + i) for i in range(n)]
        elif key in _AUTHORITATIVE and key not in explicitly_mapped:
            # Gold-constant control field — write the standard value, overriding a
            # wrong AUTO-MAPPED source guess. Skipped when the analyst explicitly
            # bound this field to a source column (issue #8): a deliberate mapping
            # outranks the constant, and silently discarding it made the UI and the
            # generated file disagree.
            df[col] = _CONTROL_DEFAULTS[key]
        elif key in _AUTHORITATIVE and bool((df[col].astype(str).str.strip() == "").all()):
            # Explicitly mapped but the source produced nothing at all — fall back
            # to the standard constant rather than shipping an empty control column.
            df[col] = _CONTROL_DEFAULTS[key]
        elif key in _CONTROL_DEFAULTS and bool((df[col].astype(str).str.strip() == "").all()):
            # Other defaults only fill columns the source left entirely blank.
            df[col] = _CONTROL_DEFAULTS[key]
        elif key in effective and bool((df[col].astype(str).str.strip() == "").all()):
            # Effective defaults from defaults_service (curated control + learned
            # example defaults) — the SAME layer the Mapping Review UI and the
            # mapping export show. Fills blank, non-suppressed columns so the output
            # FBDI matches what the analyst sees (Issue #2: e.g. Include in Credit
            # Check / Credit Hold / Send Dunning Letters / Send Statement).
            df[col] = effective[key]
    return df


async def generate_output_artifact(conversion: Conversion, fmt: str = "csv",
                                   include_header: bool | None = None,
                                   merged_df: "pd.DataFrame | None" = None) -> ConvertedOutput:
    """``include_header``: None = auto, decided by FORMAT rather than by object —
    the filled Oracle Excel templates keep their column-label row, and CSV/zip
    output is headerless because that is what the FBDI loader expects (a header
    line is read as data and rejects the first record). True/False = the user's
    explicit toggle, forcing headers on/off for every sheet of this output.

    ``merged_df``: when provided, this ALREADY-BUILT frame is written instead of
    converting ``conversion``'s own source — used by the merge-by-interface path,
    where several per-source conversions are each converted and then merged into one
    frame that is finalized/written once (one file per interface)."""
    from app.models.fbdi import FBDISheet

    # HDL divert: HCM objects (Employee HDL) are not FBDI — they load as
    # pipe-delimited .dat files, so they go to the dedicated HDL generator instead
    # of the CSV/XLSX fan-out below. Detected by the template's business object.
    _tpl_for_route = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    try:
        from app.services.hdl_output_service import is_hdl_conversion, generate_hdl_artifact
    except ImportError:
        is_hdl_conversion = None  # type: ignore
    # Scope the import guard to the import only — a real error inside the HDL
    # generator must surface, not silently fall through to the FBDI path.
    if is_hdl_conversion and is_hdl_conversion(_tpl_for_route, conversion):
        return await generate_hdl_artifact(conversion)

    # How many target fields this object has — used to gate the two DB-heavy passes
    # below. A 19-sheet Customer/Item load has ~1200 fields; re-applying every
    # learned rule and re-capturing learnings across all of them on each generate is
    # hundreds of Mongo round-trips that made the request HANG on a small instance.
    _field_count = await FBDIField.find(
        FBDIField.template_id == conversion.template_id
    ).count() if conversion.template_id else 0
    _heavy = _field_count > 300

    # Force-apply the object's stored gold reference standard BEFORE building the
    # output, so the file reflects the learned mappings/defaults/suppressions even
    # if the conversion was mapped before gold was on file (fixes "gold saved but
    # not applied"). Skipped for heavy multi-sheet objects: their mappings already
    # carry the applied gold (it's applied at map time), and re-applying it here is
    # what made generation hang — the persisted mappings are the source of truth.
    if not _heavy:
        try:
            from app.services.learning_service import apply_learned_to_conversion
            _pre_maps = await MappingSuggestion.find(
                MappingSuggestion.conversion_id == conversion.id
            ).to_list()
            await apply_learned_to_conversion(conversion, _pre_maps, force=True)
        except Exception:
            pass  # best-effort — never block output generation on the learning pass
    # Per-source frames, kept UNMERGED so each Oracle interface sheet can be fed by
    # the source sheet that actually supplies it. Only populated when the conversion
    # is bound to more than one source (e.g. a Customer + Address workbook).
    _src_frames: dict = {}
    if merged_df is not None:
        df = merged_df
    else:
        df = (await build_converted_dataframe(conversion, collect_frames=_src_frames))[0]
    if len(_src_frames) < 2:
        _src_frames = {}          # single source — nothing to route
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None

    # Fetch fields (interface sequence) + sheets so we can emit exactly the
    # template's columns and, for multi-sheet workbooks, one file per sheet.
    fields = await FBDIField.find(
        FBDIField.template_id == template.id
    ).sort(+FBDIField.sequence).to_list() if template else []
    sheets = await FBDISheet.find(
        FBDISheet.template_id == template.id
    ).sort(+FBDISheet.sequence).to_list() if template else []

    # --- Generate-time data quality on the MERGED frame: cleanse + validate ---
    # Cleansing (universal trim + custom cleansing rules) is applied to df so the
    # written file carries the cleaned values; validation (built-in FBDI checks +
    # custom validation rules) produces an advisory issues report attached to the
    # artifact. Runs off the event loop; never blocks file generation.
    dq_report = None
    try:
        from app.services.generate_dq import apply_cleansing, validate_frame, build_report
        from app.services.dq_rule_service import load_rules
        from app.services.client_service import client_id_for_conversion
        _dq_obj = (template.business_object if template else None) or (conversion.target_object or "")
        _cid = await client_id_for_conversion(conversion)
        _cleanse_rules = await load_rules(_dq_obj, _cid, "cleansing")
        _val_rules = await load_rules(_dq_obj, _cid, "validation")
        df, _dq_fixes = await asyncio.to_thread(apply_cleansing, df, _cleanse_rules)
        _tf = [{"field_name": f.field_name, "required": bool(f.required),
                "data_type": f.data_type, "max_length": f.max_length,
                "format_mask": f.format_mask} for f in fields]
        _dq_issues = await asyncio.to_thread(validate_frame, df, _tf, _val_rules, 2000)
        dq_report = build_report(_dq_issues, _dq_fixes)
    except Exception as _dq_exc:  # noqa: BLE001 — DQ is advisory; never block generation
        import logging as _lg
        _lg.getLogger(__name__).exception("generate DQ step failed")
        # Surface the reason (diagnostic) instead of silently dropping the report.
        dq_report = {"error_count": 0, "warning_count": 0, "hard_error_count": 0,
                     "blocked": False, "cleansing_fix_count": 0, "cleansing_fixes": [],
                     "top_issues": [], "dq_error": f"{type(_dq_exc).__name__}: {_dq_exc}"[:300]}

    # Group fields by their interface sheet (preserving field sequence).
    fields_by_sheet: dict[Any, list] = {}
    for f in fields:
        fields_by_sheet.setdefault(f.sheet_id, []).append(f)
    sheets_with_fields = [s for s in sheets if s.id in fields_by_sheet]

    # Fields the user's gold example / prompt marked as intentionally blank
    # (a not_applicable mapping wins the dedup). These must stay empty in the
    # output — no control default, no authoritative constant.
    _all_maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conversion.id
    ).to_list()
    _SPRIO = {"overridden": 4, "approved": 3, "not_applicable": 2, "rejected": 1, "suggested": 0}
    _best_m: dict = {}
    for _m in _all_maps:
        _c = _best_m.get(_m.target_field_id)
        if _c is None or _SPRIO.get(_m.status or "suggested", 0) > _SPRIO.get(_c.status or "suggested", 0):
            _best_m[_m.target_field_id] = _m
    _fbyid = {f.id: f for f in fields}
    suppressed_keys = {
        _fbyid[tid].field_name.strip().lower().rstrip("*").strip()
        for tid, _m in _best_m.items()
        if _m.status == "not_applicable" and tid in _fbyid and _fbyid[tid].field_name
        # An explicit default_value on the field is intent to POPULATE it (e.g. an
        # analyst set Invoice Match Option = "Receipt"), so it must not be suppressed
        # even though the mapping is not_applicable (no source column).
        and not (getattr(_m, "default_value", None) and str(_m.default_value).strip())
    }
    # Fields the analyst deliberately bound to a source column. An authoritative
    # control constant must NOT overwrite these (issue #8: eBOS Address Name was
    # mapped to `city` in the UI but the file shipped the forced "PRIMARY").
    # "suggested" is excluded on purpose — that is auto-map guessing, which is
    # exactly what the authoritative constants exist to correct.
    _MAPPED_OK = {"approved", "overridden"}
    explicitly_mapped_keys = {
        _fbyid[tid].field_name.strip().lower().rstrip("*").strip()
        for tid, _m in _best_m.items()
        if tid in _fbyid and _fbyid[tid].field_name
        and (_m.source_column or "").strip()
        and (_m.status or "") in _MAPPED_OK
    }

    # Effective defaults (curated control + learned example defaults) — the SAME
    # layer the Mapping Review UI and the mapping export display. Applied to blank,
    # non-suppressed columns at finalize so the output FBDI matches what the analyst
    # sees (Issue #2). use_ai=False keeps generation off the model path (fast); the
    # reported fields are curated/control defaults, so they're covered.
    _eff_defaults: dict = {}
    try:
        from app.services.defaults_service import compute_effective_defaults
        _eff_defaults = (await compute_effective_defaults(conversion, use_ai=False)).get("defaults", {}) or {}
    except Exception:  # noqa: BLE001 — defaults are best-effort; never block generation
        _eff_defaults = {}

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

    # "template" output = the REAL Oracle FBDI workbook, filled in. Materialize the
    # bundled source file (disk, or rehydrated from Mongo after a redeploy). If the
    # template has no stored file we can't fill it, so degrade gracefully to a fresh
    # xlsx workbook rather than failing the whole generation.
    _template_src_path: str | None = None
    if fmt == "template":
        if template:
            try:
                from app.services.fbdi_service import materialize_template_file
                _p = await materialize_template_file(template)
                _template_src_path = str(_p) if _p else None
            except Exception:  # noqa: BLE001
                _template_src_path = None
        if not _template_src_path:
            fmt = "xlsx"  # no source workbook to populate — fall back

    # ── Per-interface-sheet source routing ──────────────────────────────────
    # One conversion, one FBDI bundle, even when the input spreads the object
    # across several worksheets. A merged frame cannot express this: a Customer
    # workbook has 5,489 party rows and 22,505 address rows, so the party sheets
    # and the address sheets need DIFFERENT row grains. For each interface sheet
    # we pick the bound source that supplies the most of that sheet's mapped
    # source columns; ties and no-evidence cases fall back to the merged frame,
    # which is exactly the previous behaviour.
    _src_by_field: dict = {}
    for _m in _all_maps:
        if _m.source_column:
            _src_by_field.setdefault(_m.target_field_id, str(_m.source_column))

    def _frame_for(sfields: list) -> pd.DataFrame:
        if not _src_frames:
            return df
        wanted = {_src_by_field.get(f.id) for f in sfields}
        wanted.discard(None)
        if not wanted:
            return df
        best, best_hits = None, 0
        for _did, (_odf, _cols) in _src_frames.items():
            have = {c.strip().lower() for c in _cols}
            hits = sum(1 for w in wanted if w.strip().lower() in have)
            if hits > best_hits:
                best, best_hits = _odf, hits
        return best if best is not None and best_hits > 0 else df

    def _finalize(sfields: list) -> pd.DataFrame:
        # Req 8 — exactly this sheet's interface columns, in sequence, blanks
        # where unmapped, no instruction rows. Data ops (date reformat, control
        # defaults) run while columns are still keyed by cleaned field_name; the
        # LAST step renames columns to Oracle's exact header labels (with the
        # '*' required markers) so the file matches the shipped template.
        cols = _dedup([f.field_name for f in sfields])
        sdf = _frame_for(sfields).reindex(columns=cols, fill_value="")
        # Blank legacy null sentinels BEFORE control defaults so a column the
        # source filled entirely with "NULL" is treated as empty and gets its
        # standard default, not the literal text.
        sdf = _blank_null_sentinels(sdf)
        sdf = _format_date_columns(sdf, sfields)
        sdf = _apply_control_defaults(sdf, suppressed=suppressed_keys, effective=_eff_defaults,
                                      explicitly_mapped=explicitly_mapped_keys)
        # Supplier safety: neutralise e-mail columns so a migration/test load can't
        # trigger real supplier notifications. Runs while columns are still keyed by
        # field_name (before the header rename below).
        if _is_supplier:
            sdf = _mask_supplier_emails(sdf)
        hdr: dict[str, str] = {}
        for f in sfields:
            hdr.setdefault(f.field_name, _header_label(f))
        sdf.columns = [hdr.get(c, c) for c in sdf.columns]
        return sdf

    # Customer Import is a linked 19-table load: children point at parents through
    # the Source System + Source System Reference columns, which no source extract
    # provides. Generate that glue (batch id, ORIG_SYSTEM keys, -1/blank sentinels
    # per level) across the interface sheets — the same structural filling the
    # Supplier fan-out already does.
    _is_customer = ("customer" in (obj_name or "").lower()) or any(
        "hzimp" in _safe_sheet_name(s.sheet_name).lower().replace("_", "")
        for s in sheets_with_fields
    )
    # Item Import (Product Hub) is the same shape: ONE backbone interface table
    # (EGP_SYSTEM_ITEMS_INTERFACE) plus ~16 optional child tables (revisions,
    # categories, relationships, supplier, cost, EFF…) that the gold leaves blank
    # unless the source genuinely carries that content. Same over-population rule.
    _is_item = ("item" in (obj_name or "").lower()) or any(
        "egpsystemitems" in _safe_sheet_name(s.sheet_name).lower().replace("_", "")
        for s in sheets_with_fields
    )
    # Supplier object (Import / Address / Site / Contacts / Banks) — drives the
    # e-mail masking in _finalize. Matches the object name or a POZ_/supplier sheet.
    _is_supplier = ("supplier" in (obj_name or "").lower()) or any(
        _safe_sheet_name(s.sheet_name).lower().startswith("poz_")
        or "supplier" in _safe_sheet_name(s.sheet_name).lower()
        for s in sheets_with_fields
    )

    # Shared customer linkage config (source system code + batch), and a reference
    # series derived ONCE from the party sheet so every interface table links
    # consistently. Computed up front so we can then process sheets one at a time.
    _cust_src = str(
        getattr(conversion, "source_system_code", None)
        or getattr(conversion, "source_erp", None)
        or "LEGACY"
    ).upper().replace(" ", "_")
    _cust_batch = f"CONV-{str(conversion.id)[-6:].upper()}"

    def _cust_ref() -> list:
        if not _is_customer or not sheets_with_fields:
            return []
        try:
            from app.services.customer_structure_service import reference_series
            party = next((s for s in sheets_with_fields
                          if "parties" in _safe_sheet_name(s.sheet_name).lower()), None)
            base = _finalize(fields_by_sheet[party.id]) if party else _finalize(
                fields_by_sheet[sheets_with_fields[0].id])
            n = len(base)
            ref = reference_series(base, n, _cust_src)
            del base
            return ref
        except Exception:  # noqa: BLE001
            return []

    def _cust_apply(frame) -> None:
        if not _is_customer or not _ref_cache:
            return
        try:
            from app.services.customer_structure_service import apply_to_frame
            apply_to_frame(frame, source_system=_cust_src, batch_id=_cust_batch,
                           ref=_ref_cache, level="account")
        except Exception:  # noqa: BLE001
            pass

    _ref_cache: list = []

    # Gap-1: which interface sheets should carry DATA rows. A naive fan-out wrote
    # 5,599 rows into ALL 19 sheets, so optional entity tables (Contacts,
    # Relationships, Classifications, Pay Method…) got thousands of rows of pure
    # linkage glue with no real content — junk Oracle would reject. Rule, matching
    # the gold file: the party/account/site/profile BACKBONE always emits; every
    # other sheet emits only when a real source column is actually mapped into it.
    # Suppressed sheets are still written as headers-only (0 rows) so the file set
    # stays complete, exactly like the empty tabs in a hand-filled template.
    _CUST_BACKBONE = {
        "hzimppartiest", "hzimppartysitest", "hzimppartysiteusest", "hzimplocationst",
        "hzimpaccountst", "hzimpacctsitest", "hzimpacctsiteusest", "racustomerprofilesintall",
    }
    _ITEM_BACKBONE = {"egpsystemitemsinterface"}
    # The backbone for THIS object — the sheet(s) that always carry data. Only
    # the linked Customer/Item fan-outs suppress their optional child sheets;
    # every other object writes all its sheets exactly as before.
    _backbone_keys = _CUST_BACKBONE if _is_customer else (_ITEM_BACKBONE if _is_item else set())
    _suppress_optional = _is_customer or _is_item
    _mapped_field_ids = {
        tid for tid, m in _best_m.items()
        if getattr(m, "source_column", None) and (m.status not in ("not_applicable", "rejected"))
    }

    def _sheet_carries_data(s) -> bool:
        if not _suppress_optional:
            return True
        key = re.sub(r"[^a-z0-9]", "", (s.sheet_name or "").lower())
        if key in _backbone_keys:
            return True
        # An optional child table emits data only when a real source column is
        # actually mapped into it; otherwise it's written headers-only (empty tab).
        return any(f.id in _mapped_field_ids for f in fields_by_sheet.get(s.id, []))

    def _headers_only(sfields: list) -> pd.DataFrame:
        cols = _dedup([_header_label(f) for f in sfields])
        return pd.DataFrame(columns=cols)

    def _apply_supplier_layout(sdf: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
        # Delegate to the pure, unit-tested module (reorder to the analyst tab
        # sequence + END terminator); no-op for non-supplier objects.
        return _supplier_layout(sdf, sheet_name, _is_supplier)

    def _write_all() -> tuple[str, str, int, int]:
        """Serialize to disk, STREAMING one interface sheet at a time so peak memory
        stays at ~one sheet — not all 19 at once, which OOM'd the worker on a large
        Customer load. Runs in a worker thread so it never blocks the event loop.
        CSV (the bundle default, and the format a real FBDI load ingests) writes one
        file per sheet into a .zip; XLSX keeps the shipped populated-template format.
        """
        nonlocal _ref_cache
        total_rows = 0
        total_cols = 0
        multi = bool(sheets_with_fields)
        _ref_cache = _cust_ref()
        # Header row inclusion. The user toggle always wins. Otherwise the default
        # follows the FORMAT, not the object: an Excel/filled-template download is
        # for humans and keeps its header row, while CSV (and the zipped CSV
        # bundle) is the machine-readable FBDI load file and must be headerless —
        # Oracle reads a header line as a data row and rejects it. Previously this
        # keyed off _is_supplier, so non-supplier objects shipped CSVs WITH a
        # header and had to be hand-edited before loading.
        _hdr = include_header if include_header is not None else fmt in ("xlsx", "template")

        # Filled Oracle template: write each sheet's finalized frame INTO the real
        # bundled workbook (macros/instructions/formatting preserved), clearing the
        # shipped sample rows. No supplier reorder/END here — the Oracle template
        # already defines the column order and has no END terminator row; the
        # analyst runs the template's own CSV-generation macro for the load files.
        if fmt == "template" and _template_src_path:
            from app.services.template_fill_service import fill_template
            frames: dict[str, pd.DataFrame] = {}
            if multi:
                for s in sheets_with_fields:
                    if _sheet_carries_data(s):
                        sdf = _finalize(fields_by_sheet[s.id])
                        _cust_apply(sdf)
                    else:
                        sdf = _headers_only(fields_by_sheet[s.id])
                    frames[s.sheet_name] = sdf
                    total_rows = max(total_rows, len(sdf))
                    total_cols += len(sdf.columns)
            else:
                fdf = _finalize(fields) if fields else _apply_control_defaults(df)
                # single-sheet: key by the only data sheet's name if known
                _only = sheets_with_fields[0].sheet_name if sheets_with_fields else obj_name
                frames[_only] = fdf
                total_rows, total_cols = len(fdf), len(fdf.columns)
            _data = fill_template(_template_src_path, frames)
            _stem = Path(_template_src_path).stem
            name = f"{_stem}.xlsm" if _template_src_path.lower().endswith(".xlsm") else f"{_stem}.xlsx"
            path = out_dir / name
            path.write_bytes(_data)
            return name, str(path), total_rows, total_cols

        if fmt == "xlsx":
            name = f"{obj_name}_{ts}.xlsx"
            path = out_dir / name
            with pd.ExcelWriter(path, engine="openpyxl") as xw:
                if multi:
                    for s in sheets_with_fields:
                        if _sheet_carries_data(s):
                            sdf = _finalize(fields_by_sheet[s.id])
                            _cust_apply(sdf)
                        else:
                            sdf = _headers_only(fields_by_sheet[s.id])
                        sdf = _apply_supplier_layout(sdf, s.sheet_name)
                        sdf.to_excel(xw, index=False, header=_hdr, sheet_name=_safe_sheet_name(s.sheet_name)[:31])
                        total_rows = max(total_rows, len(sdf))
                        total_cols += len(sdf.columns)
                        del sdf
                else:
                    fdf = _finalize(fields) if fields else _apply_control_defaults(df)
                    fdf.to_excel(xw, index=False, header=_hdr, sheet_name=(_safe_sheet_name(obj_name)[:31] or "Sheet1"))
                    total_rows, total_cols = len(fdf), len(fdf.columns)
            return name, str(path), total_rows, total_cols

        # CSV
        # Supplier FBDI load package (analyst / Tejaswi spec): HEADERLESS CSVs
        # (data rows only, each terminated with END), columns in the tab's
        # extraction order, each file named for its interface and packaged in a
        # zip named for the entity — even a single-sheet interface gets a zip.
        _primary = sheets_with_fields[0] if sheets_with_fields else None
        _zbase = _zip_name_for(_primary.sheet_name) if _primary else None
        if _is_supplier and _zbase:
            import zipfile as _zip
            name = f"{_zbase}.zip"
            path = out_dir / name
            with _zip.ZipFile(path, "w", _zip.ZIP_DEFLATED) as zf:
                for s in sheets_with_fields:
                    if _sheet_carries_data(s):
                        sdf = _finalize(fields_by_sheet[s.id])
                        _cust_apply(sdf)
                    else:
                        sdf = _headers_only(fields_by_sheet[s.id])
                    sdf = _apply_supplier_layout(sdf, s.sheet_name)
                    cbase = _csv_name_for(s.sheet_name)
                    # Oracle FBDI CSVs are headerless by default (data only, END
                    # last); the user's Include-header toggle can force headers on.
                    zf.writestr(f"{cbase}.csv", sdf.to_csv(index=False, header=_hdr))
                    total_rows = max(total_rows, len(sdf))
                    total_cols += len(sdf.columns)
                    del sdf
            return name, str(path), total_rows, total_cols

        if multi and len(sheets_with_fields) > 1:
            import zipfile as _zip
            name = f"{obj_name}_{ts}.zip"
            path = out_dir / name
            with _zip.ZipFile(path, "w", _zip.ZIP_DEFLATED) as zf:
                for i, s in enumerate(sheets_with_fields, 1):
                    if _sheet_carries_data(s):
                        sdf = _finalize(fields_by_sheet[s.id])
                        _cust_apply(sdf)
                    else:
                        sdf = _headers_only(fields_by_sheet[s.id])
                    sdf = _apply_supplier_layout(sdf, s.sheet_name)
                    zf.writestr(f"{i:02d}_{_safe_sheet_name(s.sheet_name)}.csv", sdf.to_csv(index=False, header=_hdr))
                    total_rows = max(total_rows, len(sdf))
                    total_cols += len(sdf.columns)
                    del sdf
            return name, str(path), total_rows, total_cols

        sfields = fields_by_sheet[sheets_with_fields[0].id] if multi else fields
        fdf = _finalize(sfields) if sfields else _apply_control_defaults(df)
        _sname = (sheets_with_fields[0].sheet_name if multi else obj_name)
        fdf = _apply_supplier_layout(fdf, _sname)
        name = f"{obj_name}_{ts}.csv"
        path = out_dir / name
        fdf.to_csv(path, index=False, header=_hdr)
        return name, str(path), len(fdf), len(fdf.columns)

    out_name, out_path_str, total_rows, total_cols = await asyncio.to_thread(_write_all)
    out_path = Path(out_path_str)
    artefact = ConvertedOutput(
        conversion_id=conversion.id, output_file_path=str(out_path),
        output_file_name=out_name, row_count=total_rows, column_count=total_cols,
        status="generated", dq_report=dq_report,
    )
    await artefact.insert()
    await conversion.set({"status": "output_generated", "updated_at": datetime.utcnow()})
    # Learning capture: persist the trustworthy mappings/defaults as reusable
    # object-level learnings (best-effort). Skipped for heavy multi-sheet objects —
    # capturing across ~1200 fields is hundreds of Mongo upserts per generate and
    # contributes nothing to the file that was just written; it's what made the
    # request hang. Their learnings are already captured when gold is applied.
    if not _heavy:
        try:
            from app.services.learning_service import capture_learnings_from_conversion
            await capture_learnings_from_conversion(conversion)
        except Exception:
            pass
    return artefact


async def build_merged_frame_for_object(project_id, target_object: str, max_rows: int | None = None):
    """Convert EVERY bound conversion in the project that targets ``target_object``
    (each with its OWN mapping — so heterogeneous sources like eBOS + NetSuite map
    correctly), then converge them into one frame via survivorship de-dup. Returns
    (merged_df, carrier_conversion, source_names). carrier is used only for the
    shared template/sheets/naming (all conversions of one object share a template)."""
    from beanie import PydanticObjectId as _OID
    from app.models.conversion import Conversion as _Conv
    from app.models.dataset import Dataset as _DS
    convs = await _Conv.find(
        _Conv.project_id == _OID(str(project_id)),
        _Conv.target_object == target_object,
    ).sort(+_Conv.planned_load_order).to_list()
    convs = [c for c in convs if c.template_id and (c.source_dataset_ids or
             getattr(c, "source_type", "") == "ebs")]
    if not convs:
        return None, None, []
    frames, names = [], []
    for c in convs:
        try:
            f, _ = await build_converted_dataframe(c, max_rows=max_rows)
        except Exception:  # noqa: BLE001 — skip an unreadable source, keep the rest
            continue
        if f is not None and len(f.columns):
            frames.append(f)
            for did in c.source_dataset_ids:
                ds = await _DS.get(did)
                if ds:
                    names.append(ds.name)
    if not frames:
        return None, convs[0], names
    merged = _merge_dedupe(frames, target_object, REFERENCE_KEY_FIELDS) if len(frames) > 1 else frames[0]
    return merged, convs[0], names


async def generate_merged_artifact(project_id, target_object: str, fmt: str = "csv",
                                   include_header: bool | None = None) -> ConvertedOutput:
    """Merge all per-source conversions for one interface object into ONE output
    file (merged + de-duplicated + cleansed + validated), written under the carrier
    conversion's artifact."""
    merged, carrier, _names = await build_merged_frame_for_object(project_id, target_object)
    if carrier is None:
        raise ValueError(f"No bound conversions for {target_object!r} in this project")
    if merged is None:
        merged = (await build_converted_dataframe(carrier))[0]
    return await generate_output_artifact(carrier, fmt=fmt, include_header=include_header, merged_df=merged)


async def get_output_preview(conversion: Conversion, limit: int = 50) -> dict[str, Any]:
    # Only generate the rows we actually show — previews were converting the whole
    # file (tens of thousands of rows) just to display 50.
    df, lineage = await build_converted_dataframe(conversion, max_rows=limit)
    head = df.head(limit)
    # Mirror the finalize-stage supplier e-mail masking here so the PREVIEW matches
    # the downloaded FBDI file. Generation applies the "xx" e-mail mask inside
    # _finalize (which the preview path skips), so without this the preview showed
    # raw e-mails even though the real output is masked — misleading anyone checking
    # the "don't trigger supplier notifications" rule from the preview.
    try:
        _tpl = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
        _obj = ((_tpl.business_object if _tpl else None)
                or getattr(conversion, "target_object", "") or "")
        if "supplier" in _obj.lower():
            head = _mask_supplier_emails(head.copy())
    except Exception:  # noqa: BLE001 — preview must never fail on a cosmetic mask
        pass
    total = int(len(df))
    if conversion.dataset_id:
        ds = await Dataset.get(conversion.dataset_id)
        if ds and ds.row_count:
            total = int(ds.row_count)  # true dataset size, not the capped preview
    return {"columns": list(head.columns.astype(str)), "rows": head.fillna("").to_dict(orient="records"),
            "total_rows": total, "lineage": lineage}


async def preload_report(conversion: Conversion, sample_rows: int = 3000) -> dict[str, Any]:
    """Predictive pre-load check: build the merged converted frame and validate it
    (built-in FBDI checks + custom rules) WITHOUT writing a file, returning a
    plain-English 'what Oracle will reject and how to fix' report. Read-only."""
    from app.services.generate_dq import apply_cleansing, validate_frame, build_report, explain_report
    from app.services.dq_rule_service import load_rules
    from app.services.client_service import client_id_for_conversion
    df, _ = await build_converted_dataframe(conversion, max_rows=sample_rows)
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    fields = await FBDIField.find(FBDIField.template_id == template.id).to_list() if template else []
    obj = (template.business_object if template else None) or (conversion.target_object or "")
    cid = await client_id_for_conversion(conversion)
    cleanse_rules = await load_rules(obj, cid, "cleansing")
    val_rules = await load_rules(obj, cid, "validation")
    df2, fixes = await asyncio.to_thread(apply_cleansing, df, cleanse_rules)
    tf = [{"field_name": f.field_name, "required": bool(f.required), "data_type": f.data_type,
           "max_length": f.max_length, "format_mask": f.format_mask} for f in fields]
    issues = await asyncio.to_thread(validate_frame, df2, tf, val_rules, sample_rows)
    report = build_report(issues, fixes)
    report = explain_report(report)
    report["sampled_rows"] = int(len(df2))
    report["target_object"] = obj
    return report


async def get_output_preview_by_source(conversion: Conversion, limit: int = 50) -> dict[str, Any]:
    """Per-source converted preview: convert EACH source file individually (the same
    way it will be converted before the merge) and return one preview block per
    source. The single merged/de-duplicated output is only produced at Generate; this
    lets the analyst see what each source contributes. Falls back to one block for a
    single-source or EBS conversion."""
    ids = conversion.source_dataset_ids
    _tpl = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    _obj = ((_tpl.business_object if _tpl else None) or getattr(conversion, "target_object", "") or "")
    is_supplier = "supplier" in _obj.lower()

    if len(ids) <= 1:
        p = await get_output_preview(conversion, limit=limit)
        nm = None
        if ids:
            ds = await Dataset.get(ids[0])
            nm = ds.name if ds else None
        return {"multi": False, "sources": [{"source_id": (str(ids[0]) if ids else None),
                                             "source_name": nm, **p}]}

    # Convert each source in isolation by temporarily pointing the (in-memory only)
    # conversion at one dataset at a time. Never persisted; restored in finally.
    orig_ids = list(conversion.dataset_ids)
    orig_single = conversion.dataset_id
    sources: list[dict] = []
    try:
        for did in ids:
            conversion.dataset_ids = [did]
            conversion.dataset_id = did
            df, lineage = await build_converted_dataframe(conversion, max_rows=limit)
            head = df.head(limit)
            if is_supplier:
                try:
                    head = _mask_supplier_emails(head.copy())
                except Exception:  # noqa: BLE001
                    pass
            ds = await Dataset.get(did)
            total = int(ds.row_count) if (ds and ds.row_count) else int(len(df))
            sources.append({
                "source_id": str(did), "source_name": ds.name if ds else None,
                "columns": list(head.columns.astype(str)),
                "rows": head.fillna("").to_dict(orient="records"),
                "total_rows": total, "lineage": lineage,
            })
    finally:
        conversion.dataset_ids = orig_ids
        conversion.dataset_id = orig_single
    return {"multi": True, "sources": sources}
