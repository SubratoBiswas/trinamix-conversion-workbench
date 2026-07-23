"""Mapping suggestion endpoints."""
import logging
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger(__name__)
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.mapping import MappingSuggestion
from app.models.transformation import Crosswalk, TransformationRule
from app.models.user import User
from app.parsers import parse_tabular
from app.schemas.mapping import MappingOut, MappingUpdate
from app.schemas.transformation import TransformationRuleCreate, TransformationRuleOut
from app.services.auth_service import get_current_user
from app.services.learning_service import record_learning_from_mapping, record_learning_from_rule
from app.services.mapping_service import enrich_mapping_with_samples, run_mapping_suggestions
from app.transformations.engine import apply_pipeline

router = APIRouter(prefix="/api", tags=["mapping"])


async def _require_conversion(conversion_id: str) -> Conversion:
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    is_ebs = getattr(c, "source_type", "dataset") == "ebs"
    if not c.template_id or (not is_ebs and not c.dataset_id):
        raise HTTPException(400, "Conversion needs both dataset and template bound first.")
    return c


@router.post("/conversions/{conversion_id}/suggest-mapping", response_model=list[MappingOut])
async def suggest_mapping(conversion_id: str, _: User = Depends(get_current_user)):
    conv = await _require_conversion(conversion_id)
    saved = await run_mapping_suggestions(conv)
    return await enrich_mapping_with_samples(conv, saved)


@router.get("/conversions/{conversion_id}/mapping-candidates")
async def mapping_candidates_endpoint(
    conversion_id: str,
    top_n: int = 5,
    target_field_id: str | None = None,
    _: User = Depends(get_current_user),
):
    """Alternative source-column candidates per target field (ranked), so the
    reviewer can see other columns the AI could map each target to, and swap."""
    from app.services.mapping_service import mapping_candidates
    conv = await _require_conversion(conversion_id)
    top_n = max(1, min(top_n, 10))
    return await mapping_candidates(conv, top_n=top_n, target_field_id=target_field_id)


@router.post("/conversions/{conversion_id}/vet-candidates")
async def vet_candidates_endpoint(
    conversion_id: str,
    payload: dict | None = None,
    top_n: int = 4,
    only_uncertain: bool = True,
    _: User = Depends(get_current_user),
):
    """Add an AI verdict + plain-English reason to each candidate — on demand, so
    an analyst can ask "why these options?" without paying the cost on every load.

    Body may carry {"target_field_ids": [...]} to vet only the rows on screen; this
    is how a wide template (1000+ fields) stays inside the request budget. Falls
    back to the deterministic guard's reasons when AI is unavailable."""
    from app.services.candidate_vetting_service import vet_conversion_candidates
    conv = await _require_conversion(conversion_id)
    top_n = max(1, min(top_n, 8))
    tfids = (payload or {}).get("target_field_ids") or None
    force = bool((payload or {}).get("force"))
    return await vet_conversion_candidates(
        conv, top_n=top_n, only_uncertain=only_uncertain,
        target_field_ids=tfids, force=force)


@router.get("/conversions/{conversion_id}/mappings", response_model=list[MappingOut])
async def list_mappings(conversion_id: str, _: User = Depends(get_current_user)):
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    items = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == PydanticObjectId(conversion_id)
    ).to_list()
    if not items:
        return []
    return await enrich_mapping_with_samples(conv, items)


