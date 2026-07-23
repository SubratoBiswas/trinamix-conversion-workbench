"""Manual mapping for a client + FBDI template.

The document-review flow needs a document. Sometimes an analyst just wants to sit
in front of a template and map fields by hand — for a client with no doc yet, or to
fill the gaps a doc left. This surfaces every target field of a template with the
context the rest of the tool already knows (what is already LEARNT for this client,
what a previous GOLD load used), lets the analyst type a source column per field
with an optional transform rule, vets the pair (deterministic guard, optional AI),
and saves the result straight into the learning library — client-scoped, with a
reason recorded in the audit trail.
"""
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.models.fbdi import FBDIField, FBDITemplate
from app.models.learned import LearnedMapping
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/manual-map", tags=["manual-map"])

import re as _re
_NORM = _re.compile(r"[^a-z0-9]+")
_n = lambda s: _NORM.sub("", str(s or "").lower())


async def _context_for(client_id: Optional[PydanticObjectId], target_object: str):
    """Learnt source + gold source per normalized target field, for this object."""
    from app.services.client_service import scope_query
    scope = await scope_query(client_id)
    rows = await LearnedMapping.find({
        "target_object": target_object,
        "kind": {"$in": ["column_mapping", "example_default", "reference_standard"]},
        **scope,
    }).to_list()
    learnt: dict[str, dict] = {}
    gold: dict[str, dict] = {}
    for lm in rows:
        if not lm.target_field:
            continue
        k = _n(lm.target_field)
        cf = (lm.captured_from or "").lower()
        is_gold = "gold" in cf or "example_output" in cf or lm.kind in ("example_default", "reference_standard")
        if lm.kind == "column_mapping":
            learnt.setdefault(k, {"source": lm.original_value, "from": lm.captured_from,
                                  "rule_type": lm.rule_type, "id": str(lm.id)})
        if is_gold:
            gold.setdefault(k, {"source": lm.original_value if lm.kind == "column_mapping"
                                else (lm.resolved_value or "(constant)"),
                                "from": lm.captured_from})
    return learnt, gold


@router.get("/context")
async def context(
    target_object: str,
    template_id: Optional[str] = None,
    client_id: Optional[str] = None,
    _: User = Depends(get_current_user),
):
    """Every target field of the template, each pre-filled with its current learnt
    source and any previous-gold source, so the analyst edits from a starting point
    rather than a blank grid."""
    cid = PydanticObjectId(client_id) if client_id else None
    learnt, gold = await _context_for(cid, target_object)

    fields: list[dict] = []
    if template_id:
        for f in await FBDIField.find(FBDIField.template_id == PydanticObjectId(template_id)).to_list():
            k = _n(f.field_name)
            fields.append({
                "target_field": f.field_name,
                "sheet_name": getattr(f, "sheet_name", None),
                "required": bool(getattr(f, "required", False)),
                "learnt_source": (learnt.get(k) or {}).get("source"),
                "learnt_from": (learnt.get(k) or {}).get("from"),
                "learnt_rule": (learnt.get(k) or {}).get("rule_type"),
                "gold_source": (gold.get(k) or {}).get("source"),
            })
    return {"target_object": target_object, "fields": fields,
            "learnt_count": len(learnt), "gold_count": len(gold)}


@router.post("/vet")
async def vet(payload: dict, _: User = Depends(get_current_user)):
    """Judge typed (source -> target) pairs. Deterministic guard always; add AI when
    use_ai is set. Cheap — used as the analyst fills the grid."""
    from app.ai.semantic_guard import vet_candidate
    pairs = (payload or {}).get("pairs") or []
    use_ai = bool((payload or {}).get("use_ai"))
    out = []
    for p in pairs:
        tgt = (p.get("target_field") or "").strip()
        src = (p.get("source_field") or "").strip()
        if not tgt or not src:
            continue
        v = vet_candidate(src, [], tgt)
        out.append({"target_field": tgt, "source_field": src,
                    "plausible": v["plausible"], "reason": v["reason"],
                    "source_category": v["source_category"], "target_category": v["target_category"]})
    if use_ai and out:
        from app.services.candidate_vetting_service import vet_with_ai
        items = [{"id": i, "source_column": o["source_field"], "sample_values": [],
                  "target_field": o["target_field"], "target_desc": ""}
                 for i, o in enumerate(out)]
        verdicts = await vet_with_ai(items)
        for i, o in enumerate(out):
            v = verdicts.get(i)
            if v:
                o["ai_verdict"] = v.get("verdict")
                o["ai_reason"] = v.get("reason")
    return {"results": out}


@router.post("/save")
async def save(payload: dict, user: User = Depends(get_current_user)):
    """Upsert manual mappings as client-scoped column_mapping learnings.

    rows: [{target_field, source_field, rule_type?, reason?}]. An empty source_field
    with clear=true removes an existing learning for that field. A reason is recorded
    in the audit trail when supplied.
    """
    target_object = ((payload or {}).get("target_object") or "").strip()
    if not target_object:
        raise HTTPException(400, "target_object is required.")
    cid = PydanticObjectId(payload["client_id"]) if (payload or {}).get("client_id") else None
    source_system = (payload or {}).get("source_system") or None
    rows = (payload or {}).get("rows") or []

    saved = updated = cleared = 0
    for r in rows:
        tgt = (r.get("target_field") or "").strip()
        src = (r.get("source_field") or "").strip()
        if not tgt:
            continue
        rule_type = (r.get("rule_type") or "").strip() or None
        reason = (r.get("reason") or "").strip()
        captured = f"manual mapping: {reason}" if reason else "manual mapping"

        existing = await LearnedMapping.find_one(
            LearnedMapping.kind == "column_mapping",
            LearnedMapping.target_object == target_object,
            LearnedMapping.target_field == tgt,
        )
        if not src:
            if r.get("clear") and existing:
                await existing.delete()
                cleared += 1
            continue
        cfg = {"source_column": src, "confidence": "High"}
        if existing is not None:
            await existing.set({
                "original_value": src, "rule_type": rule_type, "rule_config": cfg,
                "source_erp": source_system, "captured_from": captured,
                "captured_by": user.email,
                **({"client_id": cid} if cid is not None else {}),
            })
            updated += 1
        else:
            await LearnedMapping(
                kind="column_mapping", category="Column Mapping Alias",
                original_value=src, resolved_value=tgt,
                target_object=target_object, target_field=tgt,
                rule_type=rule_type, rule_config=cfg,
                client_id=cid, is_global=cid is None,
                source_erp=source_system, captured_from=captured, captured_by=user.email,
            ).insert()
            saved += 1

    # Push onto existing conversions of this object for the client, so a manual map
    # corrects live work too (best-effort, mirrors the document-apply behaviour).
    touched = 0
    if saved or updated or cleared:
        from app.models.conversion import Conversion
        from app.models.mapping import MappingSuggestion
        from app.services.learning_service import apply_learned_to_conversion
        from app.services.client_service import client_id_for_conversion
        for conv in await Conversion.find_all().to_list():
            try:
                if cid is not None and await client_id_for_conversion(conv) != cid:
                    continue
                maps = await MappingSuggestion.find(
                    MappingSuggestion.conversion_id == conv.id).to_list()
                if maps and await apply_learned_to_conversion(conv, maps, force=True):
                    touched += 1
            except Exception:  # noqa: BLE001
                continue
    return {"saved": saved, "updated": updated, "cleared": cleared, "conversions_touched": touched}
