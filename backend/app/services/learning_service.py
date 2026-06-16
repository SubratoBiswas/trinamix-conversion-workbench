"""Capture and re-apply human-approved mapping decisions (async/Beanie)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from beanie import PydanticObjectId

from app.models.conversion import Conversion
from app.models.dataset import DatasetColumnProfile
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion
from app.models.transformation import TransformationRule

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

REFERENCE_KEY_FIELDS: dict[str, list[str]] = {
    "Item":     ["InventoryItemNumber", "Inventory Item Name", "Item Number", "ItemNumber"],
    "Customer": ["CustomerNumber", "Customer Number"],
    "Supplier": ["SupplierNumber", "Supplier Number"],
    "UOM":      ["UnitOfMeasureCode", "Unit of Measure Code"],
}


def _normalize(name: str | None) -> str:
    if not name:
        return ""
    return _NORMALIZE_RE.sub("", name.lower())


def _is_master_key_field(target_object: str | None, target_field: str | None) -> bool:
    if not target_object or not target_field:
        return False
    return target_field in REFERENCE_KEY_FIELDS.get(target_object, [])


async def _business_object_for(conversion: Conversion) -> str | None:
    if conversion.template_id:
        tpl = await FBDITemplate.get(conversion.template_id)
        if tpl and tpl.business_object:
            return tpl.business_object
    return conversion.target_object


def _category_for(rule_type: str | None) -> str:
    if not rule_type:
        return "Column Mapping Alias"
    rt = rule_type.upper()
    if rt == "DATE_FORMAT":
        return "Date Format Rule"
    if rt in ("VALUE_MAP", "CROSSWALK_LOOKUP", "CASE_WHEN", "CONDITIONAL"):
        return "Status Value Mapping"
    if rt in ("CONSTANT", "DEFAULT_VALUE", "COMPUTED", "COALESCE"):
        return "Default & Computed Value"
    if rt in ("ARITHMETIC", "NUMBER_FORMAT"):
        return "Numeric Rule"
    return "Column Mapping Alias"


async def _upsert(*, kind, category, original_value, resolved_value,
                  target_object=None, target_field=None, rule_type=None,
                  rule_config=None, project_id=None, captured_from, captured_by) -> LearnedMapping:
    query = {
        "kind": kind,
        "target_object": target_object,
        "target_field": target_field,
        "rule_type": rule_type,
    }
    norm_orig = _normalize(original_value)
    existing = await LearnedMapping.find(query).to_list()
    for lm in existing:
        if _normalize(lm.original_value) == norm_orig:
            await lm.set({
                "resolved_value": resolved_value, "rule_config": rule_config or {},
                "captured_from": captured_from, "captured_by": captured_by,
                "captured_at": datetime.utcnow(),
                "project_id": project_id,
            })
            return lm
    lm = LearnedMapping(
        kind=kind, category=category, original_value=original_value,
        resolved_value=resolved_value, target_object=target_object,
        target_field=target_field, rule_type=rule_type, rule_config=rule_config or {},
        project_id=project_id, captured_from=captured_from, captured_by=captured_by,
    )
    await lm.insert()
    return lm


async def record_learning_from_mapping(
    mapping: MappingSuggestion, conversion: Conversion, captured_by: str | None
) -> LearnedMapping | None:
    if not mapping.source_column:
        return None
    business_object = await _business_object_for(conversion)
    if not business_object:
        return None
    tpl = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    target_field = None
    if tpl:
        fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list()
        for f in fields:
            if f.id == mapping.target_field_id:
                target_field = f.field_name
                break
    if not target_field:
        return None
    rule_type = None
    rule_config: dict = {}
    if mapping.suggested_transformation and isinstance(mapping.suggested_transformation, dict):
        rule_type = mapping.suggested_transformation.get("rule_type")
        rule_config = mapping.suggested_transformation.get("config", {})
    captured_from = f"{conversion.name} — {target_field}"
    lm = await _upsert(
        kind="column_mapping", category="Column Mapping Alias",
        original_value=mapping.source_column, resolved_value=target_field,
        target_object=business_object, target_field=target_field,
        rule_type=rule_type, rule_config=rule_config,
        project_id=conversion.project_id,
        captured_from=captured_from, captured_by=captured_by,
    )
    if rule_type and _is_master_key_field(business_object, target_field):
        await _upsert(
            kind="reference_standard", category="Reference Key Standard",
            original_value=target_field, resolved_value=target_field,
            target_object=business_object, target_field=target_field,
            rule_type=rule_type, rule_config=rule_config,
            project_id=conversion.project_id,
            captured_from=captured_from, captured_by=captured_by,
        )
    return lm


async def record_learning_from_rule(
    rule: TransformationRule, conversion: Conversion, captured_by: str | None
) -> LearnedMapping | None:
    business_object = await _business_object_for(conversion)
    if not business_object:
        return None
    target_field = None
    if rule.target_field_id:
        f = await FBDIField.get(rule.target_field_id)
        if f:
            target_field = f.field_name
    if not target_field:
        return None
    captured_from = f"{conversion.name} — {target_field} (manual)"
    lm = await _upsert(
        kind="rule", category=_category_for(rule.rule_type),
        original_value=rule.source_column or "", resolved_value=target_field,
        target_object=business_object, target_field=target_field,
        rule_type=rule.rule_type, rule_config=rule.rule_config or {},
        project_id=conversion.project_id,
        captured_from=captured_from, captured_by=captured_by,
    )
    if _is_master_key_field(business_object, target_field):
        await _upsert(
            kind="reference_standard", category="Reference Key Standard",
            original_value=target_field, resolved_value=target_field,
            target_object=business_object, target_field=target_field,
            rule_type=rule.rule_type, rule_config=rule.rule_config or {},
            project_id=conversion.project_id,
            captured_from=captured_from, captured_by=captured_by,
        )
    return lm


async def apply_learned_to_conversion(
    conversion: Conversion, mappings: Iterable[MappingSuggestion]
) -> int:
    business_object = await _business_object_for(conversion)
    if not business_object:
        return 0
    learned = await LearnedMapping.find({
        "kind": "column_mapping", "target_object": business_object
    }).to_list()
    if not learned:
        return 0
    by_target: dict[str, list[LearnedMapping]] = {}
    for lm in learned:
        if lm.target_field:
            by_target.setdefault(lm.target_field, []).append(lm)
    src_index: dict[str, str] = {}
    if conversion.dataset_id:
        cols = await DatasetColumnProfile.find(
            DatasetColumnProfile.dataset_id == conversion.dataset_id
        ).to_list()
        for c in cols:
            src_index[_normalize(c.column_name)] = c.column_name
    fields_map: dict = {}
    if conversion.template_id:
        fields = await FBDIField.find(FBDIField.template_id == conversion.template_id).to_list()
        fields_map = {f.id: f.field_name for f in fields}
    auto_count = 0
    now = datetime.utcnow()
    for m in mappings:
        if m.status != "suggested":
            continue
        tgt_name = fields_map.get(m.target_field_id)
        if not tgt_name:
            continue
        candidates = by_target.get(tgt_name)
        if not candidates:
            continue
        for lm in candidates:
            actual_src = src_index.get(_normalize(lm.original_value))
            if not actual_src:
                continue
            update = {
                "source_column": actual_src, "confidence": 1.0,
                "review_required": 0, "status": "approved",
                "approved_by": "learning-engine", "approved_at": now,
                "reason": f'Auto-applied from learning library (captured from "{lm.captured_from}")',
            }
            if lm.rule_type:
                update["suggested_transformation"] = {
                    "rule_type": lm.rule_type, "config": lm.rule_config or {},
                    "description": "Re-applied from learned rule",
                }
            await m.set(update)
            await lm.set({"records_auto_fixed": (lm.records_auto_fixed or 0) + 1})
            auto_count += 1
            break
    return auto_count


async def propagate_rules_to_downstream(
    source_conversion: Conversion, approved_mapping: MappingSuggestion
) -> list[dict]:
    rule = approved_mapping.suggested_transformation
    if not rule or not isinstance(rule, dict):
        return []
    rule_type = rule.get("rule_type")
    rule_config = rule.get("config", {})
    if not rule_type:
        return []
    tpl = await FBDITemplate.get(source_conversion.template_id) if source_conversion.template_id else None
    if not tpl:
        return []
    master_obj = tpl.business_object or source_conversion.target_object
    if not master_obj:
        return []
    key_names = REFERENCE_KEY_FIELDS.get(master_obj)
    if not key_names:
        return []
    src_fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list()
    source_field_name = next((f.field_name for f in src_fields if f.id == approved_mapping.target_field_id), None)
    if source_field_name not in key_names:
        return []
    siblings = await Conversion.find(
        Conversion.project_id == source_conversion.project_id,
        Conversion.id != source_conversion.id,
    ).to_list()
    propagated: list[dict] = []
    now = datetime.utcnow()
    for conv in siblings:
        if not conv.template_id:
            continue
        sib_fields = await FBDIField.find(FBDIField.template_id == conv.template_id).to_list()
        for f in sib_fields:
            if f.field_name not in key_names:
                continue
            existing = await TransformationRule.find_one({
                "conversion_id": conv.id, "target_field_id": f.id, "rule_type": rule_type
            })
            if existing:
                await existing.set({"rule_config": rule_config})
            else:
                await TransformationRule(
                    conversion_id=conv.id, target_field_id=f.id,
                    source_column=approved_mapping.source_column,
                    rule_type=rule_type, rule_config=rule_config,
                    description=f"Auto-propagated from {master_obj} master ({source_conversion.name})",
                    sequence=1,
                ).insert()
            propagated.append({
                "conversion_id": str(conv.id), "conversion_name": conv.name,
                "target_field": f.field_name, "rule_type": rule_t