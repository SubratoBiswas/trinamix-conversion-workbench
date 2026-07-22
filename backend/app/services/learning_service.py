"""Capture and re-apply human-approved mapping decisions (async/Beanie)."""
from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

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


async def _upsert_learned(kind, business_object, field_name, *, original, resolved,
                          rule_type=None, rule_config=None, captured_from="auto-capture",
                          client_id=None):
    """Upsert one reusable object-level learned rule. Never downgrades a rule
    captured from a gold example / prompt / accepted crosswalk with an
    auto-captured one (human/gold signals outrank auto-capture).

    Captured learnings are CLIENT-SCOPED (is_global=False, client_id set) — they
    encode one client's source data, so they must not leak to other clients. The
    existing-row lookup is keyed by client too, so two clients keep independent
    rules for the same object/field."""
    if not business_object or not field_name:
        return False
    existing = await LearnedMapping.find_one(
        LearnedMapping.kind == kind,
        LearnedMapping.target_object == business_object,
        LearnedMapping.target_field == field_name,
        LearnedMapping.client_id == client_id,
    )
    if existing and existing.captured_from in ("gold example", "prompt", "value-map-accept") \
            and captured_from == "auto-capture":
        return False
    category = _category_for(rule_type) if kind == "column_mapping" else kind
    doc = {
        "kind": kind, "category": category,
        "original_value": str(original), "resolved_value": str(resolved),
        "target_object": business_object, "target_field": field_name,
        "rule_type": rule_type, "rule_config": rule_config or {},
        "client_id": client_id, "is_global": False,
        "captured_from": captured_from, "captured_at": datetime.utcnow(),
    }
    if existing:
        await existing.set(doc)
    else:
        await LearnedMapping(**doc).insert()
    return True


