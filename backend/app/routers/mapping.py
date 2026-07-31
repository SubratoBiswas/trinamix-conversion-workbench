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


async def _mark_outputs_stale(conversion_id) -> int:
    """The file on disk no longer matches the rules that would now run.

    Every write that changes what generation would produce has to do this, and until
    now the RULE endpoints did not: a rule added or edited in the UI left the artifact
    marked fresh, the download reused it, and the change never appeared in the file.
    Combined with the download path serving stale artifacts anyway, that is why "the
    rules updated in the tool are not reflected in the output", why a Fix reported
    success and changed nothing, and why one analyst's change was invisible to another
    — both were handed the same cached file.
    """
    from app.models.output import ConvertedOutput
    try:
        res = await ConvertedOutput.find(
            ConvertedOutput.conversion_id == conversion_id
        ).update({"$set": {"status": "stale"}})
        return int(getattr(res, "modified_count", 0) or 0)
    except Exception:  # noqa: BLE001 — never fail the save on the bookkeeping
        log.exception("could not mark outputs stale for %s", conversion_id)
        return 0


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


@router.post("/conversions/{conversion_id}/ai-fill-blanks")
async def ai_fill_blanks(
    conversion_id: str,
    payload: dict | None = None,
    required_only: bool = False,
    user: User = Depends(get_current_user),
):
    """Opt-in: suggest a source for target fields that are still UNMAPPED.

    The mapper is deterministic-first and deliberately never over-maps on its own.
    This runs the model ONLY when the analyst asks, and ONLY on fields that have no
    source and no default. It ranks the real dataset columns for each blank field,
    lets the model pick the best (or none), and writes the pick as a 'suggested'
    mapping with an AI-fill reason — so it lands in Needs-review for the analyst to
    approve or reject, never silently in the output."""
    import asyncio
    from app.ai.rule_based import rank_candidates
    from app.models.fbdi import FBDITemplate
    from app.models.mapping import MappingSuggestion
    from app.services.mapping_service import (_sources_for_conversion,
                                              _target_fields_for)

    conv = await _require_conversion(conversion_id)
    template = await FBDITemplate.get(conv.template_id) if conv.template_id else None
    if not template:
        raise HTTPException(400, "This conversion has no template.")
    sources = await _sources_for_conversion(conv)
    targets = await _target_fields_for(template)
    maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conv.id).to_list()
    by_tid = {str(m.target_field_id): m for m in maps}

    tfids = set((payload or {}).get("target_field_ids") or [])
    # blank = no source column and no default; skip anything a human already touched
    blanks = []
    for t in targets:
        m = by_tid.get(str(t.id))
        if m is None:
            continue
        if tfids and str(t.id) not in tfids:
            continue
        if required_only and not getattr(t, "required", False):
            continue
        if (m.source_column or "").strip() or (m.default_value or "").strip():
            continue
        if m.status in ("approved", "overridden", "rejected"):
            continue
        blanks.append((t, m))

    if not blanks:
        return {"filled": 0, "considered": 0, "note": "No unmapped fields to fill."}

    # deterministic top candidates per blank field, then let AI pick the best/none.
    # The ranking loops every source column for every blank field — CPU-bound — so
    # run it OFF the event loop (blocking it here was stalling/killing the worker on
    # wide sources, surfacing as ERR_FAILED/CORS on the client).
    MAX = 40

    def _build_items(sources, blank_list):
        items_l, index_l = [], {}
        nid_l = 0
        for t, _m in blank_list[:MAX]:
            ranked = rank_candidates(sources, t, top_n=4)
            cand_names = [src.name for _s, src, _r in ranked if _s > 0][:4]
            if not cand_names:
                continue
            nid_l += 1
            index_l[nid_l] = (t, cand_names)
            samples = []
            for _s, src, _r in ranked[:1]:
                samples = [str(v) for v in (src.sample_values or [])[:3]]
            items_l.append({"id": nid_l, "source_column": " | ".join(cand_names),
                            "sample_values": samples, "target_field": t.field_name,
                            "target_desc": (t.description or "")})
        return items_l, index_l

    items, index = await asyncio.to_thread(_build_items, sources, blanks)

    # ask the model to confirm the single best candidate per field
    from app.config import settings
    import httpx as _httpx
    import json as _json
    import re as _re
    verdicts: dict[int, str] = {}
    key = (settings.ANTHROPIC_API_KEY or "").strip()
    if key and items:
        for i in range(0, len(items), 30):
            chunk = items[i:i + 30]
            lines = "\n".join(
                f'{it["id"]}. Oracle field "{it["target_field"]}"'
                + (f' ({it["target_desc"]})' if it["target_desc"] else "")
                + f'. Candidate source columns: {it["source_column"]}.'
                for it in chunk)
            prompt = (
                "Pick the SINGLE best source column for each Oracle field from its "
                "candidate list, judged by meaning. Return null if none genuinely "
                f"fits — do not force a match.\n\n{lines}\n\n"
                'Reply ONLY with JSON: [{"id":n,"source":"col or null"}].')
            try:
                async with _httpx.AsyncClient(timeout=55.0) as cx:
                    r = await cx.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"},
                        json={"model": settings.ANTHROPIC_MODEL, "max_tokens": 1500,
                              "messages": [{"role": "user", "content": prompt}]})
                    r.raise_for_status()
                    txt = "".join(b.get("text", "") for b in r.json().get("content", [])
                                  if b.get("type") == "text")
                mm = _re.search(r"\[.*\]", txt, _re.S)
                for row in (_json.loads(mm.group(0)) if mm else []):
                    if isinstance(row, dict) and row.get("id") and row.get("source"):
                        verdicts[int(row["id"])] = str(row["source"])
            except Exception:  # noqa: BLE001
                continue

    filled = 0
    for nid2, (t, cand_names) in index.items():
        # AI pick if present and it is one of the real candidates; else deterministic top
        pick = verdicts.get(nid2)
        if pick not in cand_names:
            pick = cand_names[0] if not key else None   # with AI on, respect a 'none'
        if not pick:
            continue
        m = by_tid.get(str(t.id))
        if m is None:
            continue
        await m.set({"source_column": pick, "status": "suggested",
                     "confidence": 0.5, "review_required": 1,
                     "reason": "AI fill — review before use", "suggested_transformation": None})
        filled += 1
    return {"filled": filled, "considered": len(blanks)}


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
    # Collapse any duplicate rows a prior re-run race may have left (same target
    # field appearing 2+ times), keeping the strongest human decision per field.
    # Non-destructive: fixes the canvas/table AND the CSV export (both read this)
    # so a rejected field never shows a stale "suggested" twin. Physical cleanup
    # happens at map time (run_mapping_suggestions) and via /dedupe-mappings.
    from app.services.mapping_service import collapse_mapping_dupes
    items = collapse_mapping_dupes(items)
    return await enrich_mapping_with_samples(conv, items)


