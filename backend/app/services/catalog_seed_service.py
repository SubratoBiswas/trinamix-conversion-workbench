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

    seeded = skipped = 0
    for r in rows:
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        src_field = (r.get("source_field") or "").strip()
        if not (tgt_obj and tgt_field and src_field):
            continue
        # Additive + non-destructive: skip if a rule already maps this
        # source field → target field for this object WITHIN THE SAME SCOPE
        # (a global row and a client row for the same field can coexist).
        existing = await LearnedMapping.find_one(
            LearnedMapping.kind == "column_mapping",
            LearnedMapping.target_object == tgt_obj,
            LearnedMapping.target_field == tgt_field,
            LearnedMapping.original_value == src_field,
            LearnedMapping.client_id == client_id,
            LearnedMapping.is_global == is_global,
        )
        if existing:
            skipped += 1
            continue
        await LearnedMapping(
            kind="column_mapping",
            category="Column Mapping Alias",
            original_value=src_field,
            resolved_value=tgt_field,
            target_object=tgt_obj,
            target_field=tgt_field,
            client_id=client_id,
            is_global=is_global,
            # A row may carry a transformation (e.g. VALUE_MAP for Business
            # Relationship: Approved -> SPEND_AUTHORIZED). When it does, the
            # rule_config IS the transform config; otherwise rule_config holds
            # provenance metadata and no transform runs.
            rule_type=r.get("rule_type"),
            rule_config=(r.get("rule_config") if r.get("rule_type") else {
                "source_column": src_field,
                "source_label": r.get("source_label"),
                "fbdi_sheet": r.get("fbdi_sheet"),
                "confidence": r.get("confidence"),
            }),
            source_erp=r.get("source_system"),
            captured_from=captured_from,
        ).insert()
        seeded += 1

    if seeded or skipped:
        logger.info("%s: seeded %d, skipped %d existing (of %d rows)",
                    path.name, seeded, skipped, len(rows))
    return {"seeded": seeded, "skipped": skipped, "total": len(rows)}


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
