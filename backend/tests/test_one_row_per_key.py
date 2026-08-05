"""One row per key. Not the newest of several — the only one.

Analyst, 05-Aug:

    "there should be just one row for each mapping or fixed value stored with
     date, so that it does not even have other previous mappings or values to
     refer to even if it wants ... and it will also reduce number of records for
     each column or field in the database"

WHY THIS IS AN ARCHITECTURAL CHANGE AND NOT A TIDY-UP

Until today the store held one row per (client, source, field, KIND). A column
mapping, a fixed value, a suppression and a rule for the same field were four
live rows, and the resolver picked the latest at READ time.

That is a fallback, and a fallback is something a code path can reach for when
the newest statement does not suit it. Every screen-versus-file bug in this tool
has been two stored statements disagreeing, and Receipt Routing was the bill: a
VALUE_MAP rule dated 03-Aug and a fixed value dated 04-Aug both existed,
generation consulted them in code order rather than date order, and the file
shipped DIRECT from a 13-Jul document — a third statement again.

Sorting by date correctly is a thing every reader has to remember. Having only
one row is a thing no reader can get wrong.

WHAT IS DELETED, AND WHAT THIS COSTS

Superseded decisions are HARD DELETED, on the analyst's explicit instruction —
not tombstoned, because a tombstoned row is still a row. The cost is real and
should not be discovered later: the provenance of a superseded decision is gone,
so "why did this field once say DIRECT" stops being answerable from the database.
The forensics that found the Receipt Routing chain would not be possible after
this change.

WHAT IS NOT TOUCHED

Only the four DECISION_KINDS. ``crosswalk`` is one row per source VALUE and must
stay many-per-field — collapsing it would destroy every value mapping the client
has. ``file_signature``, ``ignore_source`` and ``reference_standard`` are not
statements about how a field maps.
"""
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("mongomock_motor",
                    reason="mongomock_motor is not installed — the one-row "
                           "guarantee cannot be exercised against a real store")

from beanie import init_beanie                                   # noqa: E402
from mongomock_motor import AsyncMongoMockClient                 # noqa: E402

from app.models.learned import (                                 # noqa: E402
    ArchivedMappingDecision, LearnedMapping,
)
from beanie import PydanticObjectId                              # noqa: E402

from app.services import mapping_store                           # noqa: E402

FIELD = "Receipt Routing"
# A real ObjectId: client_id is typed on the document, and the key this store is
# built around is (client, source, field) — so the test has to use the same shape
# the application does rather than a convenient string.
CLIENT = PydanticObjectId()
SOURCE = "netsuite"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


async def _fresh():
    await init_beanie(database=AsyncMongoMockClient()["onerow"],
                      document_models=[LearnedMapping, ArchivedMappingDecision])


async def _write(kind, value, when, captured_from="grid", **kw):
    return await mapping_store.record_learning(
        kind=kind, category=None, original_value=value, resolved_value=value,
        target_field=FIELD, client_id=CLIENT, source_erp=SOURCE,
        effective_date=when, captured_from=captured_from,
        captured_by="subrato@nextpower.com", **kw)


async def _rows(include_deleted=True):
    return await mapping_store.find_rows_for_key(
        target_field=FIELD, client_id=CLIENT, source_erp=SOURCE,
        include_deleted=include_deleted)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_four_statements_about_one_field_leave_one_row():
    """The whole point, end to end. Four different kinds, four different dates,
    written in order — and one row at the end holding the newest."""
    async def go():
        await _fresh()
        await _write("column_mapping", "Vendor Routing", datetime(2026, 7, 1))
        await _write("rule", "Vendor Routing", datetime(2026, 8, 3, 18, 32))
        await _write("suppress_field", None, datetime(2026, 8, 3, 19, 0))
        await _write("example_default", "3", datetime(2026, 8, 4, 10, 57))
        return await _rows()

    rows = _run(go())
    check("exactly one row survives", len(rows) == 1, f"got {len(rows)}: "
          f"{[(r.kind, r.effective_date) for r in rows]}")
    check("it is the newest statement", rows[0].kind == "example_default",
          f"got {rows[0].kind}")
    check("carrying its own date",
          rows[0].effective_date == datetime(2026, 8, 4, 10, 57),
          f"got {rows[0].effective_date}")
    check("and the newest value", mapping_store.value_of(rows[0]) == "3",
          f"got {mapping_store.value_of(rows[0])!r}")