@router.get("/conversions/{conversion_id}/source-columns")
async def source_columns(conversion_id: str, _: User = Depends(get_current_user)):
    """Unified source-column list for the Mapping Review canvas.

    Works for both source modes:
      * dataset mode (``dataset_id`` set) → profiled columns from the upload
      * EBS live mode (``dataset_id`` is null) → live ``ALL_TAB_COLUMNS``
        metadata for the conversion's ``ebs_table_hint``.

    Returns column dicts shaped like the frontend ``DatasetColumnProfile`` so
    the canvas can render identically regardless of source.
    """
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")

    # UI rule (mirrors ConversionDetailPage): dataset_id presence — not
    # source_type — decides which source the canvas shows.
    is_ebs = not conv.dataset_id
    columns: list[dict[str, Any]] = []
    debug: dict[str, Any] | None = None

    if is_ebs:
        from app.services.mapping_service import _ebs_columns_with_diag
        table = getattr(conv, "ebs_table_hint", "") or ""
        if table:
            srcs, debug = await _ebs_columns_with_diag(table)
        else:
            srcs, debug = [], {"stage": "no_table_hint", "table": None}
        for i, s in enumerate(srcs):
            columns.append({
                "id": i + 1,
                "column_name": s.name,
                "position": i,
                "inferred_type": s.inferred_type,
                "null_count": 0,
                "null_percent": s.null_percent,
                "distinct_count": s.distinct_count,
                "sample_values": s.sample_values,
                "min_value": None,
                "max_value": None,
                "pattern_summary": s.pattern_summary,
                "contains_pii": None,
                "pii_category": None,
            })
    else:
        from app.models.dataset import DatasetColumnProfile
        profs = await DatasetColumnProfile.find(
            DatasetColumnProfile.dataset_id == conv.dataset_id
        ).sort(+DatasetColumnProfile.position).to_list()
        for p in profs:
            columns.append({
                "id": int(str(p.id)[-8:], 16) if not isinstance(p.id, int) else p.id,
                "column_name": p.column_name,
                "position": p.position,
                "inferred_type": p.inferred_type,
                "null_count": getattr(p, "null_count", 0) or 0,
                "null_percent": p.null_percent or 0.0,
                "distinct_count": p.distinct_count or 0,
                "sample_values": p.sample_values or [],
                "min_value": getattr(p, "min_value", None),
                "max_value": getattr(p, "max_value", None),
                "pattern_summary": p.pattern_summary,
                "contains_pii": getattr(p, "contains_pii", None),
                "pii_category": getattr(p, "pii_category", None),
            })

    return {
        "source_type": "ebs" if is_ebs else "dataset",
        "table": getattr(conv, "ebs_table_hint", None) if is_ebs else None,
        "columns": columns,
        "debug": debug,
    }


@router.put("/mappings/{mapping_id}", response_model=MappingOut)
async def update_mapping(
    mapping_id: str, payload: MappingUpdate, user: User = Depends(get_current_user)
):
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("status") == "approved":
        data["approved_by"] = user.email
        data["approved_at"] = datetime.utcnow()
    await m.set(data)
    conv = await Conversion.get(m.conversion_id)
    if m.status in ("approved", "overridden") and m.source_column:
        await record_learning_from_mapping(m, conv, captured_by=user.email)
    return (await enrich_mapping_with_samples(conv, [m]))[0]


class ValueMapPair(BaseModel):
    source_value: str
    target_value: str


class ValueMapAccept(BaseModel):
    pairs: list[ValueMapPair]
    default_value: Optional[str] = None


@router.get("/mappings/{mapping_id}/value-map-recommendations")
async def value_map_recommendations(mapping_id: str, _: User = Depends(get_current_user)):
    """AI-recommended source→target value pairs (crosswalk) for one mapping.

    Compares the distinct values in the mapped source column against the
    target FBDI field's list of values using exact / meaning / synonym /
    fuzzy resolution plus previously learned crosswalks. Unresolved source
    values are returned as exceptions for manual mapping.
    """
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    conv = await Conversion.get(m.conversion_id)
    if not conv:
        raise HTTPException(404, "Conversion not found")
    from app.services.value_mapping_service import recommend_value_map
    return await recommend_value_map(conv, m)


@router.post("/mappings/{mapping_id}/value-map-accept")
async def value_map_accept(
    mapping_id: str, body: ValueMapAccept, user: User = Depends(get_current_user)
):
    """Persist accepted value pairs: creates/merges a VALUE_MAP transformation
    rule on the target field (applied at output generation) and learns each
    pair into the Crosswalk Library for reuse on future conversions."""
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    conv = await Conversion.get(m.conversion_id)
    if not conv:
        raise HTTPException(404, "Conversion not found")
    from app.services.value_mapping_service import accept_value_map
    result = await accept_value_map(
        conv, m,
        pairs=[p.model_dump() for p in body.pairs],
        default_value=body.default_value,
        user_email=user.email,
    )
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


@router.put("/mappings/{mapping_id}/approve", response_model=MappingOut)
async def approve_mapping(mapping_id: str, user: User = Depends(get_current_user)):
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    await m.set({"status": "approved", "approved_by": user.email, "approved_at": datetime.utcnow()})
    conv = await Conversion.get(m.conversion_id)
    if m.source_column:
        await record_learning_from_mapping(m, conv, captured_by=user.email)
        from app.services.learning_service import propagate_rules_to_downstream
        await propagate_rules_to_downstream(conv, m)
    return (await enrich_mapping_with_samples(conv, [m]))[0]


