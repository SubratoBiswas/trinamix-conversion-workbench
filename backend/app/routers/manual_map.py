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
            # a CONCAT learning carries all its source columns in rule_config; show
            # them joined so a many-to-one mapping reloads with every column visible.
            cols = (lm.rule_config or {}).get("columns") if isinstance(lm.rule_config, dict) else None
            disp = ", ".join(cols) if cols and len(cols) > 1 else lm.original_value
            learnt.setdefault(k, {"source": disp, "from": lm.captured_from,
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
    proposal_id: Optional[str] = None,
    _: User = Depends(get_current_user),
):
    """Every target field of the template, each pre-filled with its current learnt
    source, any previous-gold source, and — when proposal_id is given — what an
    uploaded mapping document proposed for that field, so the analyst can adjust the
    document's mapping by hand instead of starting from a blank grid."""
    cid = PydanticObjectId(client_id) if client_id else None
    learnt, gold = await _context_for(cid, target_object)

    # optional: fold in what an uploaded document proposed for each field
    doc: dict[str, str] = {}
    if proposal_id:
        from app.models.mapping_proposal import MappingProposal
        prop = await MappingProposal.get(PydanticObjectId(proposal_id))
        if prop:
            for r in prop.rows:
                if _n(r.target_object) == _n(target_object) and r.target_field:
                    doc.setdefault(_n(r.target_field), r.override_source or r.source_field)

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
                "doc_source": doc.get(k),
            })
    return {"target_object": target_object, "fields": fields,
            "learnt_count": len(learnt), "gold_count": len(gold), "doc_count": len(doc)}


@router.get("/sources")
async def known_sources(
    client_id: Optional[str] = None,
    target_object: Optional[str] = None,
    _: User = Depends(get_current_user),
):
    """The legacy source columns already seen for this client — from every dataset
    the client's projects have uploaded, plus every source column any learning holds.
    Powers a filterable pick-list so the analyst chooses a real column instead of
    typing one from memory."""
    from app.models.conversion import Conversion
    from app.models.dataset import Dataset, DatasetColumnProfile
    from app.models.project import Project
    from app.services.client_service import scope_query

    cid = PydanticObjectId(client_id) if client_id else None
    names: set[str] = set()

    # 1) real columns from the client's uploaded datasets
    try:
        proj_ids = [p.id for p in await Project.find(
            *( [Project.client_id == cid] if cid is not None else [] )).to_list()]
        convs = await Conversion.find_all().to_list()
        ds_ids = set()
        pid_set = set(proj_ids)
        for c in convs:
            if (cid is None or c.project_id in pid_set) and c.dataset_id:
                ds_ids.add(c.dataset_id)
        if ds_ids:
            for prof in await DatasetColumnProfile.find(
                    {"dataset_id": {"$in": list(ds_ids)}}).to_list():
                if prof.column_name:
                    names.add(prof.column_name.strip())
    except Exception:  # noqa: BLE001
        pass

    # 2) every source column any learning already uses (scoped to the client)
    try:
        scope = await scope_query(cid)
        q = {"kind": "column_mapping", **scope}
        if target_object:
            q["target_object"] = target_object
        for lm in await LearnedMapping.find(q).to_list():
            if lm.original_value:
                names.add(lm.original_value.strip())
            cols = (lm.rule_config or {}).get("columns") if isinstance(lm.rule_config, dict) else None
            for c in (cols or []):
                if c:
                    names.add(str(c).strip())
    except Exception:  # noqa: BLE001
        pass

    return {"sources": sorted(n for n in names if n)[:3000]}


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