def test_the_receipt_routing_chain_can_no_longer_exist():
    """The exact live case: a rule on 03-Aug, a fixed value on 04-Aug. Before
    today both were stored and generation could reach either."""
    async def go():
        await _fresh()
        await _write("rule", "Vendor Routing", datetime(2026, 8, 3, 18, 32))
        await _write("example_default", "3", datetime(2026, 8, 4, 10, 57))
        return await _rows()

    rows = _run(go())
    check("the rule is gone, not merely outranked", len(rows) == 1,
          f"got {[(r.kind, mapping_store.value_of(r)) for r in rows]}")
    check("only the fixed value remains", rows[0].kind == "example_default")


def test_an_older_statement_never_replaces_the_row():
    """Deleting the losers must not become 'last write wins'. An older statement
    arriving late is still refused, and it must not delete the newer row on its
    way out."""
    async def go():
        await _fresh()
        await _write("example_default", "3", datetime(2026, 8, 4, 10, 57))
        await _write("rule", "Vendor Routing", datetime(2026, 7, 1))
        return await _rows()

    rows = _run(go())
    check("still one row", len(rows) == 1, f"got {len(rows)}")
    check("and it is still the newer one", rows[0].kind == "example_default")
    check("with its value intact", mapping_store.value_of(rows[0]) == "3")


def test_a_crosswalk_is_never_collapsed():
    """One row per source VALUE, by design. Collapsing these would destroy every
    value mapping the client has — the single most dangerous thing this change
    could have done."""
    async def go():
        await _fresh()
        for src, dst, day in (("Direct Delivery", "3", 1),
                              ("Inspection Required", "2", 2),
                              ("Standard Receipt", "1", 3)):
            await LearnedMapping(
                kind="crosswalk", category="value", target_field=FIELD,
                original_value=src, resolved_value=dst, client_id=CLIENT,
                source_erp=SOURCE, effective_date=datetime(2026, 8, day),
                captured_from="grid", captured_by="x").insert()
        # ...and then a decision on the same field, which must not touch them.
        await _write("example_default", "3", datetime(2026, 8, 4, 10, 57))
        return await LearnedMapping.find({"kind": "crosswalk"}).to_list()

    rows = _run(go())
    check("all three value mappings survive", len(rows) == 3,
          f"got {[(r.original_value, r.resolved_value) for r in rows]}")


def test_the_loser_leaves_the_store_entirely_rather_than_being_flagged():
    """MOVED 05-Aug: was test_the_deletion_is_a_hard_delete_not_a_tombstone.

    The instruction changed from "delete outright" to "keep the older rules in
    archival currently, do not hard delete it but do not fall back to it, we will
    delete it after testing".

    The GUARANTEE is unchanged and is what this still asserts: the superseded row
    is not in LearnedMapping at all — not tombstoned, not flagged, gone from the
    collection every reader queries. Where it went is the next test's business.
    Asserted with include_deleted=True, which is what would still see a tombstone.
    """
    async def go():
        await _fresh()
        await _write("rule", "Vendor Routing", datetime(2026, 8, 3))
        await _write("example_default", "3", datetime(2026, 8, 4))
        return await _rows(include_deleted=True)

    rows = _run(go())
    check("nothing is left behind, not even retired", len(rows) == 1,
          f"got {[(r.kind, getattr(r, 'is_deleted', None)) for r in rows]}")


def test_rewriting_the_same_decision_does_not_multiply_rows():
    """The reduce-the-record-count half of the ask. Ten saves of the same field
    is one row, not ten."""
    async def go():
        await _fresh()
        for i in range(1, 11):
            await _write("example_default", str(i), datetime(2026, 8, 4, 10, i))
        return await _rows()

    rows = _run(go())
    check("one row after ten saves", len(rows) == 1, f"got {len(rows)}")
    check("holding the last one", mapping_store.value_of(rows[0]) == "10",
          f"got {mapping_store.value_of(rows[0])!r}")


