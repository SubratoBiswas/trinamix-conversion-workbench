"""AI data-quality review.

Complements the deterministic cleansing checks: sends the profiled source columns
(name, type, null%, distinct count, sample values, detected pattern) plus the
target Fusion object to Claude, which flags data-quality problems that trip up
FBDI loads — inconsistent date/phone/casing formats, embedded units, mixed
types, leading/trailing whitespace, likely-truncated values, placeholder junk
("N/A", "xxx"), invalid-looking codes, and so on — and proposes a concrete fix
for each. Issues are returned in the ValidationIssue shape so they render in the
existing cleansing UI alongside the rule-based ones.

Everything is best-effort: if AI is disabled or the call fails, an empty list is
returned and the deterministic checks stand on their own.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_MAX_COLS = 60
_ALLOWED_SEVERITY = {"info", "warning", "error"}


def _norm_name(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


async def ai_cleansing_issues(
    profiles: list[dict],
    mappings: list[dict],
    target_object: Optional[str],
) -> list[dict]:
    """Return AI-detected data-quality issues as ValidationIssue-compatible dicts
    (category='cleansing'). Empty on any error / when AI is disabled."""
    provider = (settings.AI_PROVIDER or "none").lower()
    if provider not in ("anthropic", "openai") or not profiles:
        return []

    mapped = {m.get("source_column"): m.get("target_field_name")
              for m in (mappings or []) if m.get("source_column")}
    cols = []
    for p in profiles[:_MAX_COLS]:
        name = p.get("column_name")
        cols.append({
            "column": name,
            "maps_to": mapped.get(name),
            "type": p.get("inferred_type"),
            "null_pct": p.get("null_percent"),
            "distinct": p.get("distinct_count"),
            "samples": [str(v) for v in (p.get("sample_values") or [])[:6]],
            "pattern": p.get("pattern_summary"),
        })
    # Normalized name lookup so the AI's field_name matches even if it drops a
    # space or changes case (was previously an exact match, which silently
    # dropped every AI issue when the model echoed a slightly different name).
    name_by_norm: dict[str, str] = {}
    for p in profiles:
        cn = p.get("column_name")
        if cn:
            name_by_norm[_norm_name(cn)] = cn

    prompt = (
        "You are an Oracle Fusion Cloud data-migration data-quality expert. The "
        f"columns below are a legacy extract being loaded into the '{target_object or 'Fusion'}' "
        "FBDI object. Identify data-quality problems that would cause load "
        "failures or bad data in Fusion — inconsistent formats (dates, phone, "
        "casing), embedded units or symbols, mixed types in one column, leading/"
        "trailing whitespace, placeholder junk (N/A, xxx, 0), likely truncation, "
        "invalid-looking codes, unexpected nulls in key fields, duplicates. "
        "For EACH real issue return an object with: field_name (exact column "
        "name), issue_type (short snake_case), severity (info|warning|error), "
        "message (one concise sentence), suggested_fix (the concrete transform to "
        "apply), auto_fixable (true if a deterministic transform fully fixes it). "
        "Only report genuine issues — do NOT invent problems for clean columns. "
        "Return ONLY a JSON array, at most 20 items, most important first.\n\n"
        f"COLUMNS:\n{json.dumps(cols, indent=1)}"
    )

    try:
        text = await _call_llm(provider, prompt)
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        arr = json.loads(cleaned)
        if isinstance(arr, dict):  # tolerate {"issues":[...]}
            arr = arr.get("issues", [])
        if not isinstance(arr, list):
            return []
    except Exception as e:  # noqa: BLE001
        log.warning("AI cleansing review failed (%s); rule-based only", e)
        return []

    out: list[dict] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        fname = name_by_norm.get(_norm_name(item.get("field_name")))
        if not fname:  # drop hallucinated columns
            continue
        sev = str(item.get("severity", "warning")).lower()
        if sev not in _ALLOWED_SEVERITY:
            sev = "warning"
        msg = str(item.get("message", "")).strip()
        if not msg:
            continue
        out.append({
            "category": "cleansing",
            "field_name": fname,
            "issue_type": str(item.get("issue_type", "ai_quality")).strip() or "ai_quality",
            "severity": sev,
            "message": f"{msg} (AI)",
            "suggested_fix": (str(item.get("suggested_fix", "")).strip() or None),
            "auto_fixable": bool(item.get("auto_fixable", False)),
            "impacted_count": 1,
        })
    return out[:20]


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
                "max_tokens": 4000,
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
                {"role": "user", "content": prompt + '\nWrap the array under key "issues".'},
            ],
        },
        timeout=50.0,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
