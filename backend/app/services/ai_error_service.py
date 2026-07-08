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
import re
from typing import Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_MAX_DISTINCT = 40

# Deterministic explanations for common FBDI load errors — matched by regex so
# the LLM is only called for messages we don't already recognize (cuts tokens).
_STATIC_PATTERNS: list[tuple] = [
    (re.compile(r"required|mandatory|has no value|is null", re.I),
     "A required Fusion field is empty for this row.",
     "Map a source column to it, or set a default in Mapping Review, then re-load."),
    (re.compile(r"confiden", re.I),
     "The auto-mapping for this field is low-confidence and may be wrong.",
     "Review and approve/correct the mapping in Mapping Review, then regenerate."),
    (re.compile(r"invalid.*(lookup|value|code)|not a valid|no matching", re.I),
     "The value isn't a valid Fusion lookup/reference code.",
     "Add a value crosswalk (source value → Fusion code) for this field and re-load."),
    (re.compile(r"date.*(format|invalid)|invalid date", re.I),
     "The date value isn't in Fusion's expected format.",
     "Apply a DATE_FORMAT transform (e.g. output YYYY/MM/DD) to this column."),
    (re.compile(r"duplicate", re.I),
     "A duplicate key would create a conflicting record.",
     "De-duplicate the source rows or adjust the key field."),
    (re.compile(r"(not found|does not exist|no such).*(supplier|parent|site|party)|parent .*missing", re.I),
     "A referenced parent record doesn't exist yet.",
     "Load the parent object first — follow the supplier load sequence."),
    (re.compile(r"(numeric|number).*(invalid|expected|required)|non-?numeric", re.I),
     "A numeric field received a non-numeric value.",
     "Strip non-numeric characters (e.g. punctuation) so only digits remain."),
    (re.compile(r"too long|exceeds|max(imum)? length|truncat", re.I),
     "The value exceeds the field's maximum length.",
     "Truncate or abbreviate the value to the field's max length."),
]


def _static_explain(msg: str) -> Optional[dict]:
    for rx, root, fix in _STATIC_PATTERNS:
        if rx.search(msg):
            return {"root_cause": root, "suggested_fix": fix}
    return None


async def explain_load_errors(errors: list[dict], object_name: Optional[str]) -> list[dict]:
    """Enrich error dicts (LoadError fields) with an AI root_cause + suggested_fix,
    keyed by distinct error_message. Returns the same list, enriched in place.
    Empty AI / errors are handled gracefully."""
    if not errors:
        return errors
    provider = (settings.AI_PROVIDER or "none").lower()

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

    # Deterministic-first: explain known error patterns with no LLM; only the
    # messages we don't recognize go to the model.
    obj: dict[str, dict] = {}
    residual: dict[str, dict] = {}
    for msg, meta in distinct.items():
        st = _static_explain(msg)
        if st:
            obj[msg] = st
        else:
            residual[msg] = meta

    if residual and provider in ("anthropic", "openai"):
        prompt = (
            "You are an Oracle Fusion Cloud FBDI load-error expert. For each error "
            f"below (from loading the '{object_name or 'Fusion'}' object), give a "
            "concise plain-English ROOT CAUSE and a concrete FIX telling the analyst "
            "exactly what to change in the mapping, value crosswalk, default, or "
            "format and re-load. Return ONLY a JSON object keyed by the exact error "
            "message, each value {\"root_cause\": \"...\", \"suggested_fix\": \"...\"}.\n\n"
            "ERRORS:\n" + json.dumps(list(residual.values()), indent=1)
        )
        try:
            text = await _call_llm(provider, prompt)
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                if cleaned.lower().startswith("json"):
                    cleaned = cleaned[4:].strip()
            ai_obj = json.loads(cleaned)
            if isinstance(ai_obj, dict):
                obj.update(ai_obj)
        except Exception as e:  # noqa: BLE001
            log.warning("AI load-error explanation failed (%s); static only", e)

    if not obj:
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
