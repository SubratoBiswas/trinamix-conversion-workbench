"""Prompt-steering for a conversion's output.

Lets a user type plain instructions to override the tool's mapping/defaults,
e.g.:
    default Business Relationship to PROSPECTIVE
    set Tax Organization Type = Corporation
    map Supplier Name from VENDOR_NAME
    Federal reportable as N

Each directive is matched to a target field of the conversion's template (by a
normalized name) and written as an approved MappingSuggestion, so Generate
Output honours it. Deterministic and testable (no LLM call); richer natural-
language steering can be layered on top later.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from app.models.conversion import Conversion
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion

log = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower().strip("*"))


async def _learn(business_object, target_field, *, kind, original, resolved,
                 rule_type=None, client_id=None):
    """Store a steering directive as a reusable, CLIENT-SCOPED learned rule so it
    applies to future conversions of the same object for this client (and never
    leaks to another client)."""
    if not business_object or not target_field:
        return
    existing = await LearnedMapping.find_one(
        LearnedMapping.kind == kind,
        LearnedMapping.target_object == business_object,
        LearnedMapping.target_field == target_field,
        LearnedMapping.client_id == client_id,
    )
    doc = {
        "kind": kind, "category": "Steering (prompt)",
        "original_value": str(original), "resolved_value": str(resolved),
        "target_object": business_object, "target_field": target_field,
        "rule_type": rule_type, "client_id": client_id, "is_global": False,
        "captured_from": "prompt", "captured_at": datetime.utcnow(),
    }
    if existing:
        await existing.set(doc)
    else:
        await LearnedMapping(**doc).insert()


# "clear X" / "blank X" / "leave X blank" / "don't map X" / "do not populate X"
# / "remove X"  → suppress (keep the field empty, overriding AI mapping)
_SUPPRESS_RES = [
    re.compile(r"^\s*(?:clear|blank|empty|remove|skip)\s+(?P<f>.+?)\s*$", re.I),
    re.compile(r"^\s*(?:don'?t|do not)\s+(?:map|populate|fill|use)\s+(?P<f>.+?)\s*$", re.I),
    re.compile(r"^\s*leave\s+(?P<f>.+?)\s+(?:blank|empty)\s*$", re.I),
    re.compile(r"^\s*(?P<f>.+?)\s+(?:should be|must be)\s+(?:blank|empty)\s*$", re.I),
]
# "default X to Y" / "set X = Y" / "X as Y"  (default value)
_DEFAULT_RES = [
    re.compile(r"^\s*(?:default|set|make)\s+(?P<f>.+?)\s+(?:to|=|as)\s+(?P<v>.+?)\s*$", re.I),
    re.compile(r"^\s*(?P<f>.+?)\s+(?:should be|defaults? to)\s+(?P<v>.+?)\s*$", re.I),
]
# "map X from Y" / "X from source Y"  (source column)
_MAP_RES = [
    re.compile(r"^\s*(?:map|pull|take)\s+(?P<f>.+?)\s+from\s+(?P<c>.+?)\s*$", re.I),
    re.compile(r"^\s*(?P<f>.+?)\s+from\s+(?:source\s+)?(?P<c>.+?)\s*$", re.I),
]


async def _upsert(conversion, field, *, source_column, default_value, reason,
                  business_object=None, client_id=None):
    existing = await MappingSuggestion.find_one(
        MappingSuggestion.conversion_id == conversion.id,
        MappingSuggestion.target_field_id == field.id,
    )
    payload = {
        "source_column": source_column,
        "default_value": default_value,
        "confidence": 1.0,
        "reason": reason,
        "status": "approved",
        "review_required": 0,
        "updated_at": datetime.utcnow(),
    }
    if existing:
        await existing.set(payload)
    else:
        await MappingSuggestion(conversion_id=conversion.id, target_field_id=field.id, **payload).insert()
    # Persist as a reusable client-scoped rule.
    if source_column:
        await _learn(business_object, field.field_name, kind="column_mapping",
                     original=source_column, resolved=field.field_name, client_id=client_id)
    elif default_value is not None:
        await _learn(business_object, field.field_name, kind="example_default",
                     original="(default)", resolved=default_value, rule_type="default",
                     client_id=client_id)


async def _suppress(conversion, field, business_object, client_id=None):
    """Force a field to stay blank (status not_applicable), overriding AI mapping,
    and learn it as a reusable client-scoped suppression for this object."""
    existing = await MappingSuggestion.find_one(
        MappingSuggestion.conversion_id == conversion.id,
        MappingSuggestion.target_field_id == field.id,
    )
    payload = {
        "source_column": None, "default_value": None, "confidence": 1.0,
        "reason": "prompt: leave blank", "status": "not_applicable",
        "review_required": 0, "updated_at": datetime.utcnow(),
    }
    if existing:
        await existing.set(payload)
    else:
        await MappingSuggestion(conversion_id=conversion.id, target_field_id=field.id, **payload).insert()
    await _learn(business_object, field.field_name, kind="suppress_field",
                 original="(blank)", resolved="", rule_type="suppress", client_id=client_id)


async def _ai_parse_directives(lines: list[str], field_names: list[str]) -> list[dict]:
    """Use the Claude API to turn free-form English steering lines the regex parser
    couldn't handle into structured directives. Returns a list of
    {action: map|default|suppress, field, value?, source?}. Best-effort: on any
    error (no key, network, bad JSON) returns [] so steering still works offline."""
    from app.config import settings
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key or not lines:
        return []
    import httpx
    fields_blob = "\n".join(f"- {n}" for n in field_names[:400])
    prompt = (
        "You map plain-English data-migration instructions to structured directives "
        "against a fixed list of Oracle FBDI target fields.\n\n"
        "TARGET FIELDS:\n" + fields_blob + "\n\n"
        "INSTRUCTIONS (one per line):\n" + "\n".join(f"- {l}" for l in lines) + "\n\n"
        "For each instruction you can confidently act on, output one JSON object with:\n"
        '  "action": "map" | "default" | "suppress",\n'
        '  "field": the EXACT target field name from the list it applies to,\n'
        '  "value": the constant (for action=default), or\n'
        '  "source": the source column name (for action=map).\n'
        "Use 'suppress' when the user wants the field left blank / not populated. "
        "Only include instructions you can match to a listed field. "
        'Respond with ONLY a JSON array, e.g. [{"action":"default","field":"Business Relationship","value":"PROSPECTIVE"}].'
    )
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": getattr(settings, "ANTHROPIC_MODEL", None) or "claude-sonnet-4-6",
                      "max_tokens": 1500,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", [])
                           if b.get("type") == "text")
        s, e = text.find("["), text.rfind("]")
        if s == -1 or e == -1:
            return []
        out = json.loads(text[s:e + 1])
        return [d for d in out if isinstance(d, dict) and d.get("action") and d.get("field")]
    except Exception as exc:  # noqa: BLE001
        log.warning("steering AI parse failed: %s", exc)
        return []


async def apply_steer_prompt(conversion: Conversion, prompt: str) -> dict:
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    if not template:
        return {"applied": [], "unmatched": [], "error": "no template"}
    business_object = template.business_object or conversion.target_object
    from app.services.client_service import client_id_for_conversion
    client_id = await client_id_for_conversion(conversion)
    fields = await FBDIField.find(FBDIField.template_id == template.id).to_list()
    by_key = {}
    for f in fields:
        by_key.setdefault(_norm(f.field_name), f)

    def find_field(name: str):
        k = _norm(name)
        if k in by_key:
            return by_key[k]
        # loose contains match as a fallback
        for kk, f in by_key.items():
            if k and (k in kk or kk in k):
                return f
        return None

    applied, unmatched = [], []
    # split on newlines, semicolons, and " and " between clauses
    lines = re.split(r"[\n;]+", prompt or "")
    for raw in lines:
        line = raw.strip().rstrip(".")
        if not line:
            continue
        handled = False
        # Suppression first — "leave X blank" must not be read as "default X to blank".
        for rx in _SUPPRESS_RES:
            m = rx.match(line)
            if m:
                f = find_field(m.group("f"))
                if f:
                    await _suppress(conversion, f, business_object, client_id)
                    applied.append({"field": f.field_name, "suppressed": True})
                    handled = True
                break
        if handled:
            continue
        for rx in _MAP_RES:
            m = rx.match(line)
            if m:
                f = find_field(m.group("f"))
                if f:
                    await _upsert(conversion, f, source_column=m.group("c").strip(),
                                  default_value=None, reason="prompt: map",
                                  business_object=business_object, client_id=client_id)
                    applied.append({"field": f.field_name, "source": m.group("c").strip()})
                    handled = True
                break
        if handled:
            continue
        for rx in _DEFAULT_RES:
            m = rx.match(line)
            if m:
                f = find_field(m.group("f"))
                if f:
                    val = m.group("v").strip().strip('"\'')
                    await _upsert(conversion, f, source_column=None, default_value=val,
                                  reason="prompt: default",
                                  business_object=business_object, client_id=client_id)
                    applied.append({"field": f.field_name, "default": val})
                    handled = True
                break
        if not handled:
            unmatched.append(line)

    # AI fallback: anything the regex parser couldn't place is sent to Claude, which
    # returns structured directives against the real field list. Applied + stored as
    # client-scoped rules exactly like the deterministic ones. No key / offline → the
    # lines simply stay in `unmatched`, so steering still works without AI.
    ai_used = False
    if unmatched:
        directives = await _ai_parse_directives(unmatched, [f.field_name for f in fields])
        if directives:
            ai_used = True
            still: list[str] = []
            resolved_lines = {d.get("field", "").lower() for d in directives}
            for d in directives:
                f = find_field(str(d.get("field", "")))
                if not f:
                    continue
                action = str(d.get("action", "")).lower()
                if action == "suppress":
                    await _suppress(conversion, f, business_object, client_id)
                    applied.append({"field": f.field_name, "suppressed": True, "via": "ai"})
                elif action == "map" and d.get("source"):
                    await _upsert(conversion, f, source_column=str(d["source"]).strip(),
                                  default_value=None, reason="prompt (AI): map",
                                  business_object=business_object, client_id=client_id)
                    applied.append({"field": f.field_name, "source": str(d["source"]).strip(), "via": "ai"})
                elif action == "default" and d.get("value") is not None:
                    await _upsert(conversion, f, source_column=None, default_value=str(d["value"]),
                                  reason="prompt (AI): default",
                                  business_object=business_object, client_id=client_id)
                    applied.append({"field": f.field_name, "default": str(d["value"]), "via": "ai"})
            # keep as unmatched only the lines the AI didn't resolve to a field
            unmatched = [l for l in unmatched
                         if not any(rf and rf in l.lower() for rf in resolved_lines)]

    return {"applied": applied, "unmatched": unmatched, "ai_used": ai_used}