@router.post("/conversions/{conversion_id}/rules", response_model=TransformationRuleOut)
async def add_rule(
    conversion_id: str, payload: TransformationRuleCreate, user: User = Depends(get_current_user)
):
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    seq = await TransformationRule.find(
        TransformationRule.conversion_id == PydanticObjectId(conversion_id)
    ).count()
    data = payload.model_dump()
    # target_field_id arrives as a string ObjectId (or, from a buggy older UI,
    # possibly junk). Coerce safely — never let a bad id 500 the whole save.
    if data.get("target_field_id"):
        try:
            data["target_field_id"] = PydanticObjectId(str(data["target_field_id"]))
        except Exception:
            data["target_field_id"] = None
    r = TransformationRule(conversion_id=conv.id, sequence=seq, **data)
    await r.insert()
    # Learning capture is best-effort — a failure here must not fail the save
    # (and previously surfaced as an opaque "Failed to save rule" with no CORS).
    try:
        await record_learning_from_rule(r, conv, captured_by=user.email)
    except Exception as exc:
        log.warning(f"add_rule: learning capture failed for rule {r.id}: {exc}")
    # Serialize explicitly so ObjectId fields become strings (model_dump leaves
    # target_field_id as an ObjectId, which fails TransformationRuleOut).
    return {
        "id": str(r.id),
        "conversion_id": str(r.conversion_id),
        "target_field_id": str(r.target_field_id) if r.target_field_id else None,
        "source_column": r.source_column,
        "rule_type": r.rule_type,
        "rule_config": r.rule_config or {},
        "description": r.description,
        "sequence": r.sequence,
        "created_at": r.created_at,
    }


class PreviewRule(BaseModel):
    rule_type: str
    config: dict[str, Any] = {}


class PreviewRequest(BaseModel):
    rules: list[PreviewRule]
    source_column: Optional[str] = None
    sample_size: int = 5


class PreviewSample(BaseModel):
    source: Any
    output: Any
    error: Optional[str] = None


class PreviewResponse(BaseModel):
    samples: list[PreviewSample]


@router.post("/conversions/{conversion_id}/rules/preview", response_model=PreviewResponse)
async def preview_rules(
    conversion_id: str, payload: PreviewRequest, user: User = Depends(get_current_user)
):
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")

    cws = await Crosswalk.find(
        Crosswalk.conversion_id == PydanticObjectId(conversion_id)
    ).to_list()
    crosswalks: dict[str, dict[str, str]] = {}
    for cw in cws:
        crosswalks.setdefault(cw.name, {})[cw.source_value] = cw.target_value

    rules = [{"rule_type": r.rule_type, "config": r.config} for r in payload.rules]
    n = max(1, min(int(payload.sample_size), 20))

    # Sample rows come from the uploaded file (dataset mode) or live Oracle EBS
    # (EBS mode — no file). Either way we never 404: a rule preview should work
    # the moment a source is bound.
    sample_rows: list[dict[str, Any]] = []
    if conv.dataset_id:
        ds = await Dataset.get(conv.dataset_id)
        if ds:
            # Rehydrate from GridFS — the container disk is ephemeral, so
            # ds.file_path may not exist after a redeploy (reading it raised and
            # the request died, surfacing in the UI as a bare "Network Error").
            from app.services.dataset_file_store import materialize_dataset_file
            src_path = await materialize_dataset_file(ds)
            if src_path is not None:
                # Only read the sample rows. Parsing the WHOLE extract (a wide
                # 7.5k x 258 workbook) for a 5-row preview was slow enough to
                # exhaust a small instance — and this fires on every keystroke.
                try:
                    df = parse_tabular(str(src_path), file_type=ds.file_type, nrows=n)
                    for _, row in df.head(n).iterrows():
                        sample_rows.append({k: ("" if v is None else v) for k, v in row.to_dict().items()})
                except Exception as exc:  # noqa: BLE001 — preview must never 500
                    logging.getLogger(__name__).warning("rule preview: could not read source: %s", exc)
    else:
        table = getattr(conv, "ebs_table_hint", "") or ""
        if table:
            from app.services.mapping_service import ebs_sample_rows
            sample_rows = await ebs_sample_rows(table, n)

    # Even with no rows (EBS unreachable, or CONSTANT/COMPUTED rules that need
    # none) still emit one preview row so the user sees the rule's effect.
    if not sample_rows:
        sample_rows = [{}]

    out: list[PreviewSample] = []
    for idx, row_dict in enumerate(sample_rows):
        src_value = row_dict.get(payload.source_column) if payload.source_column else None
        ctx = {"row_index": idx + 1, "current_user": user.email, "now": datetime.utcnow(), "crosswalks": crosswalks}
        try:
            transformed = apply_pipeline(rules, src_value, row=row_dict, ctx=ctx)
            out.append(PreviewSample(source=src_value, output=transformed))
        except Exception as exc:
            out.append(PreviewSample(source=src_value, output=None, error=str(exc)))
    return PreviewResponse(samples=out)