async def capture_learnings_from_conversion(conversion: Conversion) -> dict:
    """After a successful mapping/output, persist the conversion's effective
    mappings, constant defaults and suppressions as reusable object-level
    learnings so future conversions of the same object reuse them (and lean on
    AI less). Only trustworthy signals are captured — user-approved/overridden,
    deterministic/high-confidence (>=0.85), or suppressed — so low-confidence AI
    guesses don't get propagated."""
    business_object = await _business_object_for(conversion)
    if not business_object or not conversion.template_id:
        return {"captured": 0}
    from app.services.client_service import client_id_for_conversion
    _cid = await client_id_for_conversion(conversion)
    fields = {f.id: f.field_name for f in await FBDIField.find(
        FBDIField.template_id == conversion.template_id).to_list()}
    maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conversion.id).to_list()
    _PRIO = {"overridden": 4, "approved": 3, "not_applicable": 2, "rejected": 1, "suggested": 0}
    best: dict = {}
    for m in maps:
        c = best.get(m.target_field_id)
        if c is None or _PRIO.get(m.status or "suggested", 0) > _PRIO.get(c.status or "suggested", 0):
            best[m.target_field_id] = m
    n = 0
    for m in best.values():
        fname = fields.get(m.target_field_id)
        if not fname:
            continue
        # Capture user-confirmed mappings and reasonably confident ones
        # (rule-based confident matches sit ~0.6+). Gold/prompt rules are never
        # downgraded by _upsert_learned, so this only ADDS coverage over time.
        trustworthy = (m.status in ("approved", "overridden")) or ((m.confidence or 0) >= 0.60)
        if m.status == "not_applicable":
            if await _upsert_learned("suppress_field", business_object, fname,
                                     original="(blank)", resolved="", rule_type="suppress",
                                     client_id=_cid):
                n += 1
        elif m.source_column and trustworthy:
            st = m.suggested_transformation or {}
            if await _upsert_learned("column_mapping", business_object, fname,
                                     original=m.source_column, resolved=fname,
                                     rule_type=st.get("rule_type"), rule_config=st.get("config"),
                                     client_id=_cid):
                n += 1
        elif m.default_value and trustworthy:
            if await _upsert_learned("example_default", business_object, fname,
                                     original="(default)", resolved=m.default_value,
                                     rule_type="default", client_id=_cid):
                n += 1
    return {"captured": n, "object": business_object}


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
                  rule_config=None, project_id=None, captured_from, captured_by,
                  client_id=None) -> LearnedMapping:
    # Client-scoped (is_global=False): an interactively captured rule encodes one
    # client's source data. Dedup is keyed by client too, so clients stay separate.
    query = {
        "kind": kind,
        "target_object": target_object,
        "target_field": target_field,
        "rule_type": rule_type,
        "client_id": client_id,
    }
    norm_orig = _normalize(original_value)
    existing = await LearnedMapping.find(query).to_list()
    for lm in existing:
        if _normalize(lm.original_value) == norm_orig:
            await lm.set({
                "resolved_value": resolved_value, "rule_config": rule_config or {},
                "captured_from": captured_from, "captured_by": captured_by,
                "captured_at": datetime.utcnow(),
                "project_id": project_id, "client_id": client_id, "is_global": False,
            })
            return lm
    lm = LearnedMapping(
        kind=kind, category=category, original_value=original_value,
        resolved_value=resolved_value, target_object=target_object,
        target_field=target_field, rule_type=rule_type, rule_config=rule_config or {},
        project_id=project_id, captured_from=captured_from, captured_by=captured_by,
        client_id=client_id, is_global=False,
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
    captured_from = f"{conversion.name} -- {target_field}"
    from app.services.client_service import client_id_for_conversion
    _cid = await client_id_for_conversion(conversion)
    lm = await _upsert(
        kind="column_mapping", category="Column Mapping Alias",
        original_value=mapping.source_column, resolved_value=target_field,
        target_object=business_object, target_field=target_field,
        rule_type=rule_type, rule_config=rule_config,
        project_id=conversion.project_id, client_id=_cid,
        captured_from=captured_from, captured_by=captured_by,
    )
    if rule_type and _is_master_key_field(business_object, target_field):
        await _upsert(
            kind="reference_standard", category="Reference Key Standard",
            original_value=target_field, resolved_value=target_field,
            target_object=business_object, target_field=target_field,
            rule_type=rule_type, rule_config=rule_config,
            project_id=conversion.project_id, client_id=_cid,
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
    captured_from = f"{conversion.name} -- {target_field} (manual)"
    from app.services.client_service import client_id_for_conversion
    _cid = await client_id_for_conversion(conversion)
    lm = await _upsert(
        kind="rule", category=_category_for(rule.rule_type),
        original_value=rule.source_column or "", resolved_value=target_field,
        target_object=business_object, target_field=target_field,
        rule_type=rule.rule_type, rule_config=rule.rule_config or {},
        project_id=conversion.project_id, client_id=_cid,
        captured_from=captured_from, captured_by=captured_by,
    )
    if _is_master_key_field(business_object, target_field):
        await _upsert(
            kind="reference_standard", category="Reference Key Standard",
            original_value=target_field, resolved_value=target_field,
            target_object=business_object, target_field=target_field,
            rule_type=rule.rule_type, rule_config=rule.rule_config or {},
            project_id=conversion.project_id, client_id=_cid,
            captured_from=captured_from, captured_by=captured_by,
        )
    return lm


async def apply_learned_to_conversion(
    conversion: Conversion, mappings: Iterable[MappingSuggestion], force: bool = False,
) -> int:
    """Apply the object's stored reference standard (column mappings, constant
    defaults, suppressions) to a conversion's mappings.

    Normally only touches still-"suggested" mappings. With ``force=True`` (used
    at generate time and by the explicit "apply gold" action) the gold-derived
    rules also OVERRIDE mappings the AI already approved — so a stored standard
    is guaranteed to reach the output. Human "overridden" mappings are always
    preserved."""
    def _eligible(m: MappingSuggestion) -> bool:
        return m.status == "suggested" or (force and m.status == "approved")

    business_object = await _business_object_for(conversion)
    if not business_object:
        return 0
    # Tenant scope: only this client's learnings + anything global. Prevents a
    # future client inheriting NextPower's source-system mappings.
    from app.services.client_service import client_id_for_conversion, scope_query
    _scope = await scope_query(await client_id_for_conversion(conversion))
    learned = await LearnedMapping.find({
        "kind": "column_mapping", "target_object": business_object, **_scope
    }).to_list()
    suppressed = await LearnedMapping.find({
        "kind": "suppress_field", "target_object": business_object, **_scope
    }).to_list()
    if not learned and not suppressed:
        return 0
    suppressed_targets = {lm.target_field for lm in suppressed if lm.target_field}
    by_target: dict[str, list[LearnedMapping]] = {}
    for lm in learned:
        if lm.target_field:
            by_target.setdefault(lm.target_field, []).append(lm)
    # When a field has several candidate mappings, try the ones that carry a real
    # VALUE TRANSFORM (PHONE_PART, CASE_WHEN, VALUE_MAP, SPLIT…) first. A transform
    # is a deliberate rule; a plain alias or a gold "direct_map" is just a guessed
    # column. Without this, a direct_map (e.g. Phone <- "Address Phone", an empty
    # column) can beat the intended PHONE_PART split of the populated "Phone" column
    # and blank the output. Ties keep original order.
    _STRONG_TRANSFORMS = {
        "PHONE_PART", "CASE_WHEN", "VALUE_MAP", "DATE_FORMAT", "SPLIT", "CONCAT",
        "COALESCE", "CONDITIONAL", "REGEX_EXTRACT", "REGEX_REPLACE", "SUBSTRING",
        "PREFIX", "SUFFIX", "ARITHMETIC", "NUMBER_FORMAT", "CROSSWALK_LOOKUP", "PAD",
        # REPLACE / REMOVE_SPECIAL_CHARS were missing, and that silently cost data:
        # the analyst rule "Taxpayer ID <- tax_id, strip spaces" sorted BELOW a plain
        # alias for the same field, so the alias won and the FBDI shipped
        # "7 5 -2 1 1 0 3 5 7" instead of "75-2110357". A REPLACE is just as
        # deliberate a rule as a VALUE_MAP — rank it accordingly.
        "REPLACE", "REMOVE_SPECIAL_CHARS", "MAP_BOOLEAN", "CONDITIONAL_DATE",
    }
    for _lst in by_target.values():
        _lst.sort(key=lambda lm: 0 if (lm.rule_type or "").upper() in _STRONG_TRANSFORMS else 1)
    # Oracle decorates required/conditional headers with asterisks and stray spaces
    # ("Supplier Name*", "Address Name *", "*Supplier Number", "**Bank Name"), and the
    # analyst mapping docs write the plain name ("Supplier Name"). Matching the learned
    # target_field to the template field_name by EXACT string therefore silently missed
    # every decorated field — the mapping existed in the library and simply never
    # applied. Keep exact match first (unambiguous), then fall back to the normalized
    # name. Ambiguous normalized keys (two different template fields collapsing to the
    # same key) are dropped from the fallback so we never guess.
    # MERGE candidates under the normalized key — do not drop on collision.
    #
    # An earlier version dropped any normalized key that two different learned
    # target_fields mapped onto, meaning to be cautious. That was wrong, and it
    # blanked a REQUIRED field: the library holds both "Supplier Site*" <- city
    # (PREFIX "BU ") from the analyst doc and "Supplier Site" <- "Address Label"
    # from an older doc. Both normalize to "suppliersite", so the key was
    # discarded; exact match then found only the "Address Label" row, whose source
    # column does not exist in an eBOS extract, and Supplier Site shipped empty.
    #
    # A collision here is almost always two SPELLINGS OF THE SAME FIELD, not two
    # different fields — Oracle's own headers repeat and get decorated differently
    # across docs. Merging is safe because the loop below already discards any
    # candidate whose source column is absent from the dataset, and the transform
    # sort above puts deliberate rules ahead of plain aliases.
    by_target_norm: dict[str, list[LearnedMapping]] = {}
    for _tf, _lst in by_target.items():
        k = _normalize(_tf)
        if k:
            by_target_norm.setdefault(k, []).extend(_lst)
    for _lst in by_target_norm.values():
        _lst.sort(key=lambda lm: 0 if (lm.rule_type or "").upper() in _STRONG_TRANSFORMS else 1)
    _suppressed_norm = {_normalize(t) for t in suppressed_targets if _normalize(t)}
    # Precedence: an explicit source→target mapping (analyst doc / transform /
    # steering) OUTRANKS an old gold "leave this blank" suppression for the same
    # field. Without this, a gold file that left e.g. Delivery Method or D-U-N-S
    # empty would keep suppressing them even after the analyst mapped them. So drop
    # any suppression whose field also has a column mapping — the mapping wins.
    suppressed_targets -= set(by_target.keys())
    _suppressed_norm -= set(by_target_norm.keys())
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
        if not _eligible(m):
            continue
        tgt_name = fields_map.get(m.target_field_id)
        if not tgt_name:
            continue
        candidates = by_target.get(tgt_name) or by_target_norm.get(_normalize(tgt_name))
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

    # Suppression pass — fields the gold example left blank override the AI's
    # aggressive mapping: any still-"suggested" mapping for such a target is set
    # not_applicable so it stays empty at output.
    if suppressed_targets or _suppressed_norm:
        for m in mappings:
            if not _eligible(m):
                continue
            tgt_name = fields_map.get(m.target_field_id)
            if tgt_name and (tgt_name in suppressed_targets
                             or _normalize(tgt_name) in _suppressed_norm):
                await m.set({
                    "source_column": None, "status": "not_applicable",
                    "review_required": 0, "approved_by": "learning-engine",
                    "approved_at": now,
                    "reason": "Suppressed — blank in the uploaded gold example",
                    "updated_at": now,
                })
                auto_count += 1

    # ── Mapping-document-only pass ────────────────────────────────────────────
    # When an analyst mapping document exists for this object, it is AUTHORITATIVE:
    # nothing may be sourced from a column the document does not sanction. The
    # deterministic matcher and the AI otherwise keep inventing plausible-looking
    # pairs that are simply wrong, and because a "suggested" mapping still exports
    # unless someone rejects it by hand, those guesses reach the FBDI. Observed on a
    # real eBOS vendor run: fax_num -> Account Currency Code, bank_code -> Fax Area
    # Code, country -> Fax Country Code, fax_num -> Customer Number, vend_num ->
    # ATTRIBUTE_NUMBER1. A fax number in a currency column fails the load outright.
    #
    # The allow-list is every (target field, source column) pair the learning
    # library holds for this object — i.e. the seeded analyst docs, transform rules
    # and gold-derived mappings. Anything still merely "suggested" whose pair is not
    # on that list is set not_applicable, so it exports blank instead of wrong.
    #
    # Deliberately untouched: mappings with no source column (constants and control
    # defaults such as Batch ID / Import Action), anything a human set
    # (overridden / approved / rejected), and objects with too few learnings to
    # constitute a document — a brand-new object with no doc must still map freely,
    # otherwise the tool would produce nothing at all for it.
    _MIN_DOC_ROWS = 5
    allowed_pairs: set[tuple[str, str]] = set()
    for lm in learned:
        if lm.target_field and lm.original_value:
            allowed_pairs.add((_normalize(lm.target_field), _normalize(lm.original_value)))
    if len(allowed_pairs) >= _MIN_DOC_ROWS:
        dropped = 0
        for m in mappings:
            if m.status != "suggested" or not m.source_column:
                continue
            tgt_name = fields_map.get(m.target_field_id)
            if not tgt_name:
                continue
            if (_normalize(tgt_name), _normalize(m.source_column)) in allowed_pairs:
                continue
            await m.set({
                "source_column": None, "status": "not_applicable",
                "review_required": 0, "approved_by": "learning-engine",
                "approved_at": now,
                "reason": ("Not in the mapping document for this object — the analyst "
                           "mapping document is authoritative, so unsanctioned "
                           "source columns are left blank rather than guessed."),
                "updated_at": now,
            })
            dropped += 1
            auto_count += 1
        if dropped:
            logger.info("mapping-doc-only: dropped %d unsanctioned mappings for %s",
                        dropped, business_object)

    # Constant-default pass — re-apply the constant values learned from gold
    # (kind="example_default") so a brand-new conversion of this object inherits
    # them WITHOUT the user re-uploading the gold file. Only fills targets that
    # are still "suggested" (i.e. not covered by a learned column mapping above).
    defaults = await LearnedMapping.find({
        "kind": "example_default", "target_object": business_object, **_scope
    }).to_list()
    if defaults:
        by_default: dict[str, LearnedMapping] = {}
        for lm in defaults:
            if lm.target_field:
                by_default.setdefault(lm.target_field, lm)
        # Same asterisk problem as the column pass: a default authored as
        # "Business Relationship" must still land on the template's
        # "Business Relationship*". Exact key first, normalized key as fallback.
        by_default_norm: dict[str, LearnedMapping] = {}
        _dclash: set[str] = set()
        for _tf, _lm in by_default.items():
            k = _normalize(_tf)
            if not k:
                continue
            if k in by_default_norm and by_default_norm[k] is not _lm:
                _dclash.add(k)
            by_default_norm.setdefault(k, _lm)
        for k in _dclash:
            by_default_norm.pop(k, None)
        for m in mappings:
            # A learned constant default is explicit intent to POPULATE the field,
            # so it also overrides a gold "not_applicable" suppression (not only
            # still-"suggested" targets) — e.g. an analyst default of Invoice Match
            # Option = Receipt must land even though the gold example left it blank.
            # Human "overridden"/"rejected" choices are still respected.
            if m.status in ("overridden", "rejected"):
                continue
            if not (_eligible(m) or m.status == "not_applicable"):
                continue
            tgt_name = fields_map.get(m.target_field_id)
            if not tgt_name:
                continue
            lm = by_default.get(tgt_name) or by_default_norm.get(_normalize(tgt_name))
            if lm is None:
                continue
            val = (lm.rule_config or {}).get("default_value")
            if val in (None, ""):
                val = lm.resolved_value
            if val in (None, ""):
                continue
            await m.set({
                "source_column": None, "default_value": val,
                "confidence": 0.96, "review_required": 0, "status": "approved",
                "approved_by": "learning-engine", "approved_at": now,
                "reason": f'Constant default re-applied from learning library (from "{lm.captured_from}")',
                "updated_at": now,
            })
            await lm.set({"records_auto_fixed": (lm.records_auto_fixed or 0) + 1})
            auto_count += 1
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
                "target_field": f.field_name, "rule_type": rule_type,
            })
    return propagated