@router.post("/conversions/{conversion_id}/dedupe-mappings")
async def dedupe_mappings(conversion_id: str, _: User = Depends(get_current_user)):
    """Physically remove duplicate mapping rows for this conversion (heals a
    conversion whose rows were doubled by an earlier re-run race). Keeps the
    strongest human decision per target field. Returns how many were removed."""
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    from app.services.mapping_service import dedupe_conversion_mappings
    removed = await dedupe_conversion_mappings(conv.id)
    return {"conversion_id": conversion_id, "removed": removed}


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
        # EVERY bound source, not just the first. A two-sheet workbook (Customer +
        # Address) becomes two datasets on one conversion, and this read the singular
        # conv.dataset_id — so Mapping Review offered only the first sheet's columns.
        #
        # The auto-mapper had already got this right (mapping_service._sources_for_
        # conversion unions source_dataset_ids), which made the failure worse than a
        # plain omission: the AI could propose a mapping to an Address column that the
        # analyst could then neither see nor re-pick from the dropdown. Two layers
        # disagreeing about what the sources ARE.
        _ds_ids = [d for d in (conv.source_dataset_ids or []) if d] or (
            [conv.dataset_id] if conv.dataset_id else [])
        profs = await DatasetColumnProfile.find(
            {"dataset_id": {"$in": _ds_ids}}
        ).sort(+DatasetColumnProfile.position).to_list()
        # Sheets repeat column names (both carry `entityid`). One entry per NAME —
        # the mapping stores a column name, so two entries would be the same choice
        # twice with no way to tell them apart. First source wins, which is the
        # sheet order the analyst uploaded.
        _seen_names: set = set()
        _ds_order = {str(d): i for i, d in enumerate(_ds_ids)}
        profs.sort(key=lambda x: (_ds_order.get(str(getattr(x, "dataset_id", "")), 99),
                                  x.position or 0))
        for p in profs:
            _nm = (p.column_name or "").strip().lower()
            if _nm in _seen_names:
                continue
            _seen_names.add(_nm)
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
    # ANY deliberate edit is the analyst deciding, and it is dated NOW. The precedence
    # they set is "the last mapping with respect to date is final", which only works
    # if every decision carries the date it was actually made. exclude_unset means
    # `status` is absent when someone edits a column or a default without re-pressing
    # Approve, so the stamps used to stay at the ORIGINAL approval — an edit made
    # today could lose to a rule file dated yesterday.
    _DECISION_FIELDS = {"source_column", "default_value", "status",
                        "suggested_transformation"}
    _is_decision = bool(_DECISION_FIELDS & set(data))
    if _is_decision:
        data["approved_by"] = user.email
        data["approved_at"] = datetime.utcnow()
    await m.set(data)
    await _mark_outputs_stale(m.conversion_id)
    conv = await Conversion.get(m.conversion_id)
    # LEARN ON ANY DELIBERATE EDIT — not only on an approved one.
    #
    # This used to require status in (approved, overridden), and `status` is absent
    # from the payload unless the UI sends it. So changing the source column of a row
    # still sitting in "suggested" and saving captured NOTHING: the conversion in
    # front of the analyst was right, the library never heard about it, and nothing
    # propagated. The sibling Approve endpoint had no such gate, which is why Approve
    # propagated and Save silently did not — one screen, two behaviours.
    #
    # A rejected/not_applicable edit is a decision too — "remove this mapping" — and
    # is captured through the keep-blank path, which now propagates as well.
    _prop: dict | None = None
    if _is_decision and (m.source_column
                         or (m.default_value and str(m.default_value).strip())):
        _lm = await record_learning_from_mapping(m, conv, captured_by=user.email)
        if _lm is not None:
            try:
                from app.services.learning_service import (
                    propagate_learning_to_open_conversions)
                _prop = await propagate_learning_to_open_conversions(
                    _lm, conv, captured_by=user.email)
            except Exception as exc:  # noqa: BLE001
                log.exception("propagating the saved learning failed for %s", mapping_id)
                # NOT silent. Both call sites swallowed this and returned 200 with a
                # normal payload, so a propagation that threw and one that reached 12
                # conversions looked identical to the analyst.
                _prop = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    out = (await enrich_mapping_with_samples(conv, [m]))[0]
    if _prop is not None:
        try:
            out["propagation"] = _prop
        except Exception:  # noqa: BLE001 — a non-dict response shape must not break
            pass
    return out


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