def test_a_retired_decision_is_still_not_revived_by_an_automatic_path():
    """The tombstone rule predates this change and still holds: an analyst who
    deleted a decision must not have it re-created by a seed or an auto-capture.
    Only an explicit user action passes revive=True."""
    async def go():
        await _fresh()
        row = await _write("example_default", "3", datetime(2026, 8, 4))
        await row.set({"is_deleted": True})
        await _write("example_default", "9", datetime(2026, 8, 5),
                     captured_from="auto-capture")
        return await _rows(include_deleted=True)

    rows = _run(go())
    check("still one row", len(rows) == 1, f"got {len(rows)}")
    check("still retired", getattr(rows[0], "is_deleted", False) is True)
    check("and not overwritten", mapping_store.value_of(rows[0]) == "3",
          f"got {mapping_store.value_of(rows[0])!r}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall one-row-per-key checks passed")


# ── History written before the rule existed ──────────────────────────────────

def test_collapsing_existing_data_keeps_the_winner_and_removes_the_rest():
    """plan_write only governs writes from now on. Anything stored under the old
    shape keeps up to four rows per field until somebody edits it, and a key
    nobody touches again would keep them forever."""
    async def go():
        await _fresh()
        for kind, val, day in (("column_mapping", "Vendor Routing", 1),
                               ("rule", "Vendor Routing", 3),
                               ("example_default", "3", 4)):
            await LearnedMapping(
                kind=kind, category="c", target_field=FIELD,
                original_value=val, resolved_value=val, client_id=CLIENT,
                source_erp=SOURCE, effective_date=datetime(2026, 8, day),
                captured_from="seed", captured_by="x").insert()
        before = len(await _rows())
        report = await mapping_store.collapse_existing_decisions()
        return before, report, await _rows()

    before, report, after = _run(go())
    check("three rows existed", before == 3, f"got {before}")
    check("two were removed", report["rows_removed"] == 2, f"got {report}")
    check("one remains", len(after) == 1, f"got {len(after)}")
    check("and it is the newest", after[0].kind == "example_default",
          f"got {after[0].kind}")


def test_the_collapse_is_idempotent():
    """It has to be safe to run twice — the plan says so of every migration
    here, and a hard delete is not something to run nervously."""
    async def go():
        await _fresh()
        for kind, day in (("column_mapping", 1), ("rule", 3)):
            await LearnedMapping(
                kind=kind, category="c", target_field=FIELD, original_value="x",
                resolved_value="x", client_id=CLIENT, source_erp=SOURCE,
                effective_date=datetime(2026, 8, day), captured_from="seed",
                captured_by="x").insert()
        first = await mapping_store.collapse_existing_decisions()
        second = await mapping_store.collapse_existing_decisions()
        return first, second, await _rows()

    first, second, rows = _run(go())
    check("the first pass removed one", first["rows_removed"] == 1, f"got {first}")
    check("the second removed nothing", second["rows_removed"] == 0, f"got {second}")
    check("one row remains", len(rows) == 1)


def test_a_dry_run_changes_nothing():
    """This is a hard delete against a real client's history. It must be possible
    to see what would go before it goes."""
    async def go():
        await _fresh()
        for kind, day in (("column_mapping", 1), ("rule", 3)):
            await LearnedMapping(
                kind=kind, category="c", target_field=FIELD, original_value="x",
                resolved_value="x", client_id=CLIENT, source_erp=SOURCE,
                effective_date=datetime(2026, 8, day), captured_from="seed",
                captured_by="x").insert()
        report = await mapping_store.collapse_existing_decisions(dry_run=True)
        return report, await _rows()

    report, rows = _run(go())
    check("it reports what would go", report["rows_removed"] == 1, f"got {report}")
    check("and says it was a dry run", report["dry_run"] is True)
    check("but nothing was deleted", len(rows) == 2, f"got {len(rows)}")


