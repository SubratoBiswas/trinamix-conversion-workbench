"""Data-quality rule store: load, extract-from-template, upload, apply.

Rules are scoped by (target_object, client) like learnings — a client's own rows
plus any global row. Three creation sources: EXTRACTED (derived from an FBDI
template's field metadata), UPLOADED (rules workbook/JSON), MANUAL. They feed the
Generate-time DQ step (generate_dq) and are also runnable on demand.
"""
from __future__ import annotations

import logging
from typing import Optional

from beanie import PydanticObjectId

from app.models.dq_rule import DataQualityRule
from app.models.fbdi import FBDIField, FBDITemplate

logger = logging.getLogger(__name__)


def _rule_to_dict(r: DataQualityRule) -> dict:
    return {"id": str(r.id), "field": r.field, "rule_type": r.rule_type,
            "params": r.params or {}, "severity": r.severity, "kind": r.kind,
            "source": r.source, "active": r.active, "description": r.description}


async def load_rules(target_object: str, client_id: Optional[PydanticObjectId], kind: str,
                     include_extracted: bool = False) -> list[dict]:
    """Active rules for (object, client) of a given kind. For validation, extracted
    rules are excluded by default (they mirror the built-in FBDI checks that already
    run) so they aren't double-counted; uploaded/manual rules always apply. Cleansing
    rules always apply regardless of source."""
    from app.services.client_service import scope_query
    scope = await scope_query(client_id)
    q = {"kind": kind, "target_object": target_object, "active": True, **scope}
    rows = await DataQualityRule.find(q).to_list()
    out = []
    for r in rows:
        if kind == "validation" and r.source == "extracted" and not include_extracted:
            continue
        out.append(_rule_to_dict(r))
    return out


async def extract_rules_from_template(target_object: str, template_id, client_id: Optional[PydanticObjectId],
                                      created_by: str = "system") -> dict:
    """Derive validation rules from an FBDI template's field metadata and persist
    them as source='extracted' (editable). Idempotent on
    (kind, target_object, field, rule_type, client_id)."""
    tpl = await FBDITemplate.get(PydanticObjectId(str(template_id)))
    if not tpl:
        return {"error": "Template not found", "created": 0}
    fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list()
    created = skipped = 0
    for f in fields:
        specs: list[tuple[str, dict, str, str]] = []  # (rule_type, params, severity, desc)
        if f.required:
            specs.append(("REQUIRED", {}, "error", f"'{f.field_name}' is required."))
        if f.max_length:
            specs.append(("MAX_LENGTH", {"max_length": int(f.max_length)}, "error",
                          f"'{f.field_name}' max length {f.max_length}."))
        vals = [str(v.get("code") or v.get("value") or "").strip()
                for v in (f.allowed_values or []) if (v.get("code") or v.get("value"))]
        vals = [v for v in vals if v]
        if vals:
            specs.append(("VALUE_IN_SET", {"values": vals}, "error",
                          f"'{f.field_name}' must be one of {len(vals)} allowed codes."))
        dt = (f.data_type or "").lower()
        if dt in ("number", "numeric", "integer", "decimal"):
            specs.append(("NUMERIC", {}, "warning", f"'{f.field_name}' must be numeric."))
        for rule_type, params, severity, desc in specs:
            existing = await DataQualityRule.find(
                DataQualityRule.kind == "validation",
                DataQualityRule.target_object == target_object,
                DataQualityRule.field == f.field_name,
                DataQualityRule.rule_type == rule_type,
                DataQualityRule.client_id == client_id,
            ).first_or_none()
            if existing:
                skipped += 1
                continue
            await DataQualityRule(
                kind="validation", target_object=target_object, field=f.field_name,
                rule_type=rule_type, params=params, severity=severity, description=desc,
                source="extracted", client_id=client_id, created_by=created_by,
            ).insert()
            created += 1
    return {"target_object": target_object, "created": created, "skipped": skipped}