@router.post("/suggest")
async def suggest(payload: dict, _: User = Depends(get_current_user)):
    """AI-assisted composition, both directions.

    one_to_many: {mode, source, target_fields:[...]} -> which target fields that ONE
      source column plausibly populates (a source often feeds several fields —
      Location -> LocationCode + LocationName).
    many_to_one: {mode, target_field, sources:[...]} -> which of several source
      columns COMBINE to fill one field, and how (e.g. First+Last -> Name via CONCAT).
    """
    import json as _json
    import re as _re

    import httpx as _httpx

    from app.config import settings as _settings

    key = (_settings.ANTHROPIC_API_KEY or "").strip()
    if not key:
        raise HTTPException(503, "AI is not configured on this environment.")
    mode = (payload or {}).get("mode")

    async def _ask(prompt: str) -> str:
        async with _httpx.AsyncClient(timeout=55.0) as cx:
            r = await cx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": _settings.ANTHROPIC_MODEL, "max_tokens": 1200,
                      "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json().get("content", [])
                           if b.get("type") == "text")

    if mode == "one_to_many":
        source = (payload.get("source") or "").strip()
        targets = [t for t in (payload.get("target_fields") or []) if t]
        if not source or not targets:
            raise HTTPException(400, "source and target_fields are required.")
        prompt = (
            f'A legacy source column is named "{source}". From the Oracle Fusion target '
            f'fields below, which ones would this single column legitimately populate? '
            f'A column can feed several related fields. Judge by meaning.\n\n'
            f'Target fields: {_json.dumps(targets)}\n\n'
            'Reply ONLY with a JSON array: [{"target_field":"...","fits":true,'
            '"reason":"under 15 words"}]. Include only fields that fit.')
        txt = await _ask(prompt)
        m = _re.search(r"\[.*\]", txt, _re.S)
        picks = [p for p in (_json.loads(m.group(0)) if m else []) if isinstance(p, dict) and p.get("fits")]
        return {"mode": mode, "source": source, "matches": picks}

    if mode == "many_to_one":
        target = (payload.get("target_field") or "").strip()
        sources = [s for s in (payload.get("sources") or []) if s]
        if not target or not sources:
            raise HTTPException(400, "target_field and sources are required.")
        prompt = (
            f'The Oracle Fusion field "{target}" may need to be built from more than one '
            f'legacy source column. From the candidates below, which combine to fill it, '
            f'and how?\n\nCandidate source columns: {_json.dumps(sources)}\n\n'
            'Reply ONLY with JSON: {"use":["col",...],"rule_type":"CONCAT",'
            '"separator":" ","reason":"under 18 words"}. If a single column suffices, '
            'return that one column with rule_type "DIRECT".')
        txt = await _ask(prompt)
        m = _re.search(r"\{.*\}", txt, _re.S)
        obj = _json.loads(m.group(0)) if m else {}
        return {"mode": mode, "target_field": target,
                "use": obj.get("use") or [], "rule_type": obj.get("rule_type"),
                "separator": obj.get("separator", " "), "reason": obj.get("reason")}

    raise HTTPException(400, "mode must be one_to_many or many_to_one.")


@router.post("/suggest-fill")
async def suggest_fill(payload: dict, _: User = Depends(get_current_user)):
    """Opt-in AI fill for UNMAPPED fields only.

    The manual mapper never calls AI on its own — that is deliberate, so learnings,
    gold and the document stay authoritative and AI can't over-map. This runs ONLY
    when the analyst asks: for the target fields they pass (the blank ones), the
    model picks the single best source column from the client's known columns, or
    none. It never touches a field that already has a mapping.
    """
    import json as _json
    import re as _re

    import httpx as _httpx

    from app.config import settings as _settings

    key = (_settings.ANTHROPIC_API_KEY or "").strip()
    if not key:
        raise HTTPException(503, "AI is not configured on this environment.")
    targets = [t for t in ((payload or {}).get("target_fields") or []) if t]
    sources = [s for s in ((payload or {}).get("sources") or []) if s]
    if not targets or not sources:
        raise HTTPException(400, "target_fields and sources are required.")

    out: list[dict] = []
    for i in range(0, len(targets), 30):
        chunk = targets[i:i + 30]
        prompt = (
            "You are filling gaps in a data-migration mapping. For EACH Oracle Fusion "
            "target field, pick the SINGLE best source column from the candidate list "
            "that would populate it, judged by what the data means. If nothing is a "
            'genuine fit, return null for that field — do NOT force a match.\n\n'
            f"Target fields: {_json.dumps(chunk)}\n"
            f"Candidate source columns: {_json.dumps(sources)}\n\n"
            'Reply ONLY with a JSON array: [{"target_field":"...","source":"col or null",'
            '"reason":"under 12 words"}].')
        try:
            async with _httpx.AsyncClient(timeout=55.0) as cx:
                r = await cx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": _settings.ANTHROPIC_MODEL, "max_tokens": 1500,
                          "messages": [{"role": "user", "content": prompt}]})
                r.raise_for_status()
                txt = "".join(b.get("text", "") for b in r.json().get("content", [])
                              if b.get("type") == "text")
            m = _re.search(r"\[.*\]", txt, _re.S)
            for row in (_json.loads(m.group(0)) if m else []):
                if isinstance(row, dict) and row.get("target_field") and row.get("source"):
                    out.append({"target_field": row["target_field"],
                                "source": row["source"], "reason": row.get("reason", "")})
        except Exception:  # noqa: BLE001
            continue
    return {"filled": out, "considered": len(targets)}


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

        # MANY sources -> ONE destination. A comma-separated source means the field is
        # built from several legacy columns, i.e. a CONCAT. Store the join rule with all
        # columns; original_value is the FIRST column so the output gate that checks the
        # source exists in the dataset still passes, while the rule reads them all.
        parts = [p.strip() for p in src.split(",") if p.strip()]
        if len(parts) > 1:
            rule_type = rule_type or "CONCAT"
            sep = r.get("separator")
            sep = " " if sep is None else str(sep)
            cfg = {"source_column": parts[0], "columns": parts, "separator": sep, "confidence": "High"}
            src = parts[0]
        else:
            src = parts[0] if parts else src
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
