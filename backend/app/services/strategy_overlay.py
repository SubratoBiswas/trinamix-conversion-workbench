"""Write-time enforcement of the NextPower supplier conversion rules.

WHY THIS EXISTS
---------------
The strategy + analyst rules were originally seeded as client-scoped LearnedMappings.
Testing the generated FBDI proved that path does not reach the output: on both an
existing conversion AND a brand-new one, Business Relationship still emitted
PROSPECTIVE, Tax Organization Type still emitted raw source values ("Business",
"Coops"), Supplier Type regressed to raw values, and every "keep blank" rule was
ignored. The only rule that DID hold was the attribute-column block — because that
one is enforced at WRITE time, inside ``_transform_frame``, after mapping.

So the rules are applied here, at the same point, where nothing downstream can
undo them: a constant is a constant, a suppression is blank, a transform runs.
Mapping/learning remains the discovery mechanism; this is the guarantee.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent.parent / "data"
_FILE = _DATA / "supplier_strategy_defaults.json"
_cache: dict | None = None


def _n(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _load() -> dict:
    """{normalised target_object: {normalised field: directive}}"""
    global _cache
    if _cache is not None:
        return _cache
    out: dict[str, dict[str, dict]] = {}
    try:
        doc = json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing file just disables the overlay
        _cache = {}
        return _cache
    rules = list(doc.get("rules") or [])
    rules += list((doc.get("analyst_rules") or {}).get("rules") or [])
    for r in rules:
        obj, fld = _n(r.get("target_object")), _n(r.get("target_field"))
        if not obj or not fld or "*" in str(r.get("target_field") or ""):
            continue                      # wildcards handled by the attribute block
        d: dict = {}
        if r.get("suppress"):
            d["blank"] = True
        elif r.get("rule_type"):
            d["rule"] = {"rule_type": r["rule_type"], "config": r.get("rule_config") or {}}
        elif r.get("constant") is not None:
            d["constant"] = str(r["constant"])
            d["fill_blank_only"] = bool(r.get("fill_blank_only"))
        else:
            continue                      # 'derive' rows are mappings, not overlays
        out.setdefault(obj, {})[fld] = d
    _cache = out
    return _cache


def directive_for(target_object: str | None, field_name: str | None) -> dict | None:
    """The write-time directive for one target field, or None."""
    if not target_object or not field_name:
        return None
    return _load().get(_n(target_object), {}).get(_n(field_name))
