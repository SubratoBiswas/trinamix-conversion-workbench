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

_CATALOG = Path(__file__).resolve().parent.parent / "data" / "mapping_catalog.json"


async def seed_mapping_catalog() -> dict:
    if not _CATALOG.exists():
        return {"seeded": 0, "skipped": 0, "note": "catalog file not found"}
    try:
        rows = json.loads(_CATALOG.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("mapping_catalog: could not read catalog: %s", exc)
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
            rule_type=None,
            rule_config={
                "source_column": src_field,
                "source_label": r.get("source_label"),
                "fbdi_sheet": r.get("fbdi_sheet"),
                "confidence": r.get("confidence"),
            },
            source_erp=r.get("source_system"),
            captured_from="metadata catalog",
        ).insert()
        seeded += 1

    if seeded or skipped:
        logger.info("mapping_catalog: seeded %d, skipped %d existing (of %d rows)",
                    seeded, skipped, len(rows))
    return {"seeded": seeded, "skipped": skipped, "total": len(rows)}