def test_the_collapse_leaves_a_retired_row_alone_when_it_is_newest():
    """A tombstone is the analyst's latest statement about that field. Deleting
    it would revive what they retired."""
    async def go():
        await _fresh()
        await LearnedMapping(
            kind="column_mapping", category="c", target_field=FIELD,
            original_value="x", resolved_value="x", client_id=CLIENT,
            source_erp=SOURCE, effective_date=datetime(2026, 8, 1),
            captured_from="seed", captured_by="x").insert()
        await LearnedMapping(
            kind="example_default", category="c", target_field=FIELD,
            original_value="3", resolved_value="3", client_id=CLIENT,
            source_erp=SOURCE, effective_date=datetime(2026, 8, 4),
            captured_from="grid", captured_by="x", is_deleted=True).insert()
        await mapping_store.collapse_existing_decisions()
        return await _rows(include_deleted=True)

    rows = _run(go())
    check("one row remains", len(rows) == 1, f"got {len(rows)}")
    check("and the retirement survives", getattr(rows[0], "is_deleted", False) is True)


def test_crosswalks_survive_the_collapse_too():
    async def go():
        await _fresh()
        for src, day in (("Direct Delivery", 1), ("Standard Receipt", 2)):
            await LearnedMapping(
                kind="crosswalk", category="value", target_field=FIELD,
                original_value=src, resolved_value="3", client_id=CLIENT,
                source_erp=SOURCE, effective_date=datetime(2026, 8, day),
                captured_from="grid", captured_by="x").insert()
        await mapping_store.collapse_existing_decisions()
        return await LearnedMapping.find({"kind": "crosswalk"}).to_list()

    check("both value mappings survive", len(_run(go())) == 2)


# ── The archive: kept, and unreachable ───────────────────────────────────────
#
# Analyst, 05-Aug: "keep the older rules in archival currently, do not hard
# delete it but do not fall back to it, we will delete it after testing."
#
# So there are two things to prove, and they pull in opposite directions: the
# superseded statement still EXISTS, and nothing that decides an output can SEE
# it. A flag on the row would have satisfied the first and failed the second.

def test_the_superseded_statement_is_kept_with_its_provenance():
    async def go():
        await _fresh()
        await _write("rule", "Vendor Routing", datetime(2026, 8, 3, 18, 32))
        keep = await _write("example_default", "3", datetime(2026, 8, 4, 10, 57))
        return keep, await ArchivedMappingDecision.find_all().to_list()

    keep, arch = _run(go())
    check("one row was archived", len(arch) == 1, f"got {len(arch)}")
    a = arch[0]
    check("it is the rule", a.kind == "rule", f"got {a.kind}")
    check("with its own date intact",
          a.effective_date == datetime(2026, 8, 3, 18, 32), f"got {a.effective_date}")
    check("its original id is traceable", a.original_id is not None)
    check("it records what replaced it", a.superseded_by == keep.id,
          f"got {a.superseded_by}")
    check("and why it left", a.reason == "superseded", f"got {a.reason}")


def test_the_archive_is_a_separate_collection_not_a_flag():
    """The structural half. If archiving were a flag, the superseded row would
    still be sitting in the collection every reader queries — which is the
    fallback this change exists to remove."""
    check("it has its own collection",
          ArchivedMappingDecision.Settings.name == "archived_mapping_decisions",
          f"got {ArchivedMappingDecision.Settings.name}")
    check("and it is not LearnedMapping",
          ArchivedMappingDecision is not LearnedMapping)


def test_the_resolver_cannot_see_an_archived_decision():
    """The guarantee, stated as the thing that actually matters: after archiving,
    the store answers with the newest statement and has nothing else to offer."""
    async def go():
        await _fresh()
        await _write("rule", "Vendor Routing", datetime(2026, 8, 3))
        await _write("example_default", "3", datetime(2026, 8, 4))
        rows = await _rows(include_deleted=True)
        entries = mapping_store.entries_of(rows)
        return rows, mapping_store.resolve(entries, target_field=FIELD,
                                           client_id=CLIENT, source_erp=SOURCE)

    rows, winner = _run(go())
    check("the store holds one row", len(rows) == 1, f"got {len(rows)}")
    check("the resolver picks it", winner is not None)
    check("and it is the fixed value", winner.decision == mapping_store.DEFAULT_VALUE,
          f"got {winner.decision}")
    check("with the newest value", winner.value == "3", f"got {winner.value!r}")


