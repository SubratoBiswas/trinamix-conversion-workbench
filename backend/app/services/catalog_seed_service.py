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


async def _seed_catalog_file(path: Path, captured_from: str) -> dict:
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
        # source field → target field for this object (any origin).
        existing = await LearnedMapping.find_one(
            LearnedMapping.kind == "column_mapping",
            LearnedMapping.target_object == tgt_obj,
            LearnedMapping.target_field == tgt_field,
            LearnedMapping.original_value == src_field,
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


async def seed_mapping_catalog() -> dict:
    """Standard public-schema source→FBDI catalog."""
    return await _seed_catalog_file(_CATALOG, "metadata catalog")


async def seed_item_field_mappings() -> dict:
    """Analyst-confirmed NextPower Item standard-field mappings (all 5 sources)."""
    return await _seed_catalog_file(_ITEM_MAPPINGS, "NXT item field mapping doc")


async def seed_supplier_field_mappings() -> dict:
    """Analyst-confirmed NextPower Supplier mappings (NetSuite SS Vendors + eBOS)
    across the 6 supplier interface objects, incl. the Business Relationship
    value-map."""
    return await _seed_catalog_file(_SUPPLIER_MAPPINGS, "NXT supplier field mapping doc")