class TranslateRuleRequest(BaseModel):
    description: str
    target_field_id: Optional[str] = None
    source_column: Optional[str] = None
    sample_size: Optional[int] = 5


@router.post("/conversions/{conversion_id}/rules/translate")
async def translate_rule_endpoint(
    conversion_id: str, payload: TranslateRuleRequest, _: User = Depends(get_current_user)
):
    """Turn a plain-English rule description into a structured transformation rule
    ({rule_type, config, explanation, ambiguities, source}) for the Rule Author
    modal's 'Describe this rule in plain English' box. Deterministic fast-path for
    the common flag→code derivation, Claude API otherwise; never 500s."""
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    if not (payload.description or "").strip():
        raise HTTPException(400, "Describe the rule in a sentence first.")
    from app.services.rule_translation_service import translate_rule
    return await translate_rule(
        conv, payload.description,
        target_field_id=payload.target_field_id, source_column=payload.source_column,
    )


@router.get("/conversions/{conversion_id}/rules", response_model=list[TransformationRuleOut])
async def list_rules(conversion_id: str, _: User = Depends(get_current_user)):
    rules = await TransformationRule.find(
        TransformationRule.conversion_id == PydanticObjectId(conversion_id)
    ).sort("sequence").to_list()
    return [{"id": str(r.id), "conversion_id": str(r.conversion_id), **{k: v for k, v in r.model_dump().items() if k not in ("id", "conversion_id")}} for r in rules]


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, _: User = Depends(get_current_user)):
    r = await TransformationRule.get(PydanticObjectId(rule_id))
    if not r:
        raise HTTPException(404, "Rule not found")
    await r.delete()
    return {"deleted": rule_id}


@router.post("/mappings/{mapping_id}/propagate")
async def propagate_mapping_rule(mapping_id: str, user: User = Depends(get_current_user)):
    from app.services.learning_service import propagate_rules_to_downstream
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    conv = await Conversion.get(m.conversion_id)
    if not conv:
        raise HTTPException(404, "Conversion not found")
    propagated = await propagate_rules_to_downstream(conv, m)
    return {"mapping_id": mapping_id, "propagated": propagated, "count": len(propagated)}


@router.get("/conversions/{conversion_id}/propagation-candidates")
async def propagation_candidates(conversion_id: str, _: User = Depends(get_current_user)):
    from app.services.learning_service import REFERENCE_KEY_FIELDS
    from app.models.fbdi import FBDIField, FBDITemplate
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    tpl = await FBDITemplate.get(conv.template_id) if conv.template_id else None
    master_obj = (tpl.business_object if tpl else None) or conv.target_object
    key_names = REFERENCE_KEY_FIELDS.get(master_obj or "", [])
    if not key_names:
        return {"source_conversion": conversion_id, "candidates": []}
    siblings = await Conversion.find(
        Conversion.project_id == conv.project_id,
        Conversion.id != conv.id,
    ).to_list()
    candidates = []
    for sib in siblings:
        if not sib.template_id:
            continue
        sib_fields = await FBDIField.find(FBDIField.template_id == sib.template_id).to_list()
        matching = [f.field_name for f in sib_fields if f.field_name in key_names]
        if matching:
            candidates.append({
                "conversion_id": str(sib.id),
                "conversion_name": sib.name,
                "target_object": sib.target_object,
                "fk_fields": matching,
            })
    return {
        "source_conversion": conversion_id,
        "master_object": master_obj,
        "key_fields": key_names,
        "candidates": candidates,
    }


@router.get("/conversions/{conversion_id}/inherited-standards")
async def inherited_standards(conversion_id: str, _: User = Depends(get_current_user)):
    return []