def test_nothing_in_the_backend_reads_the_archive():
    """The sweep that keeps it a holding pen. If a reader ever appears, the
    fallback is back and the guarantee is gone — so this fails the moment
    ArchivedMappingDecision is referenced anywhere but the model, the store that
    writes it, the database registration and this test."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    allowed = {"models/learned.py", "services/mapping_store.py", "database.py"}
    offenders = []
    for path in root.rglob("*.py"):
        rel = str(path.relative_to(root)).replace("\\", "/")
        if rel in allowed:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # AST, not text. A docstring that MENTIONS the class — settings.py has
        # one, explaining that superseded rows are archived — is documentation,
        # not a reader. Counting the mention is the same mistake three tests in
        # one session made when a comment quoted the expression being counted.
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "ArchivedMappingDecision":
                offenders.append(rel); break
            if isinstance(node, ast.Attribute) and node.attr == "ArchivedMappingDecision":
                offenders.append(rel); break
            if isinstance(node, ast.ImportFrom):
                if any(a.name == "ArchivedMappingDecision" for a in node.names):
                    offenders.append(rel); break
    check("no reader anywhere in app/", not offenders, f"found in {offenders}")


def test_the_collapse_migration_archives_too_and_says_why():
    async def go():
        await _fresh()
        for kind, day in (("column_mapping", 1), ("rule", 3)):
            await LearnedMapping(
                kind=kind, category="c", target_field=FIELD, original_value="x",
                resolved_value="x", client_id=CLIENT, source_erp=SOURCE,
                effective_date=datetime(2026, 8, day), captured_from="seed",
                captured_by="x").insert()
        await mapping_store.collapse_existing_decisions()
        return await ArchivedMappingDecision.find_all().to_list()

    arch = _run(go())
    check("the loser was archived", len(arch) == 1, f"got {len(arch)}")
    check("marked as a collapse, not a supersede", arch[0].reason == "collapsed",
          f"got {arch[0].reason}")


def test_a_dry_run_archives_nothing():
    async def go():
        await _fresh()
        for kind, day in (("column_mapping", 1), ("rule", 3)):
            await LearnedMapping(
                kind=kind, category="c", target_field=FIELD, original_value="x",
                resolved_value="x", client_id=CLIENT, source_erp=SOURCE,
                effective_date=datetime(2026, 8, day), captured_from="seed",
                captured_by="x").insert()
        await mapping_store.collapse_existing_decisions(dry_run=True)
        return await ArchivedMappingDecision.find_all().to_list(), await _rows()

    arch, rows = _run(go())
    check("nothing archived", len(arch) == 0, f"got {len(arch)}")
    check("and nothing removed", len(rows) == 2, f"got {len(rows)}")


# ── How anyone actually runs the migration ───────────────────────────────────

def test_the_migration_is_reachable_and_defaults_to_a_dry_run():
    """It has to be runnable by the only machine that can reach Mongo — the
    running service — because the deploy box has no Python environment. And
    running it by accident must report rather than move: dry_run defaults True,
    so moving rows takes an explicit ?dry_run=false."""
    import ast
    import inspect
    from app.routers import settings as settings_router

    fn = settings_router.collapse_decisions
    params = inspect.signature(fn).parameters
    check("it takes dry_run", "dry_run" in params)
    check("and it defaults to True", params["dry_run"].default is True,
          f"got {params['dry_run'].default!r}")

    src = ast.parse(inspect.getsource(settings_router))
    routes = [n for n in ast.walk(src)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "collapse_decisions"]
    check("it is a route", routes and routes[0].decorator_list)


def test_the_migration_endpoint_is_administrators_only():
    """A migration is not something a Normal user runs. The settings router is
    mounted under ADMIN — assert that, rather than trusting it."""
    import ast
    from pathlib import Path

    main = Path(__file__).resolve().parent.parent / "app" / "main.py"
    tree = ast.parse(main.read_text(encoding="utf-8"))
    section = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id.lstrip("_") == "mount"):
            kw = {k.arg: k.value for k in node.keywords}
            name = kw.get("name")
            if isinstance(name, ast.Constant) and name.value == "settings":
                sec = kw.get("section")
                section = sec.id if isinstance(sec, ast.Name) else None
    check("settings is mounted under ADMIN", section == "ADMIN", f"got {section}")
