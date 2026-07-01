"""Value-pair (crosswalk / LOV) recommendation service.

Implements the VRS requirement that mapping must look at the *data inside*
columns, not just column names:

  * ``resolve_value`` — translate one legacy source value to a destination
    LOV code using exact / meaning / synonym / fuzzy strategies.
  * ``recommend_value_map`` — for a mapping whose target FBDI field has a
    list of values, propose a full source→target value map with per-pair
    confidence, and flag unmatched source values as exceptions.
  * ``accept_value_map`` — persist accepted pairs as a VALUE_MAP
    transformation rule on the field (applied at output generation) and
    learn each pair into the Crosswalk Library (LearnedMapping kind
    ``crosswalk``) so future conversions auto-recommend them.
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime
from typing import Any

import pandas as pd

from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.fbdi import FBDIField
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion
from app.models.transformation import TransformationRule
from app.parsers import parse_tabular

# ── Synonym groups ────────────────────────────────────────────────────────────
# Canonical concept -> spellings seen in legacy ERPs (NetSuite, SyteLine,
# Arena, EBS...). Both source values and LOV meanings are normalised through
# these groups, so "Retire" ↔ "Inactive" ↔ "Disabled" all meet in the middle.
SYNONYM_GROUPS: dict[str, tuple[str, ...]] = {
    "active": ("active", "enabled", "current", "released", "approved", "live", "open", "in use"),
    "inactive": ("inactive", "disabled", "retired", "retire", "obsolete", "discontinued",
                 "end of life", "eol", "hold", "on hold", "closed", "archived", "expired"),
    "pending": ("pending", "draft", "created", "new", "in review", "preliminary", "prototype"),
    "yes": ("yes", "y", "true", "t", "1", "on", "checked", "enabled"),
    "no": ("no", "n", "false", "f", "0", "off", "unchecked", "disabled"),
    "mrp planned": ("mrp", "mrp planned", "mrp planning", "material requirements planning"),
    "mps planned": ("mps", "mps planned", "mps planning", "master scheduled", "master schedule",
                    "master production schedule"),
    "not planned": ("not planned", "none", "unplanned", "no planning", "no plan", "manual"),
    "days of cover": ("days of cover", "days of supply", "days supply", "coverage days", "days cover"),
    "make": ("make", "manufactured", "manufacture", "produced", "in house", "internal"),
    "buy": ("buy", "purchased", "purchase", "procured", "external", "vendor supplied"),
    "organization": ("organization", "org", "plant", "site", "facility", "warehouse"),
    "standard": ("standard", "std", "normal", "regular"),
}

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: Any) -> str:
    """Lowercase, collapse punctuation/whitespace to single spaces."""
    return _NORM_RE.sub(" ", str(s or "").lower()).strip()


def _concept(s: Any) -> str | None:
    """Return the canonical synonym-group key for a value, if any."""
    n = _norm(s)
    if not n:
        return None
    for canon, aliases in SYNONYM_GROUPS.items():
        if n == canon or n in aliases:
            return canon
    return None


def resolve_value(value: Any, lov: list[dict]) -> tuple[str | None, str, float]:
    """Resolve one source value against a destination LOV.

    Each LOV entry is ``{"code": ..., "meaning": ...}`` (meaning optional).
    Returns ``(code | None, method, confidence)``. Strategy chain, strongest
    first — exact code, exact meaning, synonym concept, fuzzy similarity.
    """
    v = _norm(value)
    if not v or not lov:
        return None, "none", 0.0

    # 1. Exact match on the destination code itself
    for e in lov:
        if _norm(e.get("code")) == v:
            return str(e.get("code")), "exact_code", 1.0
    # 2. Exact match on the meaning / display text
    for e in lov:
        if e.get("meaning") and _norm(e["meaning"]) == v:
            return str(e.get("code")), "exact_meaning", 0.97
    # 3. Synonym concept bridge (retire → inactive → Disabled)
    src_concept = _concept(value)
    if src_concept:
        for e in lov:
            if _concept(e.get("meaning")) == src_concept or _concept(e.get("code")) == src_concept:
                return str(e.get("code")), "synonym", 0.9
    # 4. Fuzzy similarity against code and meaning
    best: tuple[float, str | None] = (0.0, None)
    for e in lov:
        for cand in (e.get("code"), e.get("meaning")):
            if not cand:
                continue
            r = difflib.SequenceMatcher(None, v, _norm(cand)).ratio()
            if r > best[0]:
                best = (r, str(e.get("code")))
    if best[0] >= 0.82:
        return best[1], "fuzzy", round(0.6 + (best[0] - 0.82) * 1.5, 2)
    return None, "none", 0.0


def lov_coverage(distinct_values: list[str], lov: list[dict]) -> tuple[float, int]:
    """Fraction (0..1) and count of source distinct values that resolve to the LOV."""
    if not distinct_values or not lov:
        return 0.0, 0
    hit = sum(1 for v in distinct_values if resolve_value(v, lov)[0] is not None)
    return hit / len(distinct_values), hit


# ── Distinct source values ────────────────────────────────────────────────────

DISTINCT_CAP = 200  # beyond this the column is identifier-like, not an LOV candidate


async def distinct_source_values(
    conversion: Conversion, source_column: str, cap: int = DISTINCT_CAP
) -> list[str]:
    """Distinct non-blank values of one source column (empty list if > cap)."""
    if not source_column:
        return []
    try:
        if conversion.dataset_id:
            dataset = await Dataset.get(conversion.dataset_id)
            df = parse_tabular(dataset.file_path, file_type=dataset.file_type)
        else:
            from app.services.mapping_service import ebs_fetch_rows
            table = getattr(conversion, "ebs_table_hint", "") or ""
            rows = await ebs_fetch_rows(table, limit=5000) if table else []
            df = pd.DataFrame(rows)
    except Exception:
        return []
    if df is None or source_column not in df.columns:
        return []
    ser = df[source_column].dropna().astype(str).str.strip()
    vals = [v for v in ser.unique().tolist() if v and v.lower() not in ("nan", "none", "null")]
    if len(vals) > cap:
        return []
    return vals


# ── Recommendation + accept ───────────────────────────────────────────────────

async def _learned_crosswalks_for(target_object: str | None, target_field: str | None) -> dict[str, str]:
    """Previously learned value pairs for this field: {norm(source): target}."""
    if not target_field:
        return {}
    q: dict[str, Any] = {"kind": "crosswalk", "target_field": target_field}
    if target_object:
        q["target_object"] = target_object
    out: dict[str, str] = {}
    for lm in await LearnedMapping.find(q).to_list():
        out[_norm(lm.original_value)] = lm.resolved_value
    return out


async def recommend_value_map(
    conversion: Conversion, mapping: MappingSuggestion
) -> dict[str, Any]:
    """Propose a source→target value map for one mapping's target field."""
    field = await FBDIField.get(mapping.target_field_id)
    if not field:
        return {"error": "Target field not found"}
    lov = field.allowed_values or []
    result: dict[str, Any] = {
        "target_field": field.field_name,
        "lov": lov,
        "default_if_blank": field.default_if_blank,
        "source_column": mapping.source_column,
        "distinct_values": [],
        "recommendations": [],
        "unmatched": [],
        "coverage": 0.0,
    }
    if not lov:
        result["error"] = (
            f"No list of values is catalogued for {field.field_name}. "
            "Add allowed values to the FBDI field first."
        )
        return result
    if not mapping.source_column:
        result["error"] = "Mapping has no source column yet."
        return result

    distinct = await distinct_source_values(conversion, mapping.source_column)
    result["distinct_values"] = distinct
    if not distinct:
        result["error"] = (
            "Source column has no usable distinct values (empty, unreadable, "
            f"or more than {DISTINCT_CAP} distinct values — identifier-like)."
        )
        return result

    # Business object name for learned lookups
    target_object = None
    tpl_id = conversion.template_id
    if tpl_id:
        from app.models.fbdi import FBDITemplate
        tpl = await FBDITemplate.get(tpl_id)
        target_object = getattr(tpl, "business_object", None) or getattr(tpl, "name", None)
    learned = await _learned_crosswalks_for(target_object, field.field_name)

    lov_codes = {str(e.get("code")) for e in lov}
    recs, unmatched = [], []
    for v in distinct:
        lv = learned.get(_norm(v))
        if lv is not None:
            recs.append({"source_value": v, "target_value": lv,
                         "method": "learned", "confidence": 1.0})
            continue
        code, method, conf = resolve_value(v, lov)
        if code is not None:
            # Identity pairs (value already a valid code) still shown so the
            # analyst sees full coverage, but flagged as already-valid.
            recs.append({"source_value": v, "target_value": code,
                         "method": method, "confidence": conf,
                         "already_valid": v in lov_codes})
        else:
            unmatched.append(v)

    result["recommendations"] = recs
    result["unmatched"] = unmatched
    result["coverage"] = round(len(recs) / len(distinct), 3) if distinct else 0.0
    return result