@router.put("/mappings/{mapping_id}/keep-blank", response_model=MappingOut)
async def keep_blank(mapping_id: str, user: User = Depends(get_current_user)):
    """Ship this column empty, and hold that decision everywhere.

    "Leave it blank" is a real decision and it was surprisingly hard to express: an
    analyst had to clear the source column, clear the default, and set the status by
    hand — and even then a control default would refill the column at generate, because
    that pass skips a field only if something told it to. Batch ID was the live proof:
    marked blank, still shipping 900001.

    So this does all three things at once. The mapping becomes not_applicable with no
    source and no default, which is what puts it into the generator's suppression set;
    a suppress_field learning records the intent so every current and future conversion
    of the object inherits it; and the outputs are marked stale, because the file on
    disk still has the value in it.
    """
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    conv = await Conversion.get(m.conversion_id)
    _blank = {"status": "not_applicable", "source_column": None,
              "default_value": None, "review_required": 0,
              "approved_by": user.email, "approved_at": datetime.utcnow()}
    await m.set(_blank)

    # Any SIBLING row for the same target field goes blank too. The generator keeps
    # one mapping per target field and picks it by status priority, where
    # not_applicable (2) ranks BELOW approved (3) and overridden (4) — so a stale
    # duplicate row left over from an earlier auto-map would win the dedup and the
    # column would keep shipping its value while the UI showed it blank. That is the
    # same shape of bug as Batch ID, one layer down, so it is closed here rather than
    # left for the next output to expose.
    if m.target_field_id:
        for sib in await MappingSuggestion.find(
            MappingSuggestion.conversion_id == m.conversion_id,
            MappingSuggestion.target_field_id == m.target_field_id,
        ).to_list():
            if sib.id != m.id:
                await sib.set(_blank)

    await _mark_outputs_stale(m.conversion_id)

    learned = False
    lm = None
    try:
        from app.models.fbdi import FBDIField
        from app.services.client_service import client_id_for_conversion
        from app.services.learning_service import (_upsert, source_erp_for_conversion,
                                                   _business_object_for)
        f = await FBDIField.get(m.target_field_id) if m.target_field_id else None
        obj = await _business_object_for(conv)
        if f is not None and obj:
            lm = await _upsert(
                kind="suppress_field", category="Left blank on purpose",
                original_value="(blank)", resolved_value="",
                target_object=obj, target_field=f.field_name, rule_type="suppress",
                rule_config={"note": f"Kept blank by {user.email}"},
                project_id=getattr(conv, "project_id", None),
                client_id=await client_id_for_conversion(conv),
                source_erp=await source_erp_for_conversion(conv),
                captured_from="kept blank in Mapping Review",
                captured_by=user.email,
                # An analyst pressing this IS an explicit action, so it may revive a
                # suppression they previously retired.
                revive=True,
            )
            learned = lm is not None
    except Exception:  # noqa: BLE001 — the decision is saved either way
        log.exception("keep-blank: could not record the suppression learning")

    # "Remove this mapping" is a decision like any other and has to reach the
    # conversions that already exist. This captured the suppression and then stopped,
    # so a field the analyst blanked here stayed populated everywhere else — the one
    # direction where the gap is silently destructive, because the analyst has every
    # reason to believe a blank means blank.
    _prop: dict | None = None
    if learned and lm is not None:
        try:
            from app.services.learning_service import (
                propagate_learning_to_open_conversions)
            _prop = await propagate_learning_to_open_conversions(
                lm, conv, captured_by=user.email)
        except Exception as exc:  # noqa: BLE001
            log.exception("keep-blank: propagating the suppression failed")
            _prop = {"error": f"{type(exc).__name__}: {exc}"[:300]}

    out = (await enrich_mapping_with_samples(conv, [m]))[0]
    out["learned_suppression"] = learned
    if _prop is not None:
        out["propagation"] = _prop
    return out