@router.get("/conversions/{conversion_id}/coded-values")
async def coded_values_audit(conversion_id: str, _: User = Depends(get_current_user)):
    """Audit every coded (LOV) target column BEFORE generation.

    Oracle rejects a file whose coded column holds a value outside its accepted
    list, and the failure surfaces as an opaque load error hours later. This walks
    the mapped source column for each coded field, resolves its distinct values
    against the codes mined from the template, and reports exactly what will be
    converted, what can't be grounded, and which columns depend on lookup codes
    that only exist in the customer's Fusion instance.
    """
    from app.models.fbdi import FBDIField
    from app.services.dataset_file_store import materialize_dataset_file
    from app.services.lov_service import build_crosswalk

    conv = await _require_conversion(conversion_id)
    mappings = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conv.id
    ).to_list()
    if not mappings:
        return {"columns": [], "summary": {}}

    field_ids = [m.target_field_id for m in mappings if m.target_field_id]
    fields = await FBDIField.find({"_id": {"$in": field_ids}}).to_list()
    coded = {f.id: f for f in fields if (f.allowed_values or f.lookup_type)}
    if not coded:
        return {"columns": [], "summary": {"coded_columns": 0}}

    # Sample the source once — distinct values in a few thousand rows are enough
    # to characterise a coded column, and this endpoint is called interactively.
    sample: Any = None
    if conv.dataset_id:
        ds = await Dataset.get(conv.dataset_id)
        if ds:
            src_path = await materialize_dataset_file(ds)
            if src_path is not None:
                try:
                    sample = parse_tabular(str(src_path), file_type=ds.file_type, nrows=2000)
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger(__name__).warning("coded-values: source read failed: %s", exc)

    out: list[dict] = []
    n_ok = n_confirm = n_error = n_unverified = 0

    for m in mappings:
        f = coded.get(m.target_field_id)
        if f is None or m.status == "not_applicable":
            continue

        values: list[Any] = []
        if sample is not None and m.source_column and m.source_column in sample.columns:
            values = [v for v in sample[m.source_column].dropna().unique().tolist()
                      if str(v).strip() != ""]
        elif m.default_value not in (None, ""):
            values = [m.default_value]

        allowed = list(f.allowed_values or [])
        row: dict[str, Any] = {
            "target_field": f.field_name,
            "required": bool(f.required),
            "data_type": f.data_type,
            "source_column": m.source_column,
            "default_value": m.default_value,
            "lookup_type": f.lookup_type,
            "allowed_codes": [
                {"code": str(a.get("code")), "meaning": a.get("meaning") or ""}
                for a in allowed
            ],
            "codes_source": (allowed[0].get("source") if allowed else None),
            "notes": f.validation_notes,
        }

        if not allowed:
            row.update({
                "status": "unverified",
                "resolved": [],
                "unresolved": [str(v) for v in values[:25]],
                "message": (
                    f"Codes for {f.lookup_type} live in your Fusion instance "
                    f"(Manage Standard Lookups), not in the template. Values pass "
                    f"through unchanged — import the lookup codes to validate them."
                ),
            })
            n_unverified += 1
            out.append(row)
            continue

        crosswalk = build_crosswalk(values, allowed)
        resolved = [
            {"from": k, "to": r["code"], "how": r["method"], "confidence": r["confidence"]}
            for k, r in crosswalk.items() if r["code"] is not None
        ]
        unresolved = [k for k, r in crosswalk.items() if r["code"] is None]

        unverified_codes = any(a.get("source") == "oracle_standard" for a in allowed)
        if unresolved and f.required:
            status = "error"
            n_error += 1
        elif unresolved or unverified_codes:
            status = "confirm"
            n_confirm += 1
        else:
            status = "ok"
            n_ok += 1

        msg = None
        if unresolved and f.required:
            msg = ("Required coded column. These values don't match any accepted code "
                   "and will fail the load — add a value rule, or correct the source.")
        elif unresolved:
            msg = ("Optional coded column. Unmatched values will be written blank so the "
                   "file still loads; map them if the data matters.")
        elif unverified_codes:
            msg = ("Resolved using Oracle-standard codes. Confirm them against your "
                   "instance in Manage Standard Lookups.")

        row.update({
            "status": status,
            "resolved": resolved[:25],
            "unresolved": [str(v) for v in unresolved[:25]],
            "message": msg,
        })
        out.append(row)

    order = {"error": 0, "confirm": 1, "unverified": 2, "ok": 3}
    out.sort(key=lambda r: (order.get(r["status"], 9), not r["required"], r["target_field"]))

    return {
        "columns": out,
        "summary": {
            "coded_columns": len(out),
            "ok": n_ok,
            "confirm": n_confirm,
            "error": n_error,
            "unverified": n_unverified,
            "source_sampled": sample is not None,
        },
    }
