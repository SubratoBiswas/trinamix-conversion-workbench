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

import re
from datetime import datetime

from app.models.conversion import Conversion
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.mapping import MappingSuggestion


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower().strip("*"))


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


async def _upsert(conversion, field, *, source_column, default_value, reason):
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


async def apply_steer_prompt(conversion: Conversion, prompt: str) -> dict:
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    if not template:
        return {"applied": [], "unmatched": [], "error": "no template"}
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
        for rx in _MAP_RES:
            m = rx.match(line)
            if m:
                f = find_field(m.group("f"))
                if f:
                    await _upsert(conversion, f, source_column=m.group("c").strip(),
                                  default_value=None, reason="prompt: map")
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
                                  reason="prompt: default")
                    applied.append({"field": f.field_name, "default": val})
                    handled = True
                break
        if not handled:
            unmatched.append(line)
    return {"applied": applied, "unmatched": unmatched}
