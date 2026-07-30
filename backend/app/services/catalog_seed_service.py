"""Seed the reusable Mapping Knowledge Base from the bundled metadata catalog.

`app/data/mapping_catalog.json` holds standard source→Oracle FBDI column mappings
assembled from public schemas (NetSuite, Infor SyteLine, Salesforce → Oracle
Fusion FBDI). On startup we upsert each row into the LearnedMapping registry as a
reusable ``column_mapping`` rule so the learning engine
(``apply_learned_to_conversion``) can auto-apply it whenever a matching source
file is converted in future.

Idempotent + non-destructive: an existing rule for the same
(target_object, target_field, source_field) is left untouched — this never
overwrites gold/prompt-captured learnings, only fills in catalog rows that
aren't there yet.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.models.learned import LearnedMapping

logger = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parent.parent / "data"
_CATALOG = _DATA / "mapping_catalog.json"
# Analyst-authored NextPower Item Field Mapping Document, distilled to the
# standard-field source→Oracle column mappings (the EFF rows are handled
# separately). Seeded the same way so an item file from any of the five source
# systems (Arena EBOS/Ratana Lee/Anaplan, SyteLine, NetSuite) auto-maps.
_ITEM_MAPPINGS = _DATA / "item_field_mappings.json"
# Analyst-authored NextPower Supplier Field Mapping doc (v3): NetSuite "SS
# Vendors" + Arena eBOS → the 6 Oracle supplier interface objects.
_SUPPLIER_MAPPINGS = _DATA / "supplier_field_mappings.json"
# Analyst-confirmed NextPower supplier TRANSFORM rows (from the Raman feedback /
# meeting): Delivery Method & Delivery Channel derived from the Email/Fax
# Transaction flags (CASE_WHEN), Phone/Fax split into Country/Area/Extension/Number
# (PHONE_PART), and Use Withholding Tax <- Default WT Code. Seeded as
# transformation-carrying column_mapping learnings across the 3 supplier objects.
_SUPPLIER_TRANSFORMS = _DATA / "supplier_transform_mappings.json"
# Analyst-authored NextPower Customer FBDI Field Mapping doc (V1): NetSuite
# customer export → the 19-sheet Oracle Fusion Customer Import (HZ_IMP/RA). The
# distinct source→target pairs (account/party keys + name/tax/email/phone/credit)
# propagate by field name across every interface sheet they appear in.
_CUSTOMER_MAPPINGS = _DATA / "customer_field_mappings.json"
# Analyst-authored NextPower Employee HDL Field Mapping doc (v3): Workday worker
# export → Oracle HCM via HDL. Only the true source-driven columns are seeded
# here (target_object "Employee HDL"); HDL constants, composite SourceSystemId
# keys and value maps are applied by the HDL generator from the loader schema.
_EMPLOYEE_HDL_MAPPINGS = _DATA / "employee_hdl_field_mappings.json"
# Analyst-authored NextPower BOM (Item Structure) FBDI mapping docs (Tracker +
# eBOS): Arena BOM extracts → the Item Structure import workbook
# (EGP_STRUCTURES_INTERFACE / EGP_COMPONENTS_INTERFACE). Source→target column
# rows PLUS the analyst-fixed constant defaults (Transaction Type=SYNC,
# Structure Name=Primary, Organization Code=NXT_ITEM_ORG).
_BOM_MAPPINGS = _DATA / "bom_field_mappings.json"
# Analyst DO-NOT-MAP list for NextPower Item: NetSuite source columns (the
# yellow-highlighted custom custitem_* attributes) that AI over-maps but the item
# mapping doc excludes. Seeded as ``ignore_source`` learnings so the mapper never
# uses them as a source for any Item field.
_ITEM_DONOTMAP = _DATA / "item_donotmap_columns.json"


async def _seed_catalog_file(path: Path, captured_from: str, *,
                             is_global: bool = False, client_id=None) -> dict:
    """Seed source→FBDI rows as reusable learnings. ``is_global`` marks
    client-agnostic public-schema rows (apply to every client); otherwise the rows
    are scoped to ``client_id`` (the analyst docs are one client's source data)."""
    if not path.exists():
        return {"seeded": 0, "skipped": 0, "note": f"{path.name} not found"}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog seed: could not read %s: %s", path.name, exc)
        return {"seeded": 0, "skipped": 0, "error": str(exc)}

    seeded = updated = deduped = skipped = retired = superseded = 0
    for r in rows:
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        src_field = (r.get("source_field") or "").strip()
        if not (tgt_obj and tgt_field and src_field):
            continue
        # A later analyst document can RE-POINT a target field at a different source
        # column. Seeding the new pair alone leaves the old one in place, so the
        # target ends up with two competing column_mappings and which one reaches
        # the FBDI depends on the per-target dedup tie-break (QA issue #6). Rows may
        # therefore declare the source(s) they supersede; those learnings are
        # retired here. User-deleted rows are left alone — they are already retired,
        # and resurrecting one just to delete it again would clear the tombstone.
        for old_src in (r.get("replaces_source_field") or []):
            if str(old_src).strip() == src_field:
                continue
            for prior in await LearnedMapping.find(
                LearnedMapping.kind == "column_mapping",
                LearnedMapping.target_object == tgt_obj,
                LearnedMapping.target_field == tgt_field,
                LearnedMapping.original_value == str(old_src).strip(),
            ).to_list():
                await prior.delete()
                superseded += 1

        rule_type = r.get("rule_type")
        rule_config = (r.get("rule_config") if rule_type else {
            "source_column": src_field, "source_label": r.get("source_label"),
            "fbdi_sheet": r.get("fbdi_sheet"), "confidence": r.get("confidence"),
        })
        # UPSERT-with-upgrade (scope-agnostic): find every prior row that maps this
        # source→target for this object, regardless of scope. Earlier seed runs may
        # have written a PLAIN row (no rule_type) or a wrongly-scoped copy that then
        # BLOCKS this seed's correct CASE_WHEN/PHONE_PART/is_global version. So we
        # heal in place: upgrade the first match to the seed's rule + scope, and
        # delete the rest as duplicates. Makes reseed self-correcting + de-duping.
        matches = await LearnedMapping.find(
            LearnedMapping.kind == "column_mapping",
            LearnedMapping.target_object == tgt_obj,
            LearnedMapping.target_field == tgt_field,
            LearnedMapping.original_value == src_field,
            include_deleted=True,
        ).to_list()
        # The user retired this learning — a reseed on the next restart must not
        # bring it back (QA issue #5). include_deleted above is what lets us SEE
        # the tombstone; without it the find returns nothing and we'd re-insert.
        if matches and all(getattr(m, "is_deleted", False) for m in matches):
            retired += 1
            continue
        matches = [m for m in matches if not getattr(m, "is_deleted", False)]
        if matches:
            keep = matches[0]
            upd: dict = {}
            if rule_type and keep.rule_type != rule_type:
                upd["rule_type"] = rule_type
                upd["rule_config"] = rule_config
            if is_global and not keep.is_global:
                upd["is_global"] = True
                upd["client_id"] = None
            elif client_id is not None and keep.client_id != client_id and not keep.is_global:
                upd["client_id"] = client_id
            if upd:
                await keep.set(upd)
                updated += 1
            else:
                skipped += 1
            for extra in matches[1:]:
                await extra.delete()
                deduped += 1
            continue
        await LearnedMapping(
            kind="column_mapping", category="Column Mapping Alias",
            original_value=src_field, resolved_value=tgt_field,
            target_object=tgt_obj, target_field=tgt_field,
            client_id=client_id, is_global=is_global,
            rule_type=rule_type, rule_config=rule_config,
            source_erp=r.get("source_system"), captured_from=captured_from,
        ).insert()
        seeded += 1

    if seeded or updated or deduped or skipped or retired or superseded:
        logger.info("%s: seeded %d, upgraded %d, de-duped %d, skipped %d, "
                    "retired-respected %d, superseded %d (of %d rows)",
                    path.name, seeded, updated, deduped, skipped, retired,
                    superseded, len(rows))
    return {"seeded": seeded, "updated": updated, "deduped": deduped,
            "skipped": skipped, "retired": retired, "superseded": superseded,
            "total": len(rows)}


async def _nextpower_client_id():
    """Resolve the bootstrap NextPower client id (the analyst docs are its data)."""
    from app.services.client_service import ensure_default_client
    c = await ensure_default_client()
    return c.id


async def seed_mapping_catalog() -> dict:
    """Standard public-schema source→FBDI catalog — GLOBAL (applies to every client)."""
    return await _seed_catalog_file(_CATALOG, "metadata catalog", is_global=True)


async def seed_item_field_mappings() -> dict:
    """Analyst-confirmed NextPower Item standard-field mappings (all 5 sources)."""
    return await _seed_catalog_file(_ITEM_MAPPINGS, "NXT item field mapping doc",
                                    client_id=await _nextpower_client_id())


async def seed_supplier_field_mappings() -> dict:
    """Analyst-confirmed NextPower Supplier mappings (NetSuite SS Vendors + eBOS)
    across the 6 supplier interface objects, incl. the Business Relationship
    value-map. GLOBAL: supplier source→FBDI mapping is source-system knowledge
    (NetSuite / eBOS conventions), reusable for every client and future project."""
    return await _seed_catalog_file(_SUPPLIER_MAPPINGS, "NXT supplier field mapping doc",
                                    is_global=True)


async def seed_supplier_transform_mappings() -> dict:
    """Analyst-confirmed supplier transforms: Delivery Method/Channel derivation
    (CASE_WHEN on Email/Fax Transaction flags), Phone/Fax split (PHONE_PART),
    Use Withholding Tax <- Default WT Code, and the eBOS direct 1:1 rows — GLOBAL
    so every client / future project gets them for both source vocabularies."""
    return await _seed_catalog_file(_SUPPLIER_TRANSFORMS, "NXT supplier transform rules",
                                    is_global=True)


async def seed_customer_field_mappings() -> dict:
    """Analyst-confirmed NextPower Customer mappings (NetSuite → Fusion Customer
    Import). Key references (entitynumber/entityid) plus name/tax/email/phone/
    credit; propagate by field name across the 19 HZ_IMP/RA interface sheets."""
    return await _seed_catalog_file(_CUSTOMER_MAPPINGS, "NXT customer field mapping doc",
                                    client_id=await _nextpower_client_id())


async def seed_employee_hdl_field_mappings() -> dict:
    """Analyst-confirmed NextPower Employee HDL mappings (Workday → Oracle HCM).
    Source-driven worker columns only; HDL constants, SourceSystemId keys and
    value maps are filled by the HDL generator from the loader schema."""
    return await _seed_catalog_file(_EMPLOYEE_HDL_MAPPINGS, "NXT employee HDL field mapping doc",
                                    client_id=await _nextpower_client_id())


async def seed_item_donotmap_columns() -> dict:
    """Seed the NextPower Item DO-NOT-MAP source columns as ``ignore_source``
    learnings (client-scoped to NextPower, target_object 'Item'). The mapping
    engine drops any Item mapping whose source column is on this list, so AI can't
    over-populate fields from source columns the analyst deliberately excluded
    (the yellow-highlighted NetSuite custitem_* attributes). Idempotent."""
    import json as _json
    if not _ITEM_DONOTMAP.exists():
        return {"seeded": 0, "note": "item_donotmap_columns.json not found"}
    try:
        doc = _json.loads(_ITEM_DONOTMAP.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    obj = (doc.get("target_object") or "Item").strip()
    cols = [c for c in (doc.get("columns") or []) if isinstance(c, str) and c.strip()]
    nid = await _nextpower_client_id()
    seeded = kept = 0
    for col in cols:
        col = col.strip()
        existing = await LearnedMapping.find(
            LearnedMapping.kind == "ignore_source",
            LearnedMapping.target_object == obj,
            LearnedMapping.original_value == col,
            include_deleted=True,
        ).first_or_none()
        if existing and getattr(existing, "is_deleted", False):
            kept += 1          # retired by the user — do not resurrect (issue #5)
            continue
        if existing:
            if existing.client_id != nid and not existing.is_global:
                await existing.set({"client_id": nid})
            kept += 1
            continue
        await LearnedMapping(
            kind="ignore_source", category="Do Not Map Source",
            original_value=col, resolved_value="",
            target_object=obj, target_field=None,
            client_id=nid, is_global=False,
            source_erp=doc.get("source_system"),
            captured_from="NXT item over-map feedback (NetSuite do-not-map list)",
        ).insert()
        seeded += 1
    logger.info("item do-not-map: seeded %d, kept %d (of %d cols)", seeded, kept, len(cols))
    return {"seeded": seeded, "kept": kept, "total": len(cols), "target_object": obj}


_SUPPLIER_DEFAULTS = _DATA / "supplier_default_values.json"


async def seed_supplier_default_values() -> dict:
    """Analyst-approved constant defaults for NextPower supplier objects (e.g.
    Supplier Site Invoice Match Option = 'Receipt'), seeded as ``example_default``
    learnings scoped to the NextPower client so future NextPower supplier
    conversions populate them automatically. Idempotent."""
    import json as _json
    if not _SUPPLIER_DEFAULTS.exists():
        return {"seeded": 0, "note": "supplier_default_values.json not found"}
    try:
        rows = _json.loads(_SUPPLIER_DEFAULTS.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    seeded = kept = 0
    for r in rows:
        const = r.get("constant")
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        if const is None or not (tgt_obj and tgt_field):
            continue
        existing = await LearnedMapping.find(
            LearnedMapping.kind == "example_default",
            LearnedMapping.target_object == tgt_obj,
            LearnedMapping.target_field == tgt_field,
            include_deleted=True,
        ).first_or_none()
        if existing and getattr(existing, "is_deleted", False):
            kept += 1          # retired by the user — do not resurrect (issue #5)
            continue
        if existing:
            if existing.resolved_value != str(const) or (existing.client_id != nid and not existing.is_global):
                await existing.set({"resolved_value": str(const), "client_id": nid})
            kept += 1
            continue
        await LearnedMapping(
            kind="example_default", category="Default Value",
            original_value="(constant)", resolved_value=str(const),
            target_object=tgt_obj, target_field=tgt_field,
            client_id=nid, is_global=False,
            captured_from="NXT supplier analyst default",
        ).insert()
        seeded += 1
    logger.info("supplier default values: seeded %d, kept %d", seeded, kept)
    return {"seeded": seeded, "kept": kept, "total": len(rows)}


async def seed_bom_field_mappings() -> dict:
    """Analyst-confirmed NextPower BOM (Item Structure) mappings, from the Tracker
    + eBOS BOM FBDI docs. Two parts, both scoped to the NextPower client:

    * source→target COLUMN rows (Item/Component/Structure item names, Item
      Sequence, Quantity) for BOTH Arena source vocabularies — seeded via the
      shared catalog upsert so a BOM file from either source auto-maps; and
    * the analyst-fixed CONSTANT defaults on EGP_STRUCTURES_INTERFACE
      (Transaction Type=SYNC, Structure Name=Primary, Organization Code=
      NXT_ITEM_ORG) — seeded as ``example_default`` learnings (rows carry a
      ``constant`` instead of a ``source_field``, which the column seeder skips).
    Idempotent: constants are find-or-upgraded, never duplicated.
    """
    import json as _json
    nid = await _nextpower_client_id()
    # (1) source→target column mappings (rows with a source_field)
    res = await _seed_catalog_file(_BOM_MAPPINGS, "NXT BOM field mapping doc", client_id=nid)
    # (2) constant defaults (rows with a "constant" and no source_field)
    const_seeded = const_kept = 0
    try:
        rows = _json.loads(_BOM_MAPPINGS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        const = r.get("constant")
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        if const is None or not (tgt_obj and tgt_field):
            continue
        existing = await LearnedMapping.find(
            LearnedMapping.kind == "example_default",
            LearnedMapping.target_object == tgt_obj,
            LearnedMapping.target_field == tgt_field,
            include_deleted=True,
        ).first_or_none()
        if existing and getattr(existing, "is_deleted", False):
            const_kept += 1    # retired by the user — do not resurrect (issue #5)
            continue
        if existing:
            upd = {}
            if existing.resolved_value != str(const):
                upd["resolved_value"] = str(const)
            if existing.client_id != nid and not existing.is_global:
                upd["client_id"] = nid
            if upd:
                await existing.set(upd)
            const_kept += 1
            continue
        await LearnedMapping(
            kind="example_default", category="Default Value",
            original_value="(constant)", resolved_value=str(const),
            target_object=tgt_obj, target_field=tgt_field,
            client_id=nid, is_global=False,
            captured_from="NXT BOM field mapping doc",
        ).insert()
        const_seeded += 1
    res["constants_seeded"] = const_seeded
    res["constants_kept"] = const_kept
    return res


from datetime import datetime as _dt

_SUPPLIER_CORRECTIONS = _DATA / "supplier_corrections_30jul.json"


async def seed_supplier_corrections_30jul() -> dict:
    """Seed the analyst's 30-Jul Supplier corrections as client-scoped learnings.

    Three shapes, each mapped to the learning kind that already carries it:
      blank    -> suppress_field   (nothing may fill it, including control defaults)
      constant -> example_default
      rule     -> column_mapping carrying the rule_type + config

    Seeded LAST of the supplier seeds so these values win a clash — they are the most
    recent analyst instruction. A mapping a person has since edited and approved in the
    UI still outranks all of it; apply_learned_to_conversion refuses to overwrite
    anything not approved by the engine itself.

    Idempotent, and it honours tombstones: a correction the analyst later retires is
    not resurrected on the next restart.
    """
    import json as _json
    if not _SUPPLIER_CORRECTIONS.exists():
        return {"seeded": 0, "note": "supplier_corrections_30jul.json not found"}
    try:
        doc = _json.loads(_SUPPLIER_CORRECTIONS.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    src = "analyst corrections 30-Jul-2026"
    seeded = updated = retired = 0
    for r in doc.get("rules", []):
        obj, fld = (r.get("target_object") or "").strip(), (r.get("target_field") or "").strip()
        action = (r.get("action") or "").strip()
        if not (obj and fld and action):
            continue
        kind = {"blank": "suppress_field", "constant": "example_default",
                "rule": "column_mapping"}.get(action)
        if not kind:
            continue
        resolved = ("" if action == "blank"
                    else str(r.get("value", "")) if action == "constant" else fld)
        # Identity includes captured_from for a RULE correction. A field like
        # Alternate Name legitimately carries several column_mapping rows — one per
        # source column — so keying only on (kind, object, field, client) matched an
        # arbitrary alias and updated THAT instead of creating the correction. Live,
        # two of the twelve corrections went missing exactly this way.
        _q = [LearnedMapping.kind == kind,
              LearnedMapping.target_object == obj,
              LearnedMapping.target_field == fld,
              LearnedMapping.client_id == nid]
        if kind == "column_mapping":
            _q.append(LearnedMapping.captured_from == src)
        existing = await LearnedMapping.find_one(*_q, include_deleted=True)
        if existing and getattr(existing, "is_deleted", False):
            retired += 1
            continue
        payload = {
            "resolved_value": resolved,
            "rule_type": r.get("rule_type") or ("suppress" if action == "blank" else None),
            "rule_config": r.get("rule_config") or {"note": r.get("note", "")},
            "captured_from": src, "captured_at": _dt.utcnow(),
        }
        if existing:
            await existing.set(payload)
            updated += 1
            continue
        await LearnedMapping(
            kind=kind, category=("Left blank on purpose" if action == "blank"
                                 else "Default Value" if action == "constant"
                                 else "Column Mapping Alias"),
            original_value=("(blank)" if action == "blank"
                            else "(constant)" if action == "constant" else fld),
            target_object=obj, target_field=fld,
            client_id=nid, is_global=False, **payload,
        ).insert()
        seeded += 1
    return {"seeded": seeded, "updated": updated, "left_retired": retired,
            "protected_values": doc.get("protected_values", []),
            "open_questions": doc.get("_open_questions", [])}


_SUPPLIER_SOURCE_MAP = _DATA / "supplier_source_mapping_30jul.json"


async def seed_supplier_source_mapping() -> dict:
    """Seed the GREEN rows of NXT Supplier Mapping_30Jul26.xlsx as client-scoped,
    source-system-scoped column mappings.

    Green is the workbook's own legend for "Mapped". The other colours mean
    Questions to NextPower, Duplicate, Oracle-required-but-missing and Not-to-bring —
    none of which is an instruction to map something, so none is imported. Rows whose
    Oracle field is "DFF" or "standard" are excluded too: a descriptive flexfield is a
    decision about where a value belongs, not a mapping the engine can apply.

    Keyed by source system, so NetSuite's mapping for a field and SyteLine's are two
    rows. Idempotent, and it honours tombstones like every other seeder — a learning
    the analyst retired is not resurrected by a reseed.
    """
    import json as _json
    if not _SUPPLIER_SOURCE_MAP.exists():
        return {"seeded": 0, "note": "supplier_source_mapping_30jul.json not found"}
    try:
        doc = _json.loads(_SUPPLIER_SOURCE_MAP.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    src_label = "NXT Supplier Mapping 30Jul26 (green rows)"
    seeded = kept = retired = 0
    for m in doc.get("mappings", []):
        tgt, col = (m.get("target_field") or "").strip(), (m.get("source_column") or "").strip()
        if not (tgt and col):
            continue
        existing = await LearnedMapping.find_one(
            LearnedMapping.kind == "column_mapping",
            LearnedMapping.target_object == "Supplier",
            LearnedMapping.target_field == tgt,
            LearnedMapping.original_value == col,
            LearnedMapping.source_erp == m.get("source_erp"),
            include_deleted=True,
        )
        if existing and getattr(existing, "is_deleted", False):
            retired += 1
            continue
        if existing:
            kept += 1
            continue
        await LearnedMapping(
            kind="column_mapping", category="Column Mapping Alias",
            original_value=col, resolved_value=tgt,
            target_object="Supplier", target_field=tgt,
            client_id=nid, is_global=False, source_erp=m.get("source_erp"),
            rule_config={"oracle_page": m.get("sheet") or "", "note": m.get("note") or ""},
            captured_from=src_label,
        ).insert()
        seeded += 1
    return {"seeded": seeded, "already_present": kept, "left_retired": retired,
            "open_questions": doc.get("_open_questions", [])}


_SUPPLIER_STRATEGY = _DATA / "supplier_strategy_defaults.json"


async def seed_supplier_strategy_defaults() -> dict:
    """Seed the NextPower Supplier Conversion Strategy (v1.0, section 7) as
    client-scoped ``example_default`` learnings.

    The strategy is a SIGNED functional specification, so it is the governing
    authority for this conversion: seeding it as learnings puts it ahead of the
    deterministic control constants and ahead of AI in the mapping precedence.
    Two rules are deliberately NOT seeded (see ``open_items`` in the JSON): the
    Procurement BU crosswalk does not exist yet, and the PROSPECTIVE branch of
    Business Relationship needs its source column confirmed — guessing either
    would put wrong values into a load file that looks correct. Idempotent, and
    it respects tombstones like every other seeder.
    """
    import json as _json
    if not _SUPPLIER_STRATEGY.exists():
        return {"seeded": 0, "note": "supplier_strategy_defaults.json not found"}
    try:
        doc = _json.loads(_SUPPLIER_STRATEGY.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    src = doc.get("_source", "NextPower Supplier Conversion Strategy")
    seeded = kept = retired = derived = 0
    for r in doc.get("rules", []):
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        if not (tgt_obj and tgt_field):
            continue
        const = r.get("constant")
        if const is None:
            derived += 1        # derive rules (city / BU-city) are mappings, not constants
            continue
        existing = await LearnedMapping.find(
            LearnedMapping.kind == "example_default",
            LearnedMapping.target_object == tgt_obj,
            LearnedMapping.target_field == tgt_field,
            include_deleted=True,
        ).first_or_none()
        if existing and getattr(existing, "is_deleted", False):
            retired += 1
            continue
        if existing:
            if existing.resolved_value != str(const):
                await existing.set({"resolved_value": str(const), "client_id": nid,
                                    "captured_from": src})
            kept += 1
            continue
        await LearnedMapping(
            kind="example_default", category="Default Value",
            original_value="(constant)", resolved_value=str(const),
            target_object=tgt_obj, target_field=tgt_field,
            client_id=nid, is_global=False,
            rule_config={"condition": r.get("condition", ""),
                         "fill_blank_only": bool(r.get("fill_blank_only"))},
            captured_from=src,
        ).insert()
        seeded += 1
    # Analyst refinements captured in live review: blank-on-purpose fields and
    # row-aware rules (e.g. Alternate Name blanked when it duplicates Supplier
    # Name). Seeded as suppress_field / column_mapping-with-rule learnings.
    a_sup = a_rule = a_skip = 0
    for r in (doc.get("analyst_rules", {}) or {}).get("rules", []):
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        if not (tgt_obj and tgt_field):
            continue
        if r.get("suppress"):
            kind, cat, orig, res, rtype, rcfg = ("suppress_field", "Suppressed (analyst rule)",
                                                 "(blank)", "", "suppress", {})
        elif r.get("rule_type") == "BLANK_IF_EQUALS":
            kind, cat = "rule", "Transformation Rule"
            orig = (r.get("rule_config") or {}).get("other_column", "")
            res, rtype, rcfg = tgt_field, "BLANK_IF_EQUALS", r.get("rule_config") or {}
        else:
            a_skip += 1        # constants already covered above, or not implementable yet
            continue
        existing = await LearnedMapping.find(
            LearnedMapping.kind == kind,
            LearnedMapping.target_object == tgt_obj,
            LearnedMapping.target_field == tgt_field,
            include_deleted=True,
        ).first_or_none()
        if existing and getattr(existing, "is_deleted", False):
            retired += 1
            continue
        if existing:
            kept += 1
            continue
        await LearnedMapping(
            kind=kind, category=cat, original_value=orig, resolved_value=res,
            target_object=tgt_obj, target_field=tgt_field,
            rule_type=rtype, rule_config=rcfg,
            client_id=nid, is_global=False,
            captured_from=(doc.get("analyst_rules", {}) or {}).get("_source", src),
        ).insert()
        if kind == "suppress_field":
            a_sup += 1
        else:
            a_rule += 1

    logger.info("supplier strategy defaults: seeded %d, kept %d, retired-respected %d, "
                "derived-skipped %d | analyst: %d suppressions, %d rules, %d skipped",
                seeded, kept, retired, derived, a_sup, a_rule, a_skip)
    return {"seeded": seeded, "kept": kept, "retired": retired,
            "derived_not_constant": derived,
            "analyst_suppressions": a_sup, "analyst_rules": a_rule,
            "analyst_skipped": a_skip,
            "open_items": doc.get("open_items", [])}
