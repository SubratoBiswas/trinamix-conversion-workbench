"""Backfill coded-value metadata onto FBDIFields that were parsed before the
LOV parser existed.

Templates already loaded in Mongo have their ``description`` text stored, and the
description is the only input ``lov_service.enrich_field`` needs — so we can mine
``allowed_values`` / ``lookup_type`` / ``default_if_blank`` for every existing
field without re-uploading or re-parsing a single workbook.

Idempotent and non-destructive: a field is only written when the derived metadata
actually differs, and we never clear values a human (or a lookup import) supplied.
"""
from __future__ import annotations

import logging

from app.models.fbdi import FBDIField
from app.services.lov_service import enrich_field

logger = logging.getLogger(__name__)


async def backfill_lov_metadata(force: bool = False) -> dict:
    fields = await FBDIField.find(FBDIField.description != None).to_list()  # noqa: E711
    updated = coded = 0

    for f in fields:
        # Never stomp codes that came from a verified instance lookup import.
        if f.allowed_values and any(v.get("verified") for v in f.allowed_values) and not force:
            coded += 1
            continue

        lov = enrich_field(f.field_name, f.description)
        if not lov["lookup_type"] and not lov["allowed_values"]:
            continue

        changed = False
        if lov["lookup_type"] and f.lookup_type != lov["lookup_type"]:
            f.lookup_type = lov["lookup_type"]
            changed = True
        if lov["allowed_values"] and f.allowed_values != lov["allowed_values"]:
            f.allowed_values = lov["allowed_values"]
            changed = True
        if lov["default_if_blank"] and not f.default_if_blank:
            f.default_if_blank = lov["default_if_blank"]
            changed = True
        if lov["validation_notes"] and f.validation_notes != lov["validation_notes"]:
            f.validation_notes = lov["validation_notes"]
            changed = True

        if changed:
            await f.save()
            updated += 1
        coded += 1

    logger.info("lov_backfill: %d coded columns, %d updated", coded, updated)
    return {"scanned": len(fields), "coded_columns": coded, "updated": updated}
