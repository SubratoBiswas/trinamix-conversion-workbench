"""Conversational copilot — grounded, read-only Q&A about ONE conversion.

Epic B, slice 1: answer an analyst's questions ("why is Invoice Match Option
blank?", "which required fields are still unmapped?", "what will Oracle reject?",
"how ready is this for cutover?") strictly from the conversion's OWN data —
mappings (with provenance), the generate-time DQ report, and the readiness score —
so every answer is grounded and cites where it came from.

Design:
* ``build_conversion_facts`` (async) gathers the grounded facts from Mongo.
* ``answer_from_facts`` is a PURE intent-answerer over those facts (no DB / LLM),
  so the copilot gives a useful, provenance-cited answer even with no model — and
  it is unit-testable.
* ``answer_grounded`` runs the deterministic answer, then (if an LLM is configured)
  asks the model to answer using the SAME facts, preferring its reply but always
  falling back to the deterministic one. Never mutates state.
"""
from __future__ import annotations

import re
from typing import Optional

_STATUS_PRIO = {"overridden": 4, "approved": 3, "not_applicable": 2, "rejected": 1, "suggested": 0}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", " ", str(s or "").lower()).strip()


def _provenance(m) -> str:
    """A short 'where this decision came from' label for a best mapping."""
    if m is None:
        return "unmapped"
    src = bool(m.get("source_column"))
    dfl = bool(m.get("default_value"))
    status = (m.get("status") or "suggested")
    reason = (m.get("reason") or "").lower()
    if not src and not dfl:
        return "unmapped (gap)"
    if not src and dfl:
        return "constant/default"
    if "gold" in reason:
        return f"gold standard ({status})"
    if "learn" in reason:
        return f"learned rule ({status})"
    if "ai" in reason or "model" in reason:
        return f"AI suggestion ({status})"
    return f"mapped ({status})"


async def build_conversion_facts(conversion) -> dict:
    from app.models.fbdi import FBDIField, FBDITemplate
    from app.models.mapping import MappingSuggestion
    from app.models.output import ConvertedOutput

    target = conversion.target_object or ""
    if conversion.template_id and not target:
        tpl = await FBDITemplate.get(conversion.template_id)
        target = (tpl.business_object if tpl else "") or ""

    fields = await FBDIField.find(FBDIField.template_id == conversion.template_id).to_list() \
        if conversion.template_id else []
    fbyid = {f.id: f for f in fields}
    maps = await MappingSuggestion.find(MappingSuggestion.conversion_id == conversion.id).to_list()
    best: dict = {}
    for m in maps:
        c = best.get(m.target_field_id)
        if c is None or _STATUS_PRIO.get(m.status or "suggested", 0) > _STATUS_PRIO.get(c.status or "suggested", 0):
            best[m.target_field_id] = m

    mapped, unmapped_required = [], []
    for f in fields:
        m = best.get(f.id)
        md = None
        if m is not None:
            md = {"source_column": m.source_column, "status": m.status,
                  "default_value": m.default_value,
                  "reason": m.reason,
                  "rules": [ (m.suggested_transformation or {}).get("rule_type") ]
                           if m.suggested_transformation else []}
        prov = _provenance(md)
        entry = {"target_field": f.field_name, "required": bool(f.required),
                 "source_column": (md or {}).get("source_column"),
                 "default_value": (md or {}).get("default_value"),
                 "status": (md or {}).get("status"),
                 "provenance": prov}
        mapped.append(entry)
        if f.required and prov in ("unmapped", "unmapped (gap)"):
            unmapped_required.append(f.field_name)

    out = await ConvertedOutput.find(
        ConvertedOutput.conversion_id == conversion.id).sort("-generated_at").first_or_none()
    dq = (out.dq_report if out else None) or {}

    try:
        from app.services.readiness_service import assess_conversion
        rd = await assess_conversion(conversion)
        readiness = {"score": rd["score"], "band": rd["band"], "effort": rd["effort"],
                     "coverage_pct": rd["coverage_pct"], "est_hours": rd["est_hours"]}
    except Exception:  # noqa: BLE001
        readiness = None

    return {
        "conversion_id": str(conversion.id), "name": conversion.name, "target_object": target,
        "required_total": sum(1 for f in fields if f.required),
        "required_covered": sum(1 for f in fields if f.required) - len(unmapped_required),
        "unmapped_required": unmapped_required,
        "mapped": mapped,
        "dq": {"hard_error_count": int(dq.get("hard_error_count", 0) or 0),
               "warning_count": int(dq.get("warning_count", 0) or 0),
               "top_issues": (dq.get("top_issues") or [])[:6],
               "generated": out is not None},
        "readiness": readiness,
    }


def _find_field(facts: dict, q: str) -> Optional[dict]:
    nq = _norm(q)
    best = None
    for e in facts.get("mapped", []):
        fn = _norm(e["target_field"])
        if fn and fn in nq:
            if best is None or len(fn) > len(_norm(best["target_field"])):
                best = e
    return best


