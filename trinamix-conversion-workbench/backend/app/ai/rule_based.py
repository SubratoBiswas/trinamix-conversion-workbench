"""Deterministic rule-based column → field mapper.

Combines:
  * Column-name token similarity (Jaccard + token overlap)
  * Semantic keyword dictionary (sku/item, cust/customer, uom/unit, ...)
  * Description token overlap
  * Sample-value pattern affinity (date, code, description-like, numeric)
  * Required-field priority
  * Type compatibility

Produces a MappingSuggestion per target field with a reason string and an optional
`suggested_transformation` (e.g. uppercase code, date format conversion).
"""
from __future__ import annotations

import re
from typing import Iterable

from app.ai.base import MappingProvider, MappingSuggestion, SourceColumn, TargetField


# Semantic synonym dictionary: target keyword -> tuple of source-side aliases.
# Tuned from real Oracle FBDI vocabularies.
SEMANTIC_DICT: dict[str, tuple[str, ...]] = {
    "item": ("item", "sku", "part", "product", "material", "catalog"),
    "number": ("num", "number", "no", "id", "code", "key"),
    "name": ("name", "title", "label"),
    "description": ("desc", "description", "details", "remark", "note"),
    "organization": ("org", "organization", "plant", "facility", "site"),
    "uom": ("uom", "unit", "measure"),
    "status": ("status", "active", "state", "flag"),
    "customer": ("cust", "customer", "client", "buyer", "party"),
    "supplier": ("supp", "supplier", "vendor"),
    "date": ("date", "dt", "effective", "start", "end", "expiration", "expiry", "created"),
    "amount": ("amount", "amt", "value", "price", "cost", "total"),
    "quantity": ("qty", "quantity", "count", "qnty"),
    "currency": ("curr", "currency", "ccy"),
    "address": ("addr", "address", "street", "city", "state", "zip", "postal", "country"),
    "email": ("email", "mail"),
    "phone": ("phone", "tel", "mobile", "cell"),
    "category": ("cat", "category", "class", "group", "type"),
    "lifecycle": ("lifecycle", "phase", "stage"),
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    # split snake_case, camelCase, spaces, hyphens
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    s = s.replace("_", " ").replace("-", " ").replace("/", " ")
    return [t.lower() for t in _TOKEN_RE.findall(s) if t]


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _semantic_score(source_tokens: list[str], target_tokens: list[str]) -> float:
    """Score based on semantic synonym hits."""
    score = 0.0
    matched = 0
    total = 0
    for t in target_tokens:
        aliases = SEMANTIC_DICT.get(t.lower())
        total += 1
        if not aliases:
            continue
        if any(a in source_tokens for a in aliases):
            matched += 1
            score += 1.0
    return (matched / total) if total else 0.0


_DATE_VAL_RE = re.compile(r"^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}$")
_NUM_VAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_CODE_VAL_RE = re.compile(r"^[A-Z0-9_\-]{1,12}$")


def _value_affinity(source: SourceColumn, target: TargetField) -> tuple[float, str | None]:
    """Score how well source sample values fit the target field's expected shape.

    Returns (score 0..1, optional rationale snippet).
    """
    samples = [s for s in (source.sample_values or [])[:8] if isinstance(s, str)]
    if not samples:
        return 0.0, None

    target_name = (target.field_name or "").lower()
    target_type = (target.data_type or "").lower()
    target_len = target.max_length or 0

    # Date target
    if "date" in target_type or "date" in target_name or "effective" in target_name:
        if all(_DATE_VAL_RE.match(s) for s in samples):
            return 0.6, "samples look like dates"
    # Numeric target
    if "number" in target_type or "decimal" in target_type:
        if all(_NUM_VAL_RE.match(s) for s in samples):
            return 0.5, "samples are numeric"
    # Short code (UOM, currency, country)
    if any(k in target_name for k in ("uom", "code", "currency")) and target_len and target_len <= 10:
        if all(_CODE_VAL_RE.match(s) for s in samples):
            return 0.55, "samples are short uppercase codes"
    # Description / long text target
    if "description" in target_name and any(len(s) > 25 for s in samples):
        return 0.45, "samples look like long descriptive text"
    # Identifier-like target (item number, customer number, *name* used as primary key in Oracle FBDI)
    is_identifier_target = any(k in target_name for k in ("number", "id", "code", "name"))
    if is_identifier_target:
        # Identifiers are typically high-cardinality. Penalise low-cardinality
        # category-style columns (e.g. item_class with 7 distinct values).
        if source.distinct_count and len(samples) >= 3:
            cardinality_ratio = source.distinct_count / max(source.distinct_count + 1, 1)
            if all(re.match(r"^[A-Za-z0-9_\-/]{1,50}$", s) for s in samples):
                if cardinality_ratio > 0.8:
                    return 0.55, "high-cardinality identifier-like values"
                elif cardinality_ratio < 0.4:
                    return -0.2, "low-cardinality categorical (poor fit for identifier)"
                return 0.4, "samples look like identifiers"
    return 0.0, None


def _type_compatibility(source: SourceColumn, target: TargetField) -> float:
    src = (source.inferred_type or "").lower()
    tgt = (target.data_type or "").lower()
    if not tgt:
        return 0.0
    if src in ("integer", "float") and ("number" in tgt or "decimal" in tgt):
        return 1.0
    if src == "date" and "date" in tgt:
        return 1.0
    if src == "boolean" and ("character" in tgt or "string" in tgt):
        return 0.5
    if src == "string" and ("character" in tgt or "string" in tgt):
        return 0.6
    return 0.0


def _suggest_transformation(
    source: SourceColumn, target: TargetField
) -> dict | None:
    target_name = (target.field_name or "").lower()
    samples = [s for s in (source.sample_values or [])[:8] if isinstance(s, str)]

    # UOM / short code → uppercase
    if any(k in target_name for k in ("uom", "currency", "code")):
        if any(any(ch.islower() for ch in s) for s in samples):
            return {"rule_type": "UPPERCASE", "config": {}, "description": "Force uppercase for code field"}

    # Item number / id with dashes → strip hyphens (matches FBDI convention)
    if any(k in target_name for k in ("item", "number", "id")) and any("-" in s for s in samples):
        return {"rule_type": "REMOVE_HYPHEN", "config": {}, "description": "Strip hyphens from identifier"}

    # Date format conversion to FBDI standard YYYY/MM/DD
    if ("date" in target_name or (target.data_type or "").lower() == "date") and samples:
        if all(re.match(r"^\d{2}/\d{2}/\d{4}$", s) for s in samples):
            return {
                "rule_type": "DATE_FORMAT",
                "config": {"input_format": "%m/%d/%Y", "output_format": "%Y/%m/%d"},
                "description": "Convert MM/DD/YYYY → YYYY/MM/DD",
            }
        if all(re.match(r"^\d{4}-\d{2}-\d{2}$", s) for s in samples):
            return {
                "rule_type": "DATE_FORMAT",
                "config": {"input_format": "%Y-%m-%d", "output_format": "%Y/%m/%d"},
                "description": "Convert YYYY-MM-DD → YYYY/MM/DD",
            }

    # Status: A/I → Active/Inactive
    if "status" in target_name and samples and set(s.upper() for s in samples) <= {"A", "I", "Y", "N"}:
        return {
            "rule_type": "VALUE_MAP",
            "config": {"A": "Active", "I": "Inactive", "Y": "Active", "N": "Inactive"},
            "description": "Map A/I (or Y/N) status codes",
        }

    # Always-safe: trim leading/trailing spaces if seen in samples
    if any(s != s.strip() for s in samples):
        return {"rule_type": "TRIM", "config": {}, "description": "Trim leading/trailing whitespace"}

    return None


class RuleBasedMapper:
    name = "rule-based"

    def suggest_mappings(
        self,
        source_columns: list[SourceColumn],
        target_fields: list[TargetField],
    ) -> list[MappingSuggestion]:
        suggestions: list[MappingSuggestion] = []
        used_sources: set[str] = set()

        # Score every (target, source) pair, pick the best per target greedily —
        # required fields first so they get first dibs on good candidates.
        sorted_targets = sorted(target_fields, key=lambda t: (not t.required, t.field_name))

        for tgt in sorted_targets:
            tgt_tokens = _tokenize(tgt.field_name)
            tgt_desc_tokens = _tokenize(tgt.description or "")
            best: tuple[float, SourceColumn | None, list[str]] = (0.0, None, [])

            for src in source_columns:
                if src.name in used_sources:
                    continue
                src_tokens = _tokenize(src.name)
                # 1. column name similarity
                name_score = _jaccard(src_tokens, tgt_tokens)
                # 2. semantic synonym hits
                sem_score = _semantic_score(src_tokens, tgt_tokens)
                # 3. description tokens
                desc_score = _jaccard(src_tokens, tgt_desc_tokens) if tgt_desc_tokens else 0.0
                # 4. type compatibility
                type_score = _type_compatibility(src, tgt)
                # 5. value affinity
                val_score, val_reason = _value_affinity(src, tgt)
                # 6. required priority bonus when type/name overlap exists
                bonus = 0.05 if (tgt.required and (name_score or sem_score)) else 0.0

                # weighted combination capped at 1.0
                composite = min(
                    1.0,
                    name_score * 0.40
                    + sem_score * 0.30
                    + desc_score * 0.10
                    + type_score * 0.10
                    + val_score * 0.10
                    + bonus,
                )

                reasons: list[str] = []
                if name_score >= 0.5:
                    reasons.append(f"column name overlap ({int(name_score * 100)}%)")
                if sem_score >= 0.5:
                    reasons.append("semantic keyword match")
                if type_score >= 0.5:
                    reasons.append(f"type compatible ({src.inferred_type} → {tgt.data_type})")
                if val_reason:
                    reasons.append(val_reason)

                if composite > best[0]:
                    best = (composite, src, reasons)

            confidence, src_pick, reasons = best
            if src_pick and confidence >= 0.20:
                used_sources.add(src_pick.name)
                transformation = _suggest_transformation(src_pick, tgt)
                review_required = confidence < 0.65
                reason = (
                    "; ".join(reasons) if reasons else f"best available match for {tgt.field_name}"
                )
                suggestions.append(
                    MappingSuggestion(
                        target_field_id=tgt.id,
                        target_field_name=tgt.field_name,
                        source_column=src_pick.name,
                        confidence=round(confidence, 3),
                        reason=reason,
                        suggested_transformation=transformation,
                        review_required=review_required,
                    )
                )
            else:
                # No good source — leave unmapped but record the target so the UI
                # can show "needs source / default value"
                suggestions.append(
                    MappingSuggestion(
                        target_field_id=tgt.id,
                        target_field_name=tgt.field_name,
                        source_column=None,
                        confidence=0.0,
                        reason=(
                            "No matching source column; required field — needs default value or override"
                            if tgt.required
                            else "No confident match found"
                        ),
                        suggested_transformation=None,
                        review_required=True,
                    )
                )
        return suggestions
