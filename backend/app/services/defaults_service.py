"""Effective control-defaults for a conversion.

These are the values written at Generate Output for target FBDI fields that have
NO source column — standardization constants, not data pulled from the extract.
The mapping-review UI fetches this so it can show "defaulted -> value" instead of
a misleading red "required gap" for such fields.

Sources, in priority order per field:
  1. the conversion's own mapping.default_value (learned from a gold example)
  2. an example_default LearnedMapping captured for this target object (reusable)
  3. a running-key sequence field (output_service._SEQ_FIELDS)
  4. a static control constant (output_service._CONTROL_DEFAULTS)
  5. AI-inferred constant for a required field with no known default — only when
     AI_PROVIDER is configured; the result is cached as an example_default
     LearnedMapping so it's instant next time, consistent across engagements, and
     never re-billed. If AI is off or the call fails, the field simply stays a
     genuine required gap (deterministic behaviour, product never breaks).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

from app.config import settings
from app.models.conversion import Conversion
from app.models.fbdi import FBDIField
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion
from app.services.output_service import _CONTROL_DEFAULTS, _SEQ_FIELDS

log = logging.getLogger(__name__)


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower().rstrip("*").strip()


async def _ai_infer_defaults(target_object: str, fields: list[dict]) -> dict[str, str]:
    """Ask the configured LLM for standard constant defaults for the given FBDI
    fields. Returns {normalized_field: value}. Empty dict when AI is disabled or
    on any error, so the deterministic path always keeps working."""
    provider = (settings.AI_PROVIDER or "none").lower()
    if provider not in ("anthropic", "openai") or not fields:
        return {}
    listing = "\n".join(
        f'- {f["label"]}' + (f' ({f["description"]})' if f.get("description") else "")
        for f in fields
    )
    prompt = (
        "You are an Oracle Fusion Cloud FBDI data-migration expert. For the "
        f"interface object '{target_object or 'this object'}', the target fields "
        "below have NO source column in the legacy extract. For EACH field, if "
        "Oracle expects a standard CONSTANT or enumerated default (an import "
        "action, a Y/N flag, a lookup code, an organization type, etc.), return "
        "that value. If the field genuinely needs per-row data (names, addresses, "
        "ids, amounts, dates, emails), OMIT it entirely. Return ONLY a JSON "
        "object mapping the exact field label to its constant value.\n\n"
        f"FIELDS:\n{listing}\n\n"
        'Example: {"Import Action *": "CREATE", "Federal Reportable Flag": "N"}'
    )
    try:
        if provider == "anthropic":
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    # Latest Claude Sonnet for the constant-inference task.
                    # Honors an explicit ANTHROPIC_MODEL override, else the
                    # config default (claude-sonnet-4-6).
                    "model": settings.ANTHROPIC_MODEL or "claude-sonnet-4-6",
                    "max_tokens": 1500,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=45.0,
            )
            r.raise_for_status()
            data = r.json()
            text = "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )
        else:
            r = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENAI_MODEL,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": "You output strict JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=45.0,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        obj = json.loads(cleaned)
        if not isinstance(obj, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in obj.items():
            if v is None:
                continue
            out[_norm(str(k))] = str(v)
        return out
    except Exception as e:  # noqa: BLE001 -- never break the request over AI
        log.warning("AI default inference failed (%s); deterministic defaults only", e)
        return {}


async def compute_effective_defaults(conversion: Conversion, use_ai: bool = True) -> dict:
    """Return effective defaults for every unmapped target field of a conversion.

    Shape: {"defaults": {norm_field: value},
            "detail":   [{"field","label","value","source"}],
            "ai_used":  bool}
    """
    if not conversion.template_id:
        return {"defaults": {}, "detail": [], "ai_used": False}

    fields = await FBDIField.find(FBDIField.template_id == conversion.template_id).to_list()
    maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conversion.id
    ).to_list()
    by_fid = {m.target_field_id: m for m in maps}
    target_object = conversion.target_object or ""

    # Reusable constants captured from gold examples for this object.
    learned: dict[str, str] = {}
    if target_object:
        async for lm in LearnedMapping.find(
            LearnedMapping.kind == "example_default",
            LearnedMapping.target_object == target_object,
        ):
            if lm.target_field and lm.resolved_value:
                learned[_norm(lm.target_field)] = lm.resolved_value

    defaults: dict[str, str] = {}
    detail: list[dict] = []
    ai_candidates: list[dict] = []

    for f in fields:
        m = by_fid.get(f.id)
        if m and m.source_column:
            continue  # mapped to a real source column -> not a default
        norm = _norm(f.field_name)
        normc = norm.replace(" ", "")
        value: Optional[str] = None
        source = ""
        if m and m.default_value:
            value, source = m.default_value, "learned"
        elif norm in learned:
            value, source = learned[norm], "learned"
        elif normc in _SEQ_FIELDS:
            value, source = "auto-number (100000+)", "sequence"
        elif norm in _CONTROL_DEFAULTS:
            value, source = _CONTROL_DEFAULTS[norm], "control"

        if value is not None:
            defaults[norm] = value
            detail.append({"field": norm, "label": f.field_name, "value": value, "source": source})
        elif f.required:
            ai_candidates.append(
                {"label": f.field_name, "norm": norm, "description": (f.description or "")[:120]}
            )

    ai_used = False
    if ai_candidates and use_ai:
        ai_map = await _ai_infer_defaults(target_object, ai_candidates)
        for c in ai_candidates:
            v = ai_map.get(c["norm"])
            if not v:
                continue
            ai_used = True
            defaults[c["norm"]] = v
            detail.append({"field": c["norm"], "label": c["label"], "value": v, "source": "ai"})
            # Cache as reusable example_default (instant + consistent next time).
            exists = await LearnedMapping.find_one(
                LearnedMapping.kind == "example_default",
                LearnedMapping.target_object == target_object,
                LearnedMapping.target_field == c["label"],
            )
            if not exists:
                await LearnedMapping(
                    kind="example_default",
                    category="Default Value",
                    original_value="(ai)",
                    resolved_value=v,
                    target_object=target_object,
                    target_field=c["label"],
                    rule_type="default",
                    rule_config={"default_value": v},
                    captured_from="ai-inference",
                ).insert()

    return {"defaults": defaults, "detail": detail, "ai_used": ai_used}