@router.put("/mappings/{mapping_id}/approve", response_model=MappingOut)
async def approve_mapping(mapping_id: str, user: User = Depends(get_current_user)):
    m = await MappingSuggestion.get(PydanticObjectId(mapping_id))
    if not m:
        raise HTTPException(404, "Mapping not found")
    await m.set({"status": "approved", "approved_by": user.email, "approved_at": datetime.utcnow()})
    await _mark_outputs_stale(m.conversion_id)
    conv = await Conversion.get(m.conversion_id)
    if m.source_column or (m.default_value and str(m.default_value).strip()):
        _lm = await record_learning_from_mapping(m, conv, captured_by=user.email)
        # "Any rule or correction I make apply in learning so that it will be available
        # for all CURRENT and future conversions" (analyst, 30-Jul). Capturing the
        # learning already covered future conversions; this reaches the ones that
        # already exist. It never touches a mapping another person approved.
        if _lm is not None:
            try:
                from app.services.learning_service import (
                    propagate_learning_to_open_conversions)
                _p = await propagate_learning_to_open_conversions(
                    _lm, conv, captured_by=user.email)
                log.info("approve %s propagated: %s", mapping_id, _p)
            except Exception:  # noqa: BLE001 — never fail the approve on the fan-out
                log.exception("propagating the approved learning failed for %s",
                              mapping_id)
    if m.source_column:
        from app.services.learning_service import propagate_rules_to_downstream
        await propagate_rules_to_downstream(conv, m)
    return (await enrich_mapping_with_samples(conv, [m]))[0]


