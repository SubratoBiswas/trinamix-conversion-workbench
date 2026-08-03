"""Backfill: everything already decided becomes an entry in the one dated store.

The store is only true if it holds every decision, including the ones made
before it existed. There are two kinds of those:

1. **The library.** Every ``LearnedMapping`` already is an entry — it just may
   not carry the date it was given on. Those rows get ``effective_date`` from
   ``captured_at``, which for a UI capture IS the moment the instruction was
   given, and for a seeded row is the closest thing on the row.

2. **The per-conversion rows.** Every ``MappingSuggestion`` a PERSON decided is
   a statement of exactly the same kind as anything in the library, and until
   now it lived only on its own conversion. It becomes an entry carrying
   ``approved_at`` — its existing date, never today's.

Both passes are idempotent and it must be possible to run this twice. Nothing
here re-stamps a date: pass 1 only fills a date that is missing, and pass 2
writes through ``mapping_store.record_decision``, which refuses to let an older
statement overwrite a newer one. A run that finds nothing to do writes nothing —
that matters, because ``captured_at`` being re-stamped by a seed that had nothing
to do is what inverted precedence on every redeploy.

This must land before writes are routed through the store, or existing projects
silently lose their history.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from app.models.conversion import Conversion
from app.models.fbdi import FBDIField
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion
from app.services import mapping_store as store

logger = logging.getLogger(__name__)

# What a backfilled grid decision records as its origin. Provenance only — it
# has no bearing on which entry wins.
CAPTURED_FROM = "grid (backfilled)"


async def date_the_library() -> dict:
    """Pass 1 — give every undated decision the date it already had.

    Runs over tombstoned rows too: a retired decision keeps its date so that
    restoring it puts it back in the right place in time. It does not revive
    anything.
    """
    rows = await LearnedMapping.find(
        {"kind": {"$in": sorted(store.DECISION_KINDS)}},
        include_deleted=True,
    ).to_list()
    dated = skipped = undatable = 0
    for row in rows:
        if getattr(row, "effective_date", None) is not None:
            skipped += 1
            continue
        captured_at = getattr(row, "captured_at", None)
        if captured_at is None:
            # Nothing on the row can place it in time. Leave it undated: it
            # counts as older than everything, which is the honest answer.
            undatable += 1
            continue
        await row.set({"effective_date": captured_at})
        dated += 1
    return {"examined": len(rows), "dated": dated,
            "already_dated": skipped, "undatable": undatable}


async def _field_names(conversion: Conversion) -> dict[Any, str]:
    if not conversion.template_id:
        return {}
    fields = await FBDIField.find(
        FBDIField.template_id == conversion.template_id).to_list()
    return {f.id: f.field_name for f in fields if getattr(f, "field_name", None)}


async def carry_over_conversion_decisions(*, conversion: Conversion) -> dict:
    """Pass 2, for one conversion — every human decision becomes an entry."""
    from app.services.client_service import client_id_for_conversion
    from app.services.learning_service import source_erp_for_conversion

    names = await _field_names(conversion)
    if not names:
        return {"considered": 0, "carried": 0, "already_newer": 0, "retired": 0}

    client_id = await client_id_for_conversion(conversion)
    source_erp = await source_erp_for_conversion(conversion)
    target_object = (getattr(conversion, "target_object", None) or None)

    mappings = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conversion.id).to_list()

    considered = carried = already_newer = retired = 0
    for m in mappings:
        field_name = names.get(m.target_field_id)
        if not field_name:
            continue
        entry = store.entry_from_mapping(
            m, target_field=field_name, client_id=client_id,
            source_erp=source_erp, target_object=target_object)
        if entry is None:
            continue
        considered += 1
        row = await store.record_decision(
            decision=entry.decision,
            target_field=field_name,
            value=entry.value,
            client_id=client_id,
            source_erp=source_erp,
            # Its EXISTING date. An edit with no timestamp keeps none, and so
            # counts as older than anything that can be placed in time.
            effective_date=(entry.effective_date
                            if entry.effective_date != datetime.min else None),
            captured_from=CAPTURED_FROM,
            captured_by=entry.captured_by,
            rule_type=entry.rule_type,
            rule_config=entry.rule_config,
            target_object=target_object,
            project_id=getattr(conversion, "project_id", None),
        )
        if row is None:
            # The analyst retired this decision in the Learning Centre. A
            # backfill is an automatic path, so it respects that.
            retired += 1
        elif store.effective_of(row) > (entry.effective_date or datetime.min):
            already_newer += 1
        else:
            carried += 1
    return {"considered": considered, "carried": carried,
            "already_newer": already_newer, "retired": retired}


async def carry_over_grid_decisions() -> dict:
    """Pass 2 — every human-decided mapping row in the database."""
    totals = {"conversions": 0, "considered": 0, "carried": 0,
              "already_newer": 0, "retired": 0, "failed": 0}
    async for conversion in Conversion.find_all():
        totals["conversions"] += 1
        try:
            one = await carry_over_conversion_decisions(conversion=conversion)
        except Exception as exc:                                   # noqa: BLE001
            # Reported, not swallowed. "Reached 12 conversions" and "threw
            # immediately" used to look identical to the analyst.
            totals["failed"] += 1
            totals.setdefault("errors", []).append(
                {"conversion": str(conversion.id), "error": str(exc)})
            logger.warning("backfill failed for conversion %s: %s",
                           conversion.id, exc)
            continue
        for key, val in one.items():
            totals[key] = totals.get(key, 0) + val
    return totals


async def backfill() -> dict:
    """Both passes. Idempotent — running it twice changes nothing the second time."""
    library = await date_the_library()
    grid = await carry_over_grid_decisions()
    result = {"library": library, "grid": grid}
    logger.info("one dated store backfill: %s", result)
    return result
