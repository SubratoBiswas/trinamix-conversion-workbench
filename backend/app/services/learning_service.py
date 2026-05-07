"""Capture and re-apply human-approved mapping decisions across runs.

Two halves of the same loop:

* ``record_learning_from_mapping`` — called whenever a MappingSuggestion lands
  in 'approved' or 'overridden' status. Persists (or refreshes) a
  LearnedMapping row keyed by the business object + target field +
  normalized source column.

* ``apply_learned_to_conversion`` — called after the AI provider returns fresh
  suggestions. For each suggestion still in 'suggested' status, if the
  learning library has a pattern that matches the current dataset's columns,
  the suggestion is mutated to source from the matched column at confidence
  1.0 and auto-approved with ``approved_by='learning-engine'``.

Match scope is ``FBDITemplate.business_object`` — so a learned alias on one
Oracle Item template re-fires on a newer version of the same template.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.conversion import Conversion
from app.models.dataset import DatasetColumnProfile
from app.models.fbdi import FBDIField
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion
from app.models.transformation import TransformationRule


_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


# A "Reference Standard" is a transformation rule taught on a master entity's
# key column that auto-applies to every downstream conversion's FK column
# referencing the same entity. The mapping below identifies (a) which target
# field on a master conversion counts as the canonical key, and (b) which
# field name on a downstream conversion is the inheriting FK. By FBDI
# convention these have the same field name, so one list serves both roles.
# NOTE: this list spans both the *master* key field names (Oracle's "Inventory
# Item Name" on the Item Master FBDI template) and the *downstream FK* names
# (the SO/PO/BOM "InventoryItemNumber" convention). They differ because Oracle
# FBDI uses different field-name conventions across modules. At apply time we
# match by ``business_object`` alone — any field on a downstream conversion
# whose name is in this list inherits any active standard for that object.
REFERENCE_KEY_FIELDS: dict[str, list[str]] = {
    "Item":     ["InventoryItemNumber", "Inventory Item Name", "Item Number", "ItemNumber"],
    "Customer": ["CustomerNumber", "Customer Number"],
    "Supplier": ["SupplierNumber", "Supplier Number"],
    "UOM":      ["UnitOfMeasureCode", "Unit of Measure Code"],
}


def _is_master_key_field(
    target_object: str | None, target_field: str | None
) -> bool:
    if not target_object or not target_field:
        return False
    return target_field in REFERENCE_KEY_FIELDS.get(target_object, [])


def _normalize(name: str | None) -> str:
    """Loose key for column-name comparison: ignores case, spaces, and
    punctuation so 'Item_No', 'ITEM NO', and 'item-no' collapse to the same
    key. Required because legacy extracts rarely keep the same header style.
    """
    if not name:
        return ""
    return _NORMALIZE_RE.sub("", name.lower())


def _business_object_for(conversion: Conversion) -> str | None:
    tpl = conversion.template
    if tpl and tpl.business_object:
        return tpl.business_object
    if conversion.target_object:
        return conversion.target_object
    return tpl.name if tpl else None


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
    if rt in (
        "UPPERCASE", "LOWERCASE", "TITLE_CASE", "REMOVE_HYPHEN",
        "REMOVE_SPECIAL_CHARS", "TRIM", "PAD", "SUBSTRING",
        "REPLACE", "REGEX_REPLACE", "REGEX_EXTRACT", "CONCAT", "SPLIT",
    ):
        return "Text Format Rule"
    return "Column Mapping Alias"


def _upsert(
    db: Session,
    *,
    kind: str,
    category: str,
    original_value: str,
    resolved_value: str,
    target_object: str,
    target_field: str,
    rule_type: str | None,
    rule_config: dict | None,
    project_id: int | None,
    captured_from: str,
    captured_by: str | None,
) -> LearnedMapping:
    """Idempotent upsert keyed by (kind, target_object, target_field,
    normalized original_value, rule_type) — re-approving the same column or
    re-confirming the same rule refreshes the row instead of duplicating it.
    """
    src_norm = _normalize(original_value)
    candidates = (
        db.query(LearnedMapping)
        .filter(
            LearnedMapping.kind == kind,
            LearnedMapping.target_object == target_object,
            LearnedMapping.target_field == target_field,
            LearnedMapping.rule_type == rule_type,
        )
        .all()
    )
    matched = next(
        (lm for lm in candidates if _normalize(lm.original_value) == src_norm),
        None,
    )
    if matched:
        matched.resolved_value = resolved_value
        matched.rule_config = rule_config
        matched.category = category
        matched.captured_by = captured_by or matched.captured_by
        matched.captured_from = captured_from
        matched.captured_at = datetime.utcnow()
        db.commit()
        return matched

    item = LearnedMapping(
        kind=kind,
        category=category,
        original_value=original_value,
        resolved_value=resolved_value,
        target_object=target_object,
        target_field=target_field,
        rule_type=rule_type,
        rule_config=rule_config,
        project_id=project_id,
        captured_from=captured_from,
        captured_by=captured_by,
        confidence_boost=0.26,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def record_learning_from_mapping(
    db: Session,
    mapping: MappingSuggestion,
    conversion: Conversion,
    captured_by: str | None,
) -> LearnedMapping | None:
    """An approval teaches two things at once:

    * the **alias** — legacy column → FBDI field — lands as ``kind=column_mapping``
      and powers auto-replay on future files (Learning Center).
    * the **rule** — any transformation attached to that mapping (REMOVE_HYPHEN,
      DATE_FORMAT, VALUE_MAP, …) — also lands as ``kind=rule`` so it surfaces
      in the Rule Library as a reusable transformation.

    Returns the alias row (the rule row, when written, is independent).
    """
    if not mapping.source_column:
        return None
    business_object = _business_object_for(conversion)
    if not business_object:
        return None

    target_field = None
    if conversion.template:
        for f in conversion.template.fields:
            if f.id == mapping.target_field_id:
                target_field = f.field_name
                break
    if not target_field:
        return None

    rule = mapping.suggested_transformation or {}
    rule_type = rule.get("rule_type") if isinstance(rule, dict) else None
    rule_config = rule.get("config") if isinstance(rule, dict) else None

    project_label = conversion.name or f"Conversion #{conversion.id}"
    captured_from = f"{project_label} — {target_field}"

    alias = _upsert(
        db,
        kind="column_mapping",
        category="Column Mapping Alias",
        original_value=mapping.source_column,
        resolved_value=target_field,
        target_object=business_object,
        target_field=target_field,
        rule_type=rule_type,
        rule_config=rule_config,
        project_id=conversion.project_id,
        captured_from=captured_from,
        captured_by=captured_by,
    )

    if rule_type:
        _upsert(
            db,
            kind="rule",
            category=_category_for(rule_type),
            original_value=mapping.source_column,
            resolved_value=target_field,
            target_object=business_object,
            target_field=target_field,
            rule_type=rule_type,
            rule_config=rule_config,
            project_id=conversion.project_id,
            captured_from=captured_from,
            captured_by=captured_by,
        )

    # If the rule lands on a master entity's key column (e.g. Item's
    # InventoryItemNumber), also auto-promote it to a Reference Standard so
    # every downstream conversion that references this master inherits the
    # same transformation at output time.
    if rule_type and _is_master_key_field(business_object, target_field):
        record_reference_standard(
            db,
            target_object=business_object,
            target_field=target_field,
            rule_type=rule_type,
            rule_config=rule_config,
            project_id=conversion.project_id,
            captured_from=captured_from,
            captured_by=captured_by,
        )

    return alias


def record_reference_standard(
    db: Session,
    *,
    target_object: str,
    target_field: str,
    rule_type: str,
    rule_config: dict | None,
    project_id: int | None,
    captured_from: str,
    captured_by: str | None,
) -> LearnedMapping:
    """Persist (or refresh) a reference standard — a transformation rule that
    will auto-prepend on every downstream conversion's FK column referencing
    this master entity."""
    return _upsert(
        db,
        kind="reference_standard",
        category="Reference Key Standard",
        original_value=target_field,
        resolved_value=target_field,
        target_object=target_object,
        target_field=target_field,
        rule_type=rule_type,
        rule_config=rule_config,
        project_id=project_id,
        captured_from=captured_from,
        captured_by=captured_by,
    )


def list_reference_standards_for_object(
    db: Session, target_object: str
) -> list[LearnedMapping]:
    """All active reference standards for a master entity, regardless of
    which exact field name on the master was used to teach it. Apply path
    matches by business object so naming-convention differences between the
    master's key field and downstream FK names don't break inheritance."""
    return (
        db.query(LearnedMapping)
        .filter(
            LearnedMapping.kind == "reference_standard",
            LearnedMapping.target_object == target_object,
        )
        .order_by(LearnedMapping.captured_at)
        .all()
    )


def record_learning_from_rule(
    db: Session,
    rule: TransformationRule,
    conversion: Conversion,
    captured_by: str | None,
) -> LearnedMapping | None:
    """Surface a manually-authored transformation rule in the Rule Library.

    A user-written rule is treated as authoritative — it lands as ``kind=rule``
    keyed by the same (business_object, target_field, normalized source
    column, rule_type) as approved-mapping rules, so they unify in one place.
    """
    business_object = _business_object_for(conversion)
    if not business_object:
        return None

    target_field = None
    if conversion.template and rule.target_field_id:
        for f in conversion.template.fields:
            if f.id == rule.target_field_id:
                target_field = f.field_name
                break
    if not target_field:
        # rule has no target field — Rule Library entries need one to be useful
        return None

    src = rule.source_column or ""
    project_label = conversion.name or f"Conversion #{conversion.id}"
    captured_from = f"{project_label} — {target_field} (manual)"

    learned = _upsert(
        db,
        kind="rule",
        category=_category_for(rule.rule_type),
        original_value=src,
        resolved_value=target_field,
        target_object=business_object,
        target_field=target_field,
        rule_type=rule.rule_type,
        rule_config=rule.rule_config or {},
        project_id=conversion.project_id,
        captured_from=captured_from,
        captured_by=captured_by,
    )

    # Manually-authored rules on the master's key column also become
    # Reference Standards. Same auto-prepend mechanic as approved mappings.
    if _is_master_key_field(business_object, target_field):
        record_reference_standard(
            db,
            target_object=business_object,
            target_field=target_field,
            rule_type=rule.rule_type,
            rule_config=rule.rule_config or {},
            project_id=conversion.project_id,
            captured_from=captured_from,
            captured_by=captured_by,
        )

    return learned


def apply_learned_to_conversion(
    db: Session,
    conversion: Conversion,
    mappings: Iterable[MappingSuggestion],
) -> int:
    business_object = _business_object_for(conversion)
    if not business_object:
        return 0
    learned = (
        db.query(LearnedMapping)
        .filter(
            LearnedMapping.kind == "column_mapping",
            LearnedMapping.target_object == business_object,
        )
        .all()
    )
    if not learned:
        return 0

    by_target: dict[str, list[LearnedMapping]] = {}
    for lm in learned:
        if not lm.target_field:
            continue
        by_target.setdefault(lm.target_field, []).append(lm)

    src_index: dict[str, str] = {}
    if conversion.dataset_id:
        cols = (
            db.query(DatasetColumnProfile)
            .filter(DatasetColumnProfile.dataset_id == conversion.dataset_id)
            .all()
        )
        for c in cols:
            src_index[_normalize(c.column_name)] = c.column_name

    fields = {
        f.id: f.field_name
        for f in db.query(FBDIField)
        .filter(FBDIField.template_id == conversion.template_id)
        .all()
    }

    auto_count = 0
    now = datetime.utcnow()
    for m in mappings:
        if m.status != "suggested":
            continue
        tgt_name = fields.get(m.target_field_id)
        if not tgt_name:
            continue
        candidates = by_target.get(tgt_name)
        if not candidates:
            continue
        for lm in candidates:
            actual_src = src_index.get(_normalize(lm.original_value))
            if not actual_src:
                continue
            m.source_column = actual_src
            m.confidence = 1.0
            m.review_required = 0
            m.reason = (
                f"Auto-applied from learning library "
                f"(captured from “{lm.captured_from}”)"
            )
            if lm.rule_type:
                m.suggested_transformation = {
                    "rule_type": lm.rule_type,
                    "config": lm.rule_config or {},
                    "description": "Re-applied from learned rule",
                }
            m.status = "approved"
            m.approved_by = "learning-engine"
            m.approved_at = now
            auto_count += 1
            lm.records_auto_fixed = (lm.records_auto_fixed or 0) + 1
            break
    if auto_count:
        db.commit()
    return auto_count
