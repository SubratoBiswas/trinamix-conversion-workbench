"""The one dated store.

Agreed with the analyst, 02-Aug-2026:

    "Mappings, learnings and user inputs should be stored in the same place with
    date (with respect to client and source), whichever is latest as per date the
    mapping will happen in that way, and the same will be used for existing
    projects and future projects."

And, on per-conversion overrides:

    "That's fine, the analyst mapping wins as that's the latest mapping as per
    date."

So: every statement about how a field should be mapped is a dated entry, keyed
``(client_id, source_erp, target_field)``. The mapping workbook, the gold
standard, a captured learning, the steer box, a grid edit and a custom rule all
write entries of the same shape. **The newest one wins.** Who wrote it is
provenance, recorded so a human can trace the decision — it is never precedence.

There is no object scope, no project scope and no per-conversion override. An
edit is the client's newest statement about that field and it applies
everywhere, to projects that already exist and to projects not yet created.

Why it is one store, and why the key is that short: there used to be two stores —
``LearnedMapping`` (dated, client+source scoped) and ``MappingSuggestion`` (the
per-conversion rows, which generation actually read) — and the library was
COPIED into the rows. Every "the screen says one thing and the file says
another" bug came out of those two disagreeing, and each extra scope was another
axis they could disagree on. Suppression-loses-to-mapping, most-specific-wins,
object-key spelling drift and the six-call-sites fan-out were all precedence
tiers competing with the date. They are gone. One key, one date, one winner.

This module is the whole of that rule. It is pure — it takes entries and answers
questions about them — so it can be tested against a table of competing entries
without a database.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

# ── The read/resolve core now lives in the domain ───────────────────────────
# Imported (and thereby re-exported) so every ``mapping_store.X`` reference and
# ``from app.services.mapping_store import X`` keeps working unchanged. The rule
# itself — Entry, applies, _order, resolve — is in app.domain.store.resolver; this
# module is now just the Mongo-facing adapter that reads rows and writes decisions.
from app.domain.store.resolver import (  # noqa: F401  (re-exported for callers)
    SOURCE_COLUMN, DEFAULT_VALUE, SUPPRESS, RULE,
    DECISIONS, KIND_TO_DECISION, DECISION_TO_KIND, DECISION_KINDS,
    _NORMALIZE_RE, normalise_field, normalise_source, client_key,
    Entry, effective_of, value_of, value_for,
    entry_of, entries_of, sheet_allowed, applies,
    _order, resolve, resolve_all, _as_entries,
    ENGINE, decided_by_a_person, entry_from_mapping,
)

log = logging.getLogger(__name__)

# ── The one writer ───────────────────────────────────────────────────────────
#
# Every path that records a mapping decision comes through here: the mapping
# workbook, the gold standard, a captured learning, the steer box, a grid edit
# and a custom rule. That is the point. A decision written any other way is a
# decision the resolver cannot see and the analyst cannot retire, and the
# six-call-sites problem — one object-scoped fan-out fixed three times in three
# different call sites because there was no single place to fix it — is exactly
# what this whole change exists to end. `tests/test_one_dated_store_writes.py`
# asserts that no other path writes one.

# How each decision is laid out on the stored row. The column names are the old
# vocabulary and stay as they are, because the Learning Centre, every seeder and
# the analyst's own JSON all speak it.
_DEFAULT_CATEGORY = {
    SOURCE_COLUMN: "Column Mapping Alias",
    DEFAULT_VALUE: "Default Value",
    SUPPRESS: "Left blank on purpose",
    RULE: "Transformation Rule",
}


def _row_shape(decision: str, value: Optional[str], target_field: str,
               rule_type: Optional[str], rule_config: dict) -> dict:
    """``(original_value, resolved_value, rule_type, rule_config)`` for a decision."""
    if decision == DEFAULT_VALUE:
        cfg = dict(rule_config or {})
        cfg.setdefault("default_value", "" if value is None else str(value))
        return {"original_value": "(default)",
                "resolved_value": "" if value is None else str(value),
                "rule_type": rule_type or "default", "rule_config": cfg}
    if decision == SUPPRESS:
        return {"original_value": "(blank)", "resolved_value": "",
                "rule_type": rule_type or "suppress",
                "rule_config": dict(rule_config or {})}
    # source_column and rule both name a source column and resolve to the field.
    return {"original_value": "" if value is None else str(value),
            "resolved_value": str(target_field),
            "rule_type": rule_type, "rule_config": dict(rule_config or {})}


def sheet_key(sheets: Iterable[Any] | None) -> frozenset:
    """THE FOURTH KEY DIMENSION: which interface this decision is about.

    Analyst, 05-Aug: "client, source, field, sheet should be 4 dimensions for all
    columns, for all source and fields." Insert Update Indicator is why — it must
    be "I" on one interface and blank on another, and until now it could not,
    because the store keyed (client, source, field) and one field held ONE value.
    A per-sheet keep-blank and a field-wide default collapsed into a single row.

    A decision's sheet identity is the normalised set of sheets it names. The
    empty set is FIELD-WIDE — every decision written before this, and any decision
    the analyst makes "for all sheets", carries it. So an empty-set write behaves
    exactly as it did before: one row per field. The dimension only splits rows
    once a writer names a sheet, which keeps this change inert until it is used.

    exclude_sheets is deliberately not part of the identity. The per-sheet UI
    writes an inclusion (``sheets=[X]``); "everywhere except X" remains one
    field-wide statement that the resolver narrows at read time.
    """
    return frozenset(
        normalise_field(s) for s in (sheets or []) if str(s).strip())


def _row_sheet_key(row: Any) -> frozenset:
    return sheet_key(getattr(row, "sheets", None))


def _merge_sheets(previous: Iterable[Any], incoming: Iterable[Any]) -> list:
    """Union within one sheet identity.

    Rows are now partitioned by ``sheet_key`` before they reach here, so previous
    and incoming already share an identity — this only de-dups spellings and keeps
    the list stable. Across identities there is nothing to merge: a ``sheets=["A"]``
    statement and a ``sheets=["B"]`` statement are two different rows now, one per
    interface, which is the whole point of the fourth dimension.
    """
    seen, merged = set(), []
    for item in [*(previous or []), *(incoming or [])]:
        key = str(item).strip().lower()
        if item and key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


async def find_rows_for_key(*, target_field: str, client_id: Any = None,
                            source_erp: str | None = None,
                            include_deleted: bool = True) -> list:
    """Every stored decision for ``(client, source, field)``, whatever the object.

    The object is deliberately not in the query. A decision captured under
    "Supplier" and one captured under "Supplier Import" are the same client
    saying two things about one field, and the later one is what they want. It
    was possible for those two spellings to hide from each other, and that is a
    documented way for a decision to disappear.
    """
    from app.models.learned import LearnedMapping
    want_field = normalise_field(target_field)
    # Pull one field's rows through the field_key index instead of fetching every
    # decision row and filtering in Python — the full-collection scan that cost ~15s
    # a call on the shared instance, starved the seed and slowed generation. The
    # ``field_key: None`` arm keeps legacy rows the startup backfill has not reached
    # yet visible, so a decision cannot disappear mid-migration; once backfilled there
    # are none and the query is a pure index seek. The Python filter below is
    # unchanged and remains the authority on the match.
    rows = await LearnedMapping.find(
        {"kind": {"$in": sorted(DECISION_KINDS)},
         "target_field": {"$ne": None},
         "$or": [{"field_key": want_field}, {"field_key": None}]},
        include_deleted=include_deleted,
    ).to_list()
    want_client = client_key(client_id)
    want_source = normalise_source(source_erp)
    out = []
    for row in rows:
        if normalise_field(getattr(row, "target_field", None)) != want_field:
            continue
        if client_key(getattr(row, "client_id", None)) != want_client:
            continue
        if normalise_source(getattr(row, "source_erp", None)) != want_source:
            continue
        out.append(row)
    return out


async def backfill_field_key() -> dict:
    """Populate ``field_key`` on legacy rows so find_rows_for_key's index can be used.

    field_key is the normalised target_field, set on every write from now on. Rows
    written before that column existed have it null, and while any remain null the
    resolver still fetches them (the ``field_key: None`` arm of the query) — correct
    but slow. This stamps them in ONE read + ONE bulk write rather than a row-at-a-time
    loop, so it finishes in a couple of operations even on the shared instance and is
    not the 30-minute crawl the per-row seed was. Idempotent: it only touches rows
    whose key is missing, so a second run writes nothing.
    """
    from app.models.learned import LearnedMapping
    from pymongo import UpdateOne

    coll = LearnedMapping.get_motor_collection()
    ops = []
    cursor = coll.find({"$or": [{"field_key": None}, {"field_key": {"$exists": False}}]},
                       {"target_field": 1})
    async for doc in cursor:
        ops.append(UpdateOne({"_id": doc["_id"]},
                             {"$set": {"field_key": normalise_field(doc.get("target_field"))}}))
    if ops:
        await coll.bulk_write(ops, ordered=False)
    log.info("field_key backfill: stamped %d row(s)", len(ops))
    return {"backfilled": len(ops)}


# What a write should do about the rows already stored. Pure, so that "an older
# statement never overwrites a newer one" — the property every replay, reseed and
# backfill depends on — can be tested against a table instead of a database.
SKIP_RETIRED = "skip-retired"
SKIP_OLDER = "skip-older"
UPDATE = "update"
INSERT = "insert"


REFRESH = "refresh"   # update the content, leave the stored date exactly where it is


def plan_write(existing: Sequence[Any], *, kind: str, when: Optional[datetime],
               revive: bool = False, captured_from: str | None = None
               ) -> tuple[str, Any]:
    """``(what to do, which row)`` for one incoming statement.

    ``when=None`` means the statement carries no date — a bundled data file that
    never said when it was written. Such a statement cannot win a contest, so it
    may only create a row or **refresh one it wrote itself** (same
    ``captured_from``). That is what stops a startup seed from walking the
    analyst's own correction backwards, and it is why it does not simply stamp
    itself with today: a seed re-running on every boot would then out-rank every
    instruction ever given, which is exactly what ``captured_at`` used to do.
    """
    # ONE ROW PER KEY, WHATEVER THE KIND. (05-Aug)
    #
    # This partitioned by ``kind``: a column_mapping, an example_default and a
    # rule for the same field were three separate rows, each with its own date,
    # all alive at once. That is a fallback — and a fallback is something
    # generation can reach for when the newest statement does not suit it.
    #
    # Receipt Routing is what it cost. A rule dated 03-Aug and a fixed value dated
    # 04-Aug both existed; generation consulted them in code order rather than
    # date order, and the file shipped a third value from a 13-Jul document.
    # Three statements about one field, none of them wrong on its own.
    #
    # Analyst, 05-Aug: "there should be just one row for each mapping or fixed
    # value stored with date ... so that it does not even have other previous
    # mappings or values to refer to even if it wants."
    #
    # So the four DECISION_KINDS now compete for ONE row. A fixed value typed
    # today does not outrank yesterday's rule — it REPLACES it, and
    # record_decision deletes anything else still sitting under the key.
    #
    # Deliberately NOT true of the other kinds on LearnedMapping: ``crosswalk``
    # is one row per source VALUE and must stay many-per-field, and
    # ``file_signature``, ``ignore_source`` and ``reference_standard`` are not
    # statements about how a field maps. find_rows_for_key already queries only
    # the four, which is what makes collapsing them safe.
    decisions = [r for r in existing if getattr(r, "kind", None) in DECISION_KINDS]
    if not decisions:
        return (INSERT, None)
    # Newest first, so an edit lands on the row the resolver would have picked
    # rather than on whichever happened to be inserted first.
    decisions = sorted(decisions, key=effective_of, reverse=True)
    row = decisions[0]
    if getattr(row, "is_deleted", False) and not revive:
        # The analyst retired this. An automatic path — a seed, an auto-capture,
        # a replay — must not bring it back.
        return (SKIP_RETIRED, row)
    if when is None:
        if captured_from and getattr(row, "captured_from", None) == captured_from:
            # The same file restating itself. Not a competing statement.
            return (REFRESH, row)
        return (SKIP_OLDER, row)
    if when < effective_of(row):
        # Someone has already said something later about this field. Saying an
        # older thing again does not change what they want.
        return (SKIP_OLDER, row)
    return (UPDATE, row)


async def record_decision(*, decision: str, target_field: str,
                          value: Optional[str] = None, client_id: Any = None,
                          source_erp: str | None = None,
                          effective_date: Optional[datetime] = None,
                          captured_from: str, captured_by: str | None = None,
                          rule_type: str | None = None,
                          rule_config: dict | None = None,
                          target_object: str | None = None,
                          project_id: Any = None,
                          undated: bool = False,
                          sheets: Sequence[str] | None = None,
                          exclude_sheets: Sequence[str] | None = None,
                          category: str | None = None,
                          is_global: bool = False,
                          original_value: str | None = None,
                          resolved_value: str | None = None,
                          revive: bool = False):
    """Record one dated statement about one field. The only way to write one.

    ``effective_date`` is when the INSTRUCTION was given. Pass it when you know
    it — a seeded file carries the date of the file, a grid edit carries the
    moment the analyst pressed the button. Omitted, it is now, which is right for
    something typed into the UI and wrong for anything replayed, so replays must
    pass it.

    **An older statement never overwrites a newer one.** A re-run of a seed, a
    backfill, a redeploy: if what is already stored was said later, this returns
    it untouched and nothing moves. That is what makes every writer here safe to
    run twice, and it is why a startup seed that finds nothing to do cannot
    re-stamp the library — which is precisely what ``captured_at`` did, inverting
    precedence on every redeploy.

    Returns the stored row, or ``None`` when the analyst has retired this
    decision and ``revive`` was not set.
    """
    from app.models.learned import LearnedMapping

    if decision not in DECISION_TO_KIND:
        raise ValueError(f"not a mapping decision: {decision!r}")
    if not target_field or not str(target_field).strip():
        raise ValueError("a decision must be about a target field")

    kind = DECISION_TO_KIND[decision]
    now = datetime.utcnow()
    # `undated` is for bundled data files that never said when they were written.
    # They carry no date rather than today's: a seed re-running on every boot and
    # stamping itself with `now` would out-rank every instruction ever given.
    when = None if (undated and effective_date is None) else (effective_date or now)
    shape = _row_shape(decision, value, target_field, rule_type, rule_config or {})
    if original_value is not None:
        shape["original_value"] = original_value
    if resolved_value is not None:
        shape["resolved_value"] = resolved_value

    # The identity of a stored row is (client, source, field, SHEET) — the key.
    # One field on one interface holds at most one live decision, and those compete
    # by date. A decision about a DIFFERENT interface is a different row, which is
    # what lets Insert Update Indicator be "I" on one sheet and blank on another.
    # The object is NOT part of the identity; the sheet is (05-Aug).
    _want_sheets = sheet_key(sheets)
    rows = await find_rows_for_key(target_field=target_field, client_id=client_id,
                                   source_erp=source_erp, include_deleted=True)
    # Only rows about the SAME interface(s) compete for this write. A field-wide
    # row (empty set) and a sheet-B row are left untouched by a sheet-A write.
    rows = [r for r in rows if _row_sheet_key(r) == _want_sheets]
    action, row = plan_write(rows, kind=kind, when=when, revive=revive,
                             captured_from=captured_from)

    if action == SKIP_RETIRED:
        return None
    if action == SKIP_OLDER:
        return row
    if action in (UPDATE, REFRESH):
        patch = {
            **shape,
            # THE KIND MOVES WITH THE STATEMENT.
            #
            # One row per key means the row's kind is whatever the newest
            # statement is: a field that carried a rule yesterday and a fixed
            # value today is a default_value row now, not two rows. Leaving the
            # old kind in place would have kept the row resolving as a rule while
            # holding a constant's value — a fallback hiding inside one document.
            "kind": kind,
            "category": category or _DEFAULT_CATEGORY[decision],
            # Keep the indexed key in step with the field on every rewrite.
            "field_key": normalise_field(target_field),
            "captured_from": captured_from,
            "captured_by": captured_by,
            "client_id": client_id,
            "source_erp": source_erp,
            "project_id": project_id,
            "is_global": is_global,
            # Provenance: where this was most recently said, not where it applies.
            "target_object": target_object,
        }
        if action == UPDATE:
            # REFRESH deliberately leaves the stored date alone — the file is
            # restating itself, not saying something new.
            patch["effective_date"] = when
            # ...AND captured_at moves ONLY on a real new statement. This was the
            # revert bug: REFRESH re-stamped captured_at to `now` on every boot, and
            # effective_of() falls back to captured_at for an undated seed — so a
            # startup seed re-running out-ranked an analyst's edit made earlier the
            # same day, and the column reverted to the seeded value (D-U-N-S, 07-Aug).
            # A seed restating itself is NOT newer than a human correction; freezing
            # captured_at on REFRESH is what makes "the latest decision wins" hold
            # across the deploys that re-run the seeds. First insert still stamps it
            # (below); only re-runs leave it where it was.
            patch["captured_at"] = now
        if sheets is not None:
            patch["sheets"] = _merge_sheets(getattr(row, "sheets", None), sheets)
        if exclude_sheets is not None:
            patch["exclude_sheets"] = _merge_sheets(
                getattr(row, "exclude_sheets", None), exclude_sheets)
        if revive and getattr(row, "is_deleted", False):
            patch.update({"is_deleted": False, "deleted_at": None,
                          "deleted_by": None})
        # ARCHIVE THE STATEMENT THIS REPLACES, BEFORE IT IS OVERWRITTEN.
        #
        # One row per key means a newer statement lands ON the existing row, so
        # the older one does not get deleted — it gets mutated out of existence.
        # Archiving only the rows _delete_other_decisions removes would therefore
        # have caught almost nothing: in the ordinary two-statement case there IS
        # no second row, and the previous decision would vanish with no trace at
        # all. That is worse than the hard delete it was meant to soften.
        #
        # REFRESH is excluded on purpose: that is the same dated file restating
        # itself, not a new statement, so there is nothing being superseded.
        if action == UPDATE:
            try:
                await _archive(row, superseded_by=getattr(row, "id", None),
                               reason="superseded")
            except Exception:  # noqa: BLE001 — never fail a write over the trail
                log.exception("could not archive the previous decision for %r",
                              target_field)
        await row.set(patch)
        await _delete_other_decisions(row, target_field=target_field,
                                      client_id=client_id, source_erp=source_erp)
        return row

    row = LearnedMapping(
        kind=kind,
        category=category or _DEFAULT_CATEGORY[decision],
        target_field=str(target_field),
        field_key=normalise_field(target_field),
        target_object=target_object,
        client_id=client_id,
        source_erp=source_erp,
        project_id=project_id,
        is_global=is_global,
        captured_from=captured_from,
        captured_by=captured_by,
        effective_date=when,
        sheets=list(sheets or []),
        exclude_sheets=list(exclude_sheets or []),
        **shape,
    )
    await row.insert()
    await _delete_other_decisions(row, target_field=target_field,
                                  client_id=client_id, source_erp=source_erp)
    return row


async def _delete_other_decisions(keep, *, target_field: str, client_id: Any,
                                  source_erp: str | None) -> int:
    """Remove every OTHER decision row under this key. Returns how many went.

    "There should be just one row for each mapping or fixed value stored with
    date ... so that it does not even have other previous mappings or values to
    refer to even if it wants" — analyst, 05-Aug. The point is not tidiness. A
    row that still exists is a row some future code path can read, and every
    screen-versus-file bug in this tool has been two stored statements
    disagreeing. Deleting the losers is what makes the winner unambiguous
    structurally rather than by everybody remembering to sort by date.

    ARCHIVED, NOT DELETED — analyst, 05-Aug: "keep the older rules in archival
    currently, do not hard delete it but do not fall back to it, we will delete
    it after testing." The row is copied into ``ArchivedMappingDecision`` and
    removed from ``LearnedMapping``, so the guarantee is unchanged — the resolver
    queries LearnedMapping and finds exactly one row — while the trail survives
    until the analyst is satisfied.

    A separate COLLECTION rather than a flag, deliberately. A flag would leave
    the superseded statement sitting in the collection every reader already
    queries, which is the fallback this whole change exists to remove. Switching
    to a hard delete later is one branch here, not a rewrite.

    Only the four DECISION_KINDS are touched. Crosswalks are one row per source
    VALUE and must stay many-per-field; file signatures, ignore_source and
    reference standards are not statements about how a field maps.

    ONLY THE SAME INTERFACE. With sheet in the key, "one row per key" means one row
    per (client, source, field, sheet). A decision about sheet A must not delete
    the field-wide row or a sheet-B decision — those are different keys, and wiping
    them is precisely the collapse that made per-sheet values impossible. So the
    losers are filtered to the survivor's own sheet identity.
    """
    keep_sheets = _row_sheet_key(keep)
    rows = await find_rows_for_key(target_field=target_field, client_id=client_id,
                                   source_erp=source_erp, include_deleted=True)
    gone = 0
    for other in rows:
        if getattr(other, "id", None) == getattr(keep, "id", None):
            continue
        if getattr(other, "kind", None) not in DECISION_KINDS:
            continue
        if _row_sheet_key(other) != keep_sheets:
            # A different interface (or the field-wide base). Not this key.
            continue
        try:
            await _archive(other, superseded_by=getattr(keep, "id", None),
                           reason="superseded")
            await other.delete()
            gone += 1
        except Exception:  # noqa: BLE001 — a failed cleanup must not fail the write
            log.exception("could not archive superseded decision %s for %r",
                          getattr(other, "id", None), target_field)
    if gone:
        log.info("one-row store: archived %d superseded decision(s) for %r",
                 gone, target_field)
    return gone


async def _archive(row, *, superseded_by=None, reason: str = "superseded") -> None:
    """Copy a decision into the archive collection before it leaves the store.

    Raises on failure, so the caller does NOT delete a row it could not archive —
    losing a decision silently is the one outcome worse than keeping a duplicate.
    """
    from app.models.learned import ArchivedMappingDecision

    await ArchivedMappingDecision(
        kind=getattr(row, "kind", "") or "",
        category=getattr(row, "category", None),
        original_value=getattr(row, "original_value", None),
        resolved_value=getattr(row, "resolved_value", None),
        target_object=getattr(row, "target_object", None),
        target_field=getattr(row, "target_field", None),
        rule_type=getattr(row, "rule_type", None),
        rule_config=getattr(row, "rule_config", None),
        client_id=getattr(row, "client_id", None),
        is_global=bool(getattr(row, "is_global", False)),
        project_id=getattr(row, "project_id", None),
        captured_from=getattr(row, "captured_from", None),
        captured_by=getattr(row, "captured_by", None),
        captured_at=getattr(row, "captured_at", None),
        effective_date=getattr(row, "effective_date", None),
        source_erp=getattr(row, "source_erp", None),
        sheets=list(getattr(row, "sheets", None) or []),
        exclude_sheets=list(getattr(row, "exclude_sheets", None) or []),
        is_deleted=bool(getattr(row, "is_deleted", False)),
        original_id=getattr(row, "id", None),
        superseded_by=superseded_by,
        reason=reason,
    ).insert()


async def collapse_existing_decisions(dry_run: bool = False) -> dict:
    """Bring data written BEFORE the one-row rule into line. Idempotent.

    ``plan_write`` now keeps a single decision row per (client, source, field),
    but that only applies from the next write onwards. Everything stored under
    the old shape still carries up to four live rows per field — one per kind —
    and those are exactly the fallbacks this change exists to remove. A key
    nobody happens to edit again would keep them forever.

    So: for every key, keep the newest decision and delete the rest. Same rule as
    the writer, applied to history once.

    A RETIRED row is kept if it is the newest, because the tombstone is itself
    the analyst's latest statement about that field and removing it would revive
    what they retired.

    Losers are ARCHIVED, not deleted — see ``_archive``. ``dry_run=True`` reports
    what would move without touching anything.
    """
    from app.models.learned import LearnedMapping

    rows = await LearnedMapping.find(
        {"kind": {"$in": sorted(DECISION_KINDS)}},
        {"target_field": {"$ne": None}},
        include_deleted=True,
    ).to_list()

    by_key: dict[tuple, list] = {}
    for row in rows:
        # Sheet is part of the key now (05-Aug). Collapsing across sheets would
        # delete a per-interface decision as a "duplicate" of a field-wide one and
        # undo exactly the per-sheet control this dimension exists to give.
        key = (client_key(getattr(row, "client_id", None)),
               normalise_source(getattr(row, "source_erp", None)),
               normalise_field(getattr(row, "target_field", None)),
               _row_sheet_key(row))
        by_key.setdefault(key, []).append(row)

    kept = removed = 0
    collapsed_keys = 0
    for key, group in by_key.items():
        kept += 1
        if len(group) < 2:
            continue
        collapsed_keys += 1
        # Newest first — the same ordering the resolver uses, so the survivor is
        # the row that was already winning. This must not change any answer; it
        # removes the losers, it does not promote one.
        group = sorted(group, key=effective_of, reverse=True)
        for loser in group[1:]:
            removed += 1
            if not dry_run:
                try:
                    await _archive(loser, superseded_by=getattr(group[0], "id", None),
                                   reason="collapsed")
                    await loser.delete()
                except Exception:  # noqa: BLE001
                    log.exception("could not archive/collapse decision %s",
                                  getattr(loser, "id", None))
    log.info("one-row store: %d key(s), %d had duplicates, %d row(s) %s",
             kept, collapsed_keys, removed,
             "would be archived" if dry_run else "archived")
    return {"keys": kept, "keys_with_duplicates": collapsed_keys,
            "rows_removed": removed, "dry_run": dry_run}


async def record_reference_standard(*, target_object: str | None,
                                    target_field: str, original_value: str = "",
                                    resolved_value: str = "",
                                    rule_type: str | None = None,
                                    rule_config: dict | None = None,
                                    captured_from: str, captured_by: str | None = None,
                                    client_id: Any = None,
                                    source_erp: str | None = None,
                                    project_id: Any = None,
                                    effective_date: Optional[datetime] = None,
                                    category: str = "Reference Key Standard",
                                    revive: bool = False):
    """A master-key format standard.

    Not a statement about how a field MAPS — it records the shape a reference key
    takes — so it is not one of the four decisions and the resolver does not rank
    it against them. It lives here anyway so that the store stays the only place
    that writes a learning, and so it gets the same guarantee: an older statement
    never overwrites a newer one.
    """
    from app.models.learned import LearnedMapping

    now = datetime.utcnow()
    when = effective_date or now
    # Reference standards ARE scoped by object: "the Item number format" and "the
    # Supplier number format" are different facts about different keys, not two
    # statements about one field.
    rows = [r for r in await _reference_rows(target_field, client_id, source_erp,
                                            include_deleted=True)
            if _same_object(r, target_object)]
    action, row = plan_write(rows, kind="reference_standard", when=when,
                             revive=revive)
    if action == SKIP_RETIRED:
        return None
    if action == SKIP_OLDER:
        return row
    patch = {
        "original_value": original_value, "resolved_value": resolved_value,
        "rule_type": rule_type, "rule_config": dict(rule_config or {}),
        "captured_from": captured_from, "captured_by": captured_by,
        "captured_at": now, "effective_date": when, "client_id": client_id,
        "source_erp": source_erp, "project_id": project_id,
        "target_object": target_object, "category": category,
    }
    if action == UPDATE:
        if revive and getattr(row, "is_deleted", False):
            patch.update({"is_deleted": False, "deleted_at": None,
                          "deleted_by": None})
        await row.set(patch)
        return row
    row = LearnedMapping(kind="reference_standard", target_field=str(target_field),
                         is_global=False, sheets=[], exclude_sheets=[], **patch)
    await row.insert()
    return row


async def _reference_rows(target_field: str, client_id: Any,
                          source_erp: str | None,
                          include_deleted: bool = True) -> list:
    from app.models.learned import LearnedMapping
    rows = await LearnedMapping.find({"kind": "reference_standard"},
                                     include_deleted=include_deleted).to_list()
    want_field = normalise_field(target_field)
    return [r for r in rows
            if normalise_field(getattr(r, "target_field", None)) == want_field
            and client_key(getattr(r, "client_id", None)) == client_key(client_id)
            and normalise_source(getattr(r, "source_erp", None))
            == normalise_source(source_erp)]


def _same_object(row: Any, target_object: str | None) -> bool:
    return normalise_field(getattr(row, "target_object", None)) == \
        normalise_field(target_object)


async def record_learning(*, kind, category, original_value, resolved_value,
                        target_object=None, target_field=None, rule_type=None,
                        rule_config=None, project_id=None, captured_from, captured_by,
                        client_id=None, source_erp=None, sheets=None,
                        exclude_sheets=None, effective_date=None,
                        undated: bool = False, revive: bool = False):
    """Record a decision. A thin adapter onto the one dated store.

    Everything real happens in ``record_decision``: the key is
    ``(client, source system, target field)``, the newest statement wins, and an
    older one never overwrites a newer one. This function exists only so the
    long-standing callers keep their argument names.

    ``revive=False`` (the default) honours the tombstone: if the analyst deleted
    this decision, an automatic path — auto-capture after Generate, a startup
    seed, an approve/override — must NOT bring it back. Only an explicit user
    action passes ``revive=True``. Returns ``None`` when a retired row was left
    untouched.
    """
    if kind in DECISION_KINDS:
        return await record_decision(
            decision=KIND_TO_DECISION[kind],
            target_field=target_field,
            value=value_for(kind, original_value=original_value,
                                          resolved_value=resolved_value,
                                          rule_config=rule_config),
            client_id=client_id, source_erp=source_erp,
            effective_date=effective_date, undated=undated,
            captured_from=captured_from, captured_by=captured_by,
            rule_type=rule_type, rule_config=rule_config,
            target_object=target_object, project_id=project_id,
            sheets=sheets, exclude_sheets=exclude_sheets, category=category,
            original_value=original_value, resolved_value=resolved_value,
            revive=revive,
        )
    if kind == "reference_standard":
        return await record_reference_standard(
            target_object=target_object, target_field=target_field,
            original_value=original_value or "", resolved_value=resolved_value or "",
            rule_type=rule_type, rule_config=rule_config,
            captured_from=captured_from, captured_by=captured_by,
            client_id=client_id, source_erp=source_erp, project_id=project_id,
            effective_date=effective_date, category=category, revive=revive,
        )
    raise ValueError(f"not a learning this store records: {kind!r}")


async def record_source_exclusion(*, target_object: str | None, source_column: str,
                                  captured_from: str, captured_by: str | None = None,
                                  client_id: Any = None,
                                  source_erp: str | None = None,
                                  effective_date: Optional[datetime] = None,
                                  category: str = "Do Not Map Source",
                                  revive: bool = False):
    """"Never map anything FROM this source column."

    Not one of the four decisions — it is about a source column, not a target
    field, so the resolver does not rank it against them. It lives here so the
    store stays the only place a learning is written.
    """
    from app.models.learned import LearnedMapping

    now = datetime.utcnow()
    rows = await LearnedMapping.find({"kind": "ignore_source"},
                                     include_deleted=True).to_list()
    want = normalise_field(source_column)
    rows = [r for r in rows
            if normalise_field(getattr(r, "original_value", None)) == want
            and _same_object(r, target_object)]
    if rows:
        row = rows[0]
        if getattr(row, "is_deleted", False) and not revive:
            return None
        await row.set({"client_id": client_id, "source_erp": source_erp,
                       "captured_from": captured_from, "captured_by": captured_by,
                       "captured_at": now})
        return row
    row = LearnedMapping(
        kind="ignore_source", category=category,
        original_value=source_column, resolved_value="",
        target_object=target_object, target_field=None,
        client_id=client_id, is_global=False, source_erp=source_erp,
        captured_from=captured_from, captured_by=captured_by,
        effective_date=effective_date,
    )
    await row.insert()
    return row