async def accept_value_map(
    conversion: Conversion,
    mapping: MappingSuggestion,
    pairs: list[dict[str, str]],
    default_value: str | None = None,
    user_email: str = "system",
) -> dict[str, Any]:
    """Persist accepted pairs: VALUE_MAP rule on the field + Crosswalk Library learning."""
    field = await FBDIField.get(mapping.target_field_id)
    if not field:
        return {"error": "Target field not found"}
    # Skip identity pairs (exact string match only) — norm-equal but
    # surface-different values (e.g. "Not Planned" vs "NOT_PLANNED") still
    # need mapping because FBDI expects the exact destination code.
    pairs = [p for p in pairs
             if p.get("source_value") and p.get("target_value")
             and str(p["source_value"]) != str(p["target_value"])]

    config: dict[str, Any] = {p["source_value"]: p["target_value"] for p in pairs}
    config["case_insensitive"] = True
    if default_value not in (None, ""):
        config["default"] = default_value
    elif field.default_if_blank not in (None, ""):
        config["default"] = field.default_if_blank

    # Upsert one VALUE_MAP rule per target field for this conversion
    existing = await TransformationRule.find(
        TransformationRule.conversion_id == conversion.id,
        TransformationRule.target_field_id == field.id,
        TransformationRule.rule_type == "VALUE_MAP",
    ).to_list()
    if existing:
        rule = existing[0]
        merged = dict(rule.rule_config or {})
        merged.update(config)
        await rule.set({"rule_config": merged})
    else:
        rule = TransformationRule(
            conversion_id=conversion.id,
            target_field_id=field.id,
            rule_type="VALUE_MAP",
            rule_config=config,
            description=f"Value map (crosswalk) for {field.field_name} — accepted AI recommendation",
            sequence=10,
        )
        await rule.insert()

    # Learn each pair into the Crosswalk Library
    target_object = None
    if conversion.template_id:
        from app.models.fbdi import FBDITemplate
        tpl = await FBDITemplate.get(conversion.template_id)
        target_object = getattr(tpl, "business_object", None) or getattr(tpl, "name", None)
    from app.services.learning_service import _upsert
    learned_count = 0
    for p in pairs:
        await _upsert(
            kind="crosswalk",
            category=f"Value Mapping — {field.field_name}",
            original_value=p["source_value"],
            resolved_value=p["target_value"],
            target_object=target_object,
            target_field=field.field_name,
            rule_type="VALUE_MAP",
            rule_config=None,
            project_id=getattr(conversion, "project_id", None),
            captured_from=f"value-map recommendation on {field.field_name}",
            captured_by=user_email,
        )
        learned_count += 1

    return {
        "rule_id": str(rule.id),
        "pairs_applied": len(pairs),
        "learned": learned_count,
        "default": config.get("default"),
    }