async def _sync_mapping_to_rule(r: TransformationRule, actor: str) -> None:
    """Make Mapping Review agree with the rule the analyst just saved.

    The rule dialog asks for a Source column and a Target FBDI field, and stores both
    on the RULE. The Mapping Review screen reads the MAPPING row — so after saving
    "Address Name <- City, value mapping" the field still showed SOURCE "(none)" and
    "Required field unmapped", which is the analyst's report: "I updated this in the
    custom rule, but it is not reflecting in the mapping section".

    Two artefacts describing one decision is the problem; this makes the rule write
    the decision down where the screen, the required-field gate and the generator all
    already look.

    Deliberately conservative. It only FILLS an empty binding — a field the analyst
    has already bound to a different column keeps that column, because the mapping is
    the more specific statement and silently retargeting it would be the eBOS
    "shows city, ships PRIMARY" bug pointed the other way. And a not_applicable
    mapping is lifted, because attaching a rule to a field is saying it should carry
    a value: leaving it discarded is how a correctly authored rule produced nothing.
    """
    if not r.target_field_id or not (r.source_column or "").strip():
        return
    try:
        m = await MappingSuggestion.find_one(
            MappingSuggestion.conversion_id == r.conversion_id,
            MappingSuggestion.target_field_id == r.target_field_id,
        )
        if m is None:
            return
        patch: dict = {}
        if not (m.source_column or "").strip():
            patch["source_column"] = r.source_column
        if (m.status or "") in ("not_applicable", "rejected"):
            patch["status"] = "overridden"
        if patch:
            patch.update({"approved_by": actor, "approved_at": datetime.utcnow()})
            await m.set(patch)
    except Exception as exc:  # noqa: BLE001 — never fail the rule save over this
        log.warning("could not sync mapping to rule %s: %s", r.id, exc)


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
    await _mark_outputs_stale(conv.id)
    await _sync_mapping_to_rule(r, user.email)
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


@router.post("/conversions/{conversion_id}/rules/author")
async def author_rule_endpoint(
    conversion_id: str, payload: dict, user: User = Depends(get_current_user)
):
    """Write a rule once in plain English, learn it for several modules.

    The Rule Author's translation box is opened FROM a target field and stamps
    its learning with that one conversion's object, so an Item rule never reached
    BOM. This takes the same sentence plus an EXPLICIT list of modules and writes
    one learning each — client-scoped and stamped with the source system, so a
    convention stated once governs every module the analyst names, and no more.

    Body: {description, target_field, objects[], target_field_id?, source_column?}
    """
    from app.services.rule_authoring_service import (KNOWN_OBJECTS,
                                                     author_rule_for_objects)

    conv = await _require_conversion(conversion_id)
    description = (payload.get("description") or "").strip()
    # One rule may write to SEVERAL fields. Splitting a phone into country, area,
    # number and extension is one sentence, not four; a single-target signature forced
    # the analyst to retype it once per destination, and the copies then drifted apart
    # as each was edited. `target_field` (string) is still accepted so nothing that
    # already calls this breaks.
    target_fields = payload.get("target_fields")
    if not target_fields:
        one = (payload.get("target_field") or "").strip()
        target_fields = [one] if one else []
    target_fields = [str(f).strip() for f in target_fields if str(f or "").strip()]
    objects = payload.get("objects") or []
    if not description:
        raise HTTPException(400, "Describe the rule in a sentence first.")
    if not target_fields:
        raise HTTPException(400, "Pick at least one target field the rule writes to.")
    if not objects:
        raise HTTPException(
            400, f"Pick at least one module. Known: {', '.join(KNOWN_OBJECTS[:6])}…")

    res = await author_rule_for_objects(
        conv, description, target_field=target_fields, objects=objects,
        captured_by=getattr(user, "email", ""),
        target_field_id=payload.get("target_field_id"),
        source_column=payload.get("source_column"),
    )
    if res.get("error") and not res.get("written"):
        raise HTTPException(422, res["error"])
    return res


