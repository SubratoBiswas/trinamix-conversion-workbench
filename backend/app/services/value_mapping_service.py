"""Value crosswalks — normalize legacy source values to valid Oracle Fusion
codes for a single mapped column.

This resolves the classic FBDI validation failure: the source system uses its
own value vocabulary ("United States", "Kilogram", "Retire", "Net 30") but
Fusion expects specific lookup codes ("US", "KG", "INACTIVE", a payment-terms
id). For each distinct value in the mapped source column we try, in order:

  1. a previously learned crosswalk (this conversion or the reusable library),
  2. deterministic resolution against the target field's list of values
     (exact code / exact meaning / fuzzy),
  3. AI normalization (Claude Sonnet) for whatever is left — the model knows the
     standard Oracle codes for common domains (country, currency, UOM, status,
     Y/N flags, etc.) even when no explicit LOV is stored on the field.

AI results are cached as learned crosswalks on acceptance, so they're instant
and consistent next time. Everything degrades gracefully: if AI is unavailable
the deterministic pass still runs, and if there's no LOV at all we simply return
the values as-is for manual review.

Public API (imported by routers/mapping.py and ai/rule_based.py):
  _norm, resolve_value, lov_coverage, recommend_value_map, accept_value_map
"""
from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher
from typing import Any, Optional

import httpx

from app.config import settings
from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.fbdi import FBDIField
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion
from app.models.transformation import Crosswalk, TransformationRule
from app.parsers import parse_tabular

log = logging.getLogger(__name__)

_MAX_VALUES = 200          # cap distinct values we consider per column
_FUZZY_MIN = 0.88          # min ratio to accept a fuzzy LOV match


def _norm(s: Any) -> str:
    """Loose normalization for value comparison: lowercase, strip, collapse
    non-alphanumerics. 'United States ' and 'united-states' compare equal."""
    return re.sub(r"[^a-z0-9]+", "", str(s or "").strip().lower())


def _lov_pairs(allowed_values: list[dict]) -> list[tuple[str, str]]:
    """Return [(code, meaning)] from a list-of-values definition."""
    out: list[tuple[str, str]] = []
    for lv in allowed_values or []:
        if isinstance(lv, dict):
            code = str(lv.get("code", lv.get("value", ""))).strip()
            meaning = str(lv.get("meaning", lv.get("label", "")) or "").strip()
        else:
            code = str(lv).strip()
            meaning = ""
        if code:
            out.append((code, meaning))
    return out


def resolve_value(
    value: Any, allowed_values: list[dict]
) -> tuple[Optional[str], str, float]:
    """Deterministically resolve one source value to a target LOV code.

    Returns (code, method, confidence). code is None when nothing matches.
    method ∈ {exact_code, exact_meaning, fuzzy}."""
    pairs = _lov_pairs(allowed_values)
    if not pairs:
        return None, "", 0.0
    nv = _norm(value)
    if not nv:
        return None, "", 0.0
    # exact code
    for code, _meaning in pairs:
        if _norm(code) == nv:
            return code, "exact_code", 1.0
    # exact meaning
    for code, meaning in pairs:
        if meaning and _norm(meaning) == nv:
            return code, "exact_meaning", 0.97
    # fuzzy on code or meaning
    best_code, best_ratio = None, 0.0
    for code, meaning in pairs:
        r = max(
            SequenceMatcher(None, nv, _norm(code)).ratio(),
            SequenceMatcher(None, nv, _norm(meaning)).ratio() if meaning else 0.0,
        )
        if r > best_ratio:
            best_code, best_ratio = code, r
    if best_code is not None and best_ratio >= _FUZZY_MIN:
        return best_code, "fuzzy", round(best_ratio, 2)
    return None, "", 0.0


def lov_coverage(vals: list, lov: list[dict]) -> tuple[float, int]:
    """Fraction of *vals* that resolve against *lov*, and the hit count. Used by
    the rule-based mapper to score how well a source column fits a LOV target."""
    if not vals or not lov:
        return 0.0, 0
    hits = sum(1 for v in vals if resolve_value(v, lov)[0] is not None)
    return (hits / len(vals)), hits


