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

    seeded = updated = deduped = skipped = 0
    for r in rows:
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        src_field = (r.get("source_field") or "").strip()
        if not (tgt_obj and tgt_field and src_field):
            continue
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
        ).to_list()
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

    if seeded or updated or deduped or skipped:
        logger.info("%s: seeded %d, upgraded %d, de-duped %d, skipped %d (of %d rows)",
                    path.name, seeded, updated, deduped, skipped, len(rows))
    return {"seeded": seeded, "updated": updated, "deduped": deduped,
            "skipped": skipped, "total": len(rows)}


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
        ).first_or_none()
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
        ).first_or_none()
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