def answer_from_facts(facts: dict, question: str) -> dict:
    """Pure deterministic intent answerer over the grounded facts. Returns
    ``{answer, citations, intent}``."""
    q = _norm(question)
    cites: list[str] = []
    obj = facts.get("target_object") or facts.get("name") or "this object"

    # why is <field> blank / how is <field> mapped
    if any(k in q for k in ("why", "blank", "empty", "mapped", "map for", "come from", "populate")):
        e = _find_field(facts, question)
        if e:
            cites.append(f"Mapping: {e['target_field']} — {e['provenance']}")
            if e["source_column"]:
                a = (f"'{e['target_field']}' is mapped from source column "
                     f"'{e['source_column']}' ({e['status']}).")
            elif e["default_value"]:
                a = f"'{e['target_field']}' has no source column; it is defaulted to '{e['default_value']}'."
            else:
                a = (f"'{e['target_field']}' is blank because no source column is mapped to it and it has "
                     f"no default{' (and it is required)' if e['required'] else ''}.")
            return {"answer": a, "citations": cites, "intent": "field_provenance"}

    # unmapped / required gaps
    if any(k in q for k in ("unmapped", "not mapped", "gap", "missing", "required field")):
        um = facts.get("unmapped_required", [])
        cites.append(f"Required coverage: {facts.get('required_covered')}/{facts.get('required_total')}")
        if not um:
            a = f"All {facts.get('required_total', 0)} required fields for {obj} are covered (mapped or defaulted)."
        else:
            a = (f"{len(um)} required field(s) are still unmapped for {obj}: "
                 + ", ".join(um[:15]) + ("…" if len(um) > 15 else "") + ".")
        return {"answer": a, "citations": cites, "intent": "unmapped_required"}

    # data quality / what will Oracle reject
    if any(k in q for k in ("reject", "fail", "error", "quality", "dq", "invalid", "block")):
        dq = facts.get("dq", {})
        if not dq.get("generated"):
            return {"answer": "No output has been generated yet, so there is no data-quality report to read. "
                              "Generate the output first, then ask again.", "citations": [], "intent": "dq"}
        he, wn = dq.get("hard_error_count", 0), dq.get("warning_count", 0)
        cites.append(f"DQ report: {he} hard error(s), {wn} warning(s)")
        tops = "; ".join(f"{i.get('field_name') or i.get('issue_type','issue')}: {i.get('issue_type') or i.get('message','')}"
                         for i in dq.get("top_issues", [])[:4])
        a = (f"The generate-time data-quality check found {he} hard error(s) that would block the load and "
             f"{wn} warning(s)." + (f" Top issues — {tops}." if tops else ""))
        return {"answer": a, "citations": cites, "intent": "dq"}

    # readiness / cutover
    if any(k in q for k in ("ready", "readiness", "cutover", "score", "how far", "effort")):
        rd = facts.get("readiness")
        if not rd:
            return {"answer": "Readiness isn't available yet for this object.", "citations": [], "intent": "readiness"}
        cites.append(f"Readiness: {rd['score']}/100 ({rd['band']})")
        a = (f"{obj} scores {rd['score']}/100 — {rd['band']}. Required-field coverage is {rd['coverage_pct']}%, "
             f"estimated remaining effort is {rd['effort']} (~{rd['est_hours']}h).")
        return {"answer": a, "citations": cites, "intent": "readiness"}

    # default: grounded summary
    rd = facts.get("readiness") or {}
    dq = facts.get("dq", {})
    cites.append(f"Required coverage: {facts.get('required_covered')}/{facts.get('required_total')}")
    if rd:
        cites.append(f"Readiness: {rd.get('score')}/100 ({rd.get('band')})")
    a = (f"{obj}: {facts.get('required_covered')}/{facts.get('required_total')} required fields covered"
         + (f", readiness {rd.get('score')}/100 ({rd.get('band')})" if rd else "")
         + (f", {dq.get('hard_error_count',0)} DQ hard error(s)" if dq.get("generated") else ", output not generated yet")
         + f". {len(facts.get('unmapped_required', []))} required field(s) still unmapped.")
    return {"answer": a, "citations": cites, "intent": "summary"}


async def answer_grounded(conversion, question: str) -> dict:
    facts = await build_conversion_facts(conversion)
    det = answer_from_facts(facts, question)
    result = {**det, "ai_used": False,
              "facts_summary": {"required_total": facts["required_total"],
                                "required_covered": facts["required_covered"],
                                "unmapped_required": facts["unmapped_required"][:20],
                                "dq": facts["dq"], "readiness": facts["readiness"]}}
    try:
        import json
        import httpx
        from app.config import settings
        provider = (settings.AI_PROVIDER or "none").lower()
        if provider not in ("anthropic", "openai"):
            return result
        compact = {k: facts[k] for k in ("target_object", "required_total", "required_covered",
                                         "unmapped_required", "dq", "readiness")}
        compact["mapped"] = [m for m in facts["mapped"]][:120]
        prompt = (
            "You are the Trinamix Conversion Workbench copilot. Answer the analyst's question "
            "STRICTLY from the FACTS about this one conversion (do not invent). Be concise "
            "(2-4 sentences) and, when relevant, say which field/source/precedence the answer "
            "comes from. If the facts don't contain the answer, say so.\n\n"
            f"FACTS:\n{json.dumps(compact, default=str)[:9000]}\n\nQUESTION: {question}"
        )
        if provider == "anthropic":
            r = httpx.post("https://api.anthropic.com/v1/messages",
                           headers={"x-api-key": settings.ANTHROPIC_API_KEY,
                                    "anthropic-version": "2023-06-01", "content-type": "application/json"},
                           json={"model": settings.ANTHROPIC_MODEL or "claude-sonnet-4-6",
                                 "max_tokens": 500, "messages": [{"role": "user", "content": prompt}]},
                           timeout=45.0)
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
        else:
            r = httpx.post("https://api.openai.com/v1/chat/completions",
                           headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
                           json={"model": settings.OPENAI_MODEL, "messages": [{"role": "user", "content": prompt}]},
                           timeout=45.0)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
        if text:
            result["answer"] = text
            result["ai_used"] = True
    except Exception:  # noqa: BLE001 — deterministic answer already stands
        return result
    return result