async def _source_distinct_values(conv: Conversion, source_column: str) -> list[str]:
    """Distinct non-null values of the mapped source column (durable read,
    capped). Best-effort — returns [] if the dataset can't be read."""
    if not source_column or not conv.dataset_id:
        return []
    ds = await Dataset.get(conv.dataset_id)
    if not ds:
        return []
    path = ds.file_path
    try:
        from app.services.dataset_file_store import materialize_dataset_file
        mp = await materialize_dataset_file(ds)
        if mp:
            path = str(mp)
    except Exception:
        pass
    try:
        df = parse_tabular(path, file_type=ds.file_type)
        if source_column not in df.columns:
            return []
        ser = df[source_column].dropna().astype(str).str.strip()
        vals = [v for v in ser.unique().tolist()
                if v and v.lower() not in ("nan", "none", "null")]
        return vals[:_MAX_VALUES]
    except Exception as e:  # noqa: BLE001
        log.warning("crosswalk: could not read source values (%s)", e)
        return []


async def _ai_crosswalk(
    field_name: str, description: Optional[str],
    values: list[str], allowed_values: list[dict],
) -> dict[str, str]:
    """Ask Claude to normalize legacy values to Oracle Fusion codes.

    Returns {source_value: target_code} for values that need a change. Empty on
    any error or when AI is disabled — the deterministic path still applies."""
    provider = (settings.AI_PROVIDER or "none").lower()
    if provider not in ("anthropic", "openai") or not values:
        return {}
    pairs = _lov_pairs(allowed_values)
    lov_hint = ""
    if pairs:
        listed = "; ".join(f"{c}" + (f' ({m})' if m else "") for c, m in pairs[:60])
        lov_hint = (
            "\nThe ONLY valid target codes are: " + listed +
            ". Map each source value to one of these exact codes."
        )
    else:
        lov_hint = (
            "\nUse the standard Oracle Fusion Cloud code/value for this field "
            "(e.g. country ISO like US/GB/CA, currency like USD/EUR, UOM like "
            "EA/KG/LB, Y/N flags, status codes)."
        )
    prompt = (
        "You are an Oracle Fusion Cloud data-migration expert normalizing legacy "
        f"values for the FBDI field '{field_name}'"
        + (f" ({description})." if description else ".")
        + lov_hint +
        "\n\nFor EACH source value below, return the correct Oracle Fusion code. "
        "If a value is ALREADY a valid Fusion code (no change needed) or you "
        "cannot map it confidently, OMIT it. Return ONLY a JSON object mapping "
        "the exact source value to its Fusion code.\n\nSOURCE VALUES:\n"
        + "\n".join(f"- {v}" for v in values[:_MAX_VALUES])
        + '\n\nExample: {"United States": "US", "Kilogram": "KG"}'
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
                    "model": settings.ANTHROPIC_MODEL or "claude-sonnet-4-6",
                    "max_tokens": 2000,
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
        # keep only values we actually asked about, non-empty, and changed
        vset = {str(v): v for v in values}
        out: dict[str, str] = {}
        for k, code in obj.items():
            if code is None:
                continue
            src = vset.get(str(k))
            if src is None:
                continue
            code = str(code).strip()
            if code and _norm(code) != _norm(src):
                out[src] = code
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("AI crosswalk failed (%s); deterministic only", e)
        return {}


async def recommend_value_map(conv: Conversion, mapping: MappingSuggestion) -> dict:
    """Recommend source→target value pairs (crosswalk) for one mapping.

    Returns {"field", "source_column", "recommendations": [...], "unresolved": N,
             "ai_used": bool}. Each recommendation:
      {source_value, target_value, method, confidence, already_valid}.
    """
    field = await FBDIField.get(mapping.target_field_id)
    if not field or not mapping.source_column:
        return {"field": None, "recommendations": [], "unresolved": 0, "ai_used": False}

    allowed = getattr(field, "allowed_values", None) or []
    values = await _source_distinct_values(conv, mapping.source_column)
    if not values:
        return {"field": field.field_name, "source_column": mapping.source_column,
                "recommendations": [], "unresolved": 0, "ai_used": False}

    # Learned crosswalks: this conversion's own + the reusable library (by field).
    learned: dict[str, str] = {}
    async for cw in Crosswalk.find(Crosswalk.conversion_id == conv.id):
        if cw.field_name == field.field_name:
            learned[cw.source_value] = cw.target_value
    async for lm in LearnedMapping.find(
        LearnedMapping.kind == "crosswalk", LearnedMapping.target_field == field.field_name
    ):
        learned.setdefault(lm.original_value, lm.resolved_value)

    recs: list[dict] = []
    unmatched: list[str] = []
    ai_candidates: list[str] = []
    for v in values:
        if v in learned:
            recs.append({"source_value": v, "target_value": learned[v],
                         "method": "learned", "confidence": 0.99, "already_valid": False})
            continue
        code, method, conf = resolve_value(v, allowed)
        if code is not None:
            already = _norm(code) == _norm(v)
            recs.append({"source_value": v, "target_value": code, "method": method,
                         "confidence": conf, "already_valid": already})
        else:
            ai_candidates.append(v)

    ai_used = False
    if ai_candidates:
        ai_map = await _ai_crosswalk(field.field_name, field.description, ai_candidates, allowed)
        for v in ai_candidates:
            if v in ai_map:
                ai_used = True
                recs.append({"source_value": v, "target_value": ai_map[v],
                             "method": "ai", "confidence": 0.9, "already_valid": False})
            else:
                unmatched.append(v)  # surface for manual mapping

    coverage = (len(recs) / len(values)) if values else 0.0
    return {
        "target_field": field.field_name,
        "lov": [{"code": c, "meaning": m} for c, m in _lov_pairs(allowed)],
        "default_if_blank": getattr(field, "default_if_blank", None),
        "source_column": mapping.source_column,
        "distinct_values": values,
        "recommendations": recs,
        "unmatched": unmatched,
        "coverage": round(coverage, 3),
        "ai_used": ai_used,
    }


async def accept_value_map(
    conv: Conversion, mapping: MappingSuggestion, *,
    pairs: list[dict], default_value: Optional[str] = None, user_email: str = "",
) -> dict:
    """Persist accepted value pairs: upsert a VALUE_MAP transformation rule on the
    target field (applied at Generate Output) and learn each pair into the
    Crosswalk Library (conversion-scoped + reusable) for future conversions."""
    field = await FBDIField.get(mapping.target_field_id)
    if not field:
        return {"error": "Target field not found"}

    value_map = {str(p["source_value"]): str(p["target_value"])
                 for p in pairs if p.get("target_value") not in (None, "")}

    # Upsert the VALUE_MAP rule for this (conversion, target field).
    rule = await TransformationRule.find_one(
        TransformationRule.conversion_id == conv.id,
        TransformationRule.target_field_id == field.id,
        TransformationRule.rule_type == "VALUE_MAP",
    )
    # The transformation engine reads the value pairs as TOP-LEVEL config keys
    # (reserved keys: case_insensitive, default). Merge with any existing pairs.
    def _pairs_only(c: dict) -> dict:
        return {k: v for k, v in (c or {}).items()
                if k not in ("case_insensitive", "default")}

    if rule:
        merged = _pairs_only(rule.rule_config)
        merged.update(value_map)
        cfg = dict(merged)
    else:
        cfg = dict(value_map)
    cfg["case_insensitive"] = True
    if default_value not in (None, ""):
        cfg["default"] = default_value

    if rule:
        await rule.set({"rule_config": cfg, "source_column": mapping.source_column,
                        "description": f"Value crosswalk for {field.field_name}"})
        rule_id = str(rule.id)
    else:
        rule = TransformationRule(
            conversion_id=conv.id, target_field_id=field.id,
            source_column=mapping.source_column, rule_type="VALUE_MAP",
            rule_config=cfg, description=f"Value crosswalk for {field.field_name}",
        )
        await rule.insert()
        rule_id = str(rule.id)

    # Learn each pair: conversion-scoped Crosswalk + reusable LearnedMapping.
    learned = 0
    for src, tgt in value_map.items():
        exists = await Crosswalk.find_one(
            Crosswalk.conversion_id == conv.id,
            Crosswalk.field_name == field.field_name,
            Crosswalk.source_value == src,
        )
        if exists:
            await exists.set({"target_value": tgt})
        else:
            await Crosswalk(conversion_id=conv.id, name=field.field_name,
                            field_name=field.field_name, source_value=src,
                            target_value=tgt).insert()
        lm = await LearnedMapping.find_one(
            LearnedMapping.kind == "crosswalk",
            LearnedMapping.target_field == field.field_name,
            LearnedMapping.original_value == src,
        )
        if not lm:
            await LearnedMapping(
                kind="crosswalk", category="Value Crosswalk",
                original_value=src, resolved_value=tgt,
                target_object=conv.target_object, target_field=field.field_name,
                rule_type="VALUE_MAP", captured_from="value-map-accept",
                captured_by=user_email,
            ).insert()
        learned += 1

    return {"rule_id": rule_id, "field": field.field_name,
            "pairs_applied": len(value_map), "learned": learned}
