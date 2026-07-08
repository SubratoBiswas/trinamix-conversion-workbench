"""AI load-error explanations.

When a load run produces Oracle FBDI rejections, this turns each terse error
message into a plain-English root cause and a concrete, actionable fix (which
mapping/value/format to change and re-load). Errors are deduplicated by message
first, so one LLM call explains all rows that share an error.

The enriched text fills the LoadError.root_cause and LoadError.suggested_fix
fields that the Error Traceback UI already renders — so no frontend change is
needed. Best-effort: on any failure the original (rule-based) fields are kept.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_MAX_DISTINCT = 40


async def explain_load_errors(errors: list[dict], object_name: Optional[str]) -> list[dict]:
    """Enrich error dicts (LoadError fields) with an AI root_cause + suggested_fix,
    keyed by distinct error_message. Returns the same list, enriched in place.
    Empty AI / errors are handled gracefully."""
    provider = (settings.AI_PROVIDER or "none").lower()
    if provider not in ("anthropic", "openai") or not errors:
        return errors

    # Distinct messages (+ a sample category/reference) → one explanation each.
    distinct: dict[str, dict] = {}
    for e in errors:
        msg = (e.get("error_message") or "").strip()
        if msg and msg not in distinct:
            distinct[msg] = {
                "message": msg,
                "category": e.get("error_category"),
                "field": e.get("object_name"),
                "reference": e.get("reference_value"),
            }
        if len(distinct) >= _MAX_DISTINCT:
            break
    if not distinct:
        return errors

    prompt = (
        "You are an Oracle Fusion Cloud FBDI load-error expert. For each error "
        f"below (from loading the '{object_name or 'Fusion'}' object), give a "
        "concise plain-English ROOT CAUSE and a concrete FIX telling the analyst "
        "exactly what to change in the mapping, value crosswalk, default, or "
        "format and re-load. Return ONLY a JSON object keyed by the exact error "
        "message, each value {\"root_cause\": \"...\", \"suggested_fix\": \"...\"}.\n\n"
        "ERRORS:\n" + json.dumps(list(distinct.values()), indent=1)
    )
    try:
        text = await _call_llm(provider, prompt)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        obj = json.loads(cleaned)
        if not isinstance(obj, dict):
            return errors
    except Exception as e:  # noqa: BLE001
        log.warning("AI load-error explanation failed (%s); keeping originals", e)
        return errors

    for err in errors:
        msg = (err.get("error_message") or "").strip()
        ai = obj.get(msg)
        if isinstance(ai, dict):
            rc = str(ai.get("root_cause", "")).strip()
            sf = str(ai.get("suggested_fix", "")).strip()
            if rc:
                err["root_cause"] = rc
            if sf:
                err["suggested_fix"] = sf
    return errors


async def _call_llm(provider: str, prompt: str) -> str:
    if provider == "anthropic":
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": settings.ANTHROPIC_MODEL or "claude-sonnet-4-6",
                "max_tokens": 2500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=50.0,
        )
        r.raise_for_status()
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                 "Content-Type": "application/json"},
        json={
            "model": settings.OPENAI_MODEL,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You output strict JSON only."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=50.0,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