@router.get("/rule-authoring/objects")
async def rule_authoring_objects(_: User = Depends(get_current_user)):
    """Modules a plain-English rule can be learned for."""
    from app.services.rule_authoring_service import KNOWN_OBJECTS
    return {"objects": list(KNOWN_OBJECTS)}


@router.get("/conversions/{conversion_id}/rules", response_model=list[TransformationRuleOut])
async def list_rules(conversion_id: str, _: User = Depends(get_current_user)):
    """Rules saved against this conversion — what the authoring modal reloads.

    ``target_field_id`` is now stringified. The spread used to pass it through as a
    raw ObjectId, and ``TransformationRuleOut`` types it ``str``, so this endpoint
    returned 500 for every conversion that actually HAD a rule: the analyst's rule
    was intact in the database and unreachable in the UI, which presents as "my saved
    rule disappeared" — CW #6, still live after the modal was fixed. ``ApiOut`` now
    coerces this whole class of mistake; the explicit cast stays because it is the
    one visible at the call site.
    """
    rules = await TransformationRule.find(
        TransformationRule.conversion_id == PydanticObjectId(conversion_id)
    ).sort("sequence").to_list()
    return [{**{k: v for k, v in r.model_dump().items()
                if k not in ("id", "conversion_id", "target_field_id")},
             "id": str(r.id),
             "conversion_id": str(r.conversion_id),
             "target_field_id": (str(r.target_field_id)
                                 if r.target_field_id else None)}
            for r in rules]


@router.put("/rules/{rule_id}", response_model=TransformationRuleOut)
async def update_rule(
    rule_id: str, payload: TransformationRuleCreate, user: User = Depends(get_current_user)
):
    """Edit a saved rule in place.

    Without this, re-opening "Add custom transformation rule" for a field the
    analyst already configured and pressing Save inserted a SECOND rule for the
    same target field — the two then stacked at generate time. The authoring
    modal now loads the existing rule and PUTs back to this endpoint.
    """
    r = await TransformationRule.get(PydanticObjectId(rule_id))
    if not r:
        raise HTTPException(404, "Rule not found")
    conv = await Conversion.get(r.conversion_id)
    if not conv:
        raise HTTPException(404, "Conversion not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("target_field_id"):
        try:
            data["target_field_id"] = PydanticObjectId(str(data["target_field_id"]))
        except Exception:
            data["target_field_id"] = None
    for k, v in data.items():
        setattr(r, k, v)
    await r.save()
    await _mark_outputs_stale(r.conversion_id)
    await _sync_mapping_to_rule(r, user.email)
    # Re-learn so the edited definition (not the superseded one) is what future
    # conversions inherit. Best-effort, exactly as in add_rule.
    try:
        await record_learning_from_rule(r, conv, captured_by=user.email)
    except Exception as exc:
        log.warning(f"update_rule: learning capture failed for rule {r.id}: {exc}")
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


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, _: User = Depends(get_current_user)):
    r = await TransformationRule.get(PydanticObjectId(rule_id))
    if not r:
        raise HTTPException(404, "Rule not found")
    conv_id = r.conversion_id
    await r.delete()
    # Removing a rule changes the output just as much as adding one.
    await _mark_outputs_stale(conv_id)
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
