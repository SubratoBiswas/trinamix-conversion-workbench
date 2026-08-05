"""The one dated store — one writer, and a backfill that can be run twice.

Two claims are tested here, and they are the two that make the store safe to
put behind everything else:

1. **An older statement never overwrites a newer one.** Every replay depends on
   it: a startup seed re-running on every boot, a reseed endpoint, the backfill.
   It is what stops a redeploy from inverting the analyst's own corrections.

2. **Every path that records a mapping decision goes through one function.**
   The six-call-sites problem — one fan-out fixed three times in three different
   places because there was no single place to fix it — is what this change
   exists to end, so "there is only one writer" is asserted rather than assumed.
"""
import ast
import os
import re
from datetime import datetime

import pytest

from app.services import mapping_store as store
from app.services.mapping_store import (
    INSERT, SKIP_OLDER, SKIP_RETIRED, UPDATE, plan_write,
)

HERE = os.path.dirname(__file__)
APP = os.path.join(HERE, "..", "app")


class Row:
    def __init__(self, kind="column_mapping", effective_date=None,
                 captured_at=None, is_deleted=False, tag=None):
        self.kind = kind
        self.effective_date = effective_date
        self.captured_at = captured_at
        self.is_deleted = is_deleted
        self.tag = tag


def d(day, month=7, year=2026):
    return datetime(year, month, day)


# ── An older statement never overwrites a newer one ──────────────────────────

def test_nothing_stored_yet_means_insert():
    assert plan_write([], kind="column_mapping", when=d(31))[0] == INSERT


def test_a_newer_statement_updates_the_row():
    stored = Row(effective_date=d(13))
    action, row = plan_write([stored], kind="column_mapping", when=d(31))
    assert (action, row) == (UPDATE, stored)


def test_an_older_statement_is_refused():
    """A seed re-running cannot walk the analyst's correction backwards."""
    stored = Row(effective_date=d(31))
    action, row = plan_write([stored], kind="column_mapping", when=d(13))
    assert (action, row) == (SKIP_OLDER, stored)


def test_the_same_statement_twice_is_not_a_second_write():
    """"It must be possible to run it twice." The second run is a no-op update
    of identical content, never an insert, so nothing duplicates."""
    stored = Row(effective_date=d(31))
    first = plan_write([stored], kind="column_mapping", when=d(31))
    second = plan_write([stored], kind="column_mapping", when=d(31))
    assert first == second
    assert first[0] == UPDATE


def test_a_retired_decision_is_not_resurrected_by_an_automatic_path():
    stored = Row(effective_date=d(13), is_deleted=True)
    assert plan_write([stored], kind="column_mapping",
                      when=d(31))[0] == SKIP_RETIRED


def test_an_explicit_user_action_may_revive_it():
    stored = Row(effective_date=d(13), is_deleted=True)
    assert plan_write([stored], kind="column_mapping", when=d(31),
                      revive=True)[0] == UPDATE


def test_a_retired_row_is_checked_before_the_date():
    """Otherwise an old replay reports "already newer" and the analyst never
    learns their deletion is what stopped it."""
    stored = Row(effective_date=d(31), is_deleted=True)
    assert plan_write([stored], kind="column_mapping",
                      when=d(13))[0] == SKIP_RETIRED


def test_one_field_holds_exactly_one_decision_whatever_its_kind():
    """MOVED 05-Aug. Was test_the_four_decisions_are_separate_rows_that_compete_by_date.

    It asserted that a column mapping, a default, a suppression and a rule were
    FOUR live rows for one field, competing by date at read time. That is a
    fallback: four stored answers, and whichever code path asks first decides.

    Receipt Routing is what it cost. A rule dated 03-Aug and a fixed value dated
    04-Aug both existed; generation consulted them in code order rather than date
    order, and the file shipped a third value from a 13-Jul document.

    Analyst, 05-Aug: "there should be just one row for each mapping or fixed value
    stored with date ... so that it does not even have other previous mappings or
    values to refer to even if it wants."

    So the four kinds now compete for ONE row. A default written today lands on
    the column mapping's row and replaces it; an older statement of any kind is
    still refused. The assertion is not relaxed — it is the same date rule, with
    the kind no longer able to carve out a second row to hide in.
    """
    column = Row(kind="column_mapping", effective_date=d(31), tag="column")
    action, row = plan_write([column], kind="example_default", when=d(2, month=8))
    assert (action, row) == (UPDATE, column), "a newer default must REPLACE the row"
    assert plan_write([column], kind="example_default", when=d(13))[0] == SKIP_OLDER
    assert plan_write([column], kind="column_mapping", when=d(13))[0] == SKIP_OLDER


def test_a_kind_that_is_not_a_mapping_decision_never_collapses_the_row():
    """Crosswalks are one row per source VALUE and must stay many-per-field;
    file signatures and reference standards are not statements about how a field
    maps. Only the four DECISION_KINDS compete for the single row — find_rows_for_key
    already queries only those, and plan_write must agree."""
    crosswalk = Row(kind="crosswalk", effective_date=d(31), tag="xwalk")
    assert plan_write([crosswalk], kind="example_default", when=d(13))[0] == INSERT


def test_the_edit_lands_on_the_row_the_resolver_would_have_picked():
    old = Row(effective_date=d(13), tag="old")
    new = Row(effective_date=d(31), tag="new")
    _, row = plan_write([old, new], kind="column_mapping", when=d(2, month=8))
    assert row.tag == "new"


def test_an_undated_row_is_updated_rather_than_duplicated():
    """Undated counts as older, so anything dated supersedes it — in place."""
    stored = Row(effective_date=None, captured_at=None)
    action, row = plan_write([stored], kind="column_mapping", when=d(31))
    assert (action, row) == (UPDATE, stored)


def test_captured_at_is_what_an_undated_row_is_compared_on():
    stored = Row(effective_date=None, captured_at=d(31))
    assert plan_write([stored], kind="column_mapping",
                      when=d(13))[0] == SKIP_OLDER


# ── The shape written for each decision ──────────────────────────────────────

def test_a_source_column_decision_stores_the_column_and_resolves_to_the_field():
    shape = store._row_shape(store.SOURCE_COLUMN, "Legal Name", "Supplier Name",
                             None, {})
    assert shape["original_value"] == "Legal Name"
    assert shape["resolved_value"] == "Supplier Name"


def test_a_default_decision_stores_its_constant_where_both_readers_look():
    """The constant is read off rule_config by one layer and off resolved_value
    by another. Writing one and not the other is how the screen and the file
    came to disagree, so the writer fills both."""
    shape = store._row_shape(store.DEFAULT_VALUE, "900001", "Party Number",
                             None, {})
    assert shape["resolved_value"] == "900001"
    assert shape["rule_config"]["default_value"] == "900001"


def test_a_suppression_stores_nothing_to_write():
    shape = store._row_shape(store.SUPPRESS, None, "Delivery Channel", None, {})
    assert shape["resolved_value"] == ""
    assert shape["rule_type"] == "suppress"


def test_a_rule_decision_keeps_its_type_and_config():
    shape = store._row_shape(store.RULE, "Country", "Country Code", "VALUE_MAP",
                             {"map": {"US": "USA"}})
    assert shape["rule_type"] == "VALUE_MAP"
    assert shape["rule_config"] == {"map": {"US": "USA"}}


def test_sheets_are_unioned_never_replaced():
    """Setting the same default on a second sheet used to overwrite the first
    one. Both saves said approved; the file carried one."""
    assert store._merge_sheets(["A"], ["B"]) == ["A", "B"]
    assert store._merge_sheets(["A"], ["a"]) == ["A"]
    assert store._merge_sheets([], ["A"]) == ["A"]
    assert store._merge_sheets(["A"], []) == ["A"]


def test_a_decision_must_be_about_a_field():
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(store.record_decision(decision=store.SOURCE_COLUMN,
                                          target_field="", captured_from="test"))


def test_something_that_is_not_a_decision_is_refused():
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(store.record_decision(decision="crosswalk",
                                          target_field="Supplier Name",
                                          captured_from="test"))


# ── The key has no object in it ──────────────────────────────────────────────

def test_the_lookup_does_not_query_on_the_object():
    """The whole point. A decision captured under "Supplier" and one captured
    under "Supplier Import" are one client saying two things about one field,
    and it was possible for those two spellings to hide from each other."""
    src = open(os.path.join(APP, "services", "mapping_store.py"),
               encoding="utf-8").read()
    body = src.split("async def find_rows_for_key(")[1].split("\nasync def ")[0]
    assert "target_object" not in body.split('"""')[2]


def test_the_resolver_never_narrows_by_object():
    src = open(os.path.join(APP, "services", "mapping_store.py"),
               encoding="utf-8").read()
    body = src.split("def applies(")[1].split("\ndef _order")[0]
    code = body.split('"""')[2]
    assert "target_object" not in code


# ── One writer ───────────────────────────────────────────────────────────────

def _python_files():
    for root, _dirs, files in os.walk(APP):
        if "__pycache__" in root:
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


# The store itself writes rows; so do the paths that RETIRE a decision rather
# than record one, and the paths for kinds that are not mapping decisions at all.
_ALLOWED_TO_CONSTRUCT = {
    os.path.join("services", "mapping_store.py"),          # the writer itself
    os.path.join("services", "value_mapping_service.py"),  # crosswalk: one row per VALUE
    os.path.join("routers", "datasets.py"),                # file_signature
    os.path.join("routers", "learned.py"),                 # the Learning Centre's own CRUD
}


def _constructs_learned_mapping(path):
    src = open(path, encoding="utf-8").read()
    if "LearnedMapping(" not in src:
        return []
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "LearnedMapping":
            hits.append(node.lineno)
    return hits


def test_only_the_store_constructs_a_mapping_decision():
    """A decision written any other way is one the resolver cannot see and the
    analyst cannot retire."""
    offenders = {}
    for path in _python_files():
        rel = os.path.relpath(path, APP)
        if rel in _ALLOWED_TO_CONSTRUCT:
            continue
        lines = _constructs_learned_mapping(path)
        if lines:
            offenders[rel] = lines
    assert not offenders, (
        "these write a learning without going through mapping_store."
        f"record_decision: {offenders}")


def test_every_writer_that_is_allowed_writes_a_kind_that_is_not_a_decision():
    """The exceptions are exceptions because they are not statements about how
    a target field maps — not because they were awkward to convert."""
    for rel in sorted(_ALLOWED_TO_CONSTRUCT):
        if rel.endswith("mapping_store.py"):
            continue
        src = open(os.path.join(APP, rel), encoding="utf-8").read()
        kinds = set(re.findall(r'kind\s*=\s*"([a-z_]+)"', src))
        kinds |= set(re.findall(r'"kind":\s*"([a-z_]+)"', src))
        if rel.endswith("learned.py"):
            # The Learning Centre creates whatever the analyst typed; it is the
            # one place a person writes a row by hand, and it is dated on write.
            assert "effective_date" in src
            continue
        assert kinds, f"{rel} constructs a learning under no visible kind"
        assert not (kinds & store.DECISION_KINDS), (
            f"{rel} writes mapping decisions {sorted(kinds & store.DECISION_KINDS)} "
            "outside the store")


def _reaches_the_store(src: str) -> bool:
    """Writes a decision by calling the store, under either of its two names.

    ``record_decision`` is the writer; ``record_learning`` is the adapter that
    takes the old ``kind=`` vocabulary and calls it. Both live in the store and
    both go through the same guard, so either is the seam being asserted.
    """
    return ("from app.services.mapping_store import record_learning" in src
            or "from app.services import mapping_store" in src
            or "mapping_store.record_decision(" in src
            or "record_decision(" in src)


def test_the_seeders_record_decisions_through_the_store():
    src = open(os.path.join(APP, "services", "catalog_seed_service.py"),
               encoding="utf-8").read()
    assert _reaches_the_store(src)
    assert "LearnedMapping(" not in src


def test_the_interactive_capture_paths_record_through_the_store():
    """Workbook, gold, learning capture, steer, grid edit, custom rule."""
    for rel in ("services/learning_service.py",
                "services/steering_service.py",
                "services/example_learning_service.py",
                "services/mapping_import_service.py",
                "services/mapping_ingest_service.py",
                "routers/manual_map.py"):
        src = open(os.path.join(APP, *rel.split("/")), encoding="utf-8").read()
        assert _reaches_the_store(src), f"{rel} does not write through the store"


# ── The backfill ─────────────────────────────────────────────────────────────

def _backfill_src():
    return open(os.path.join(APP, "services", "mapping_store_backfill.py"),
                encoding="utf-8").read()


def test_the_backfill_only_fills_a_date_that_is_missing():
    """"effective_date must never move on a read. A startup seed that finds
    nothing to do must not re-stamp anything.\""""
    body = _backfill_src().split("async def date_the_library(")[1] \
                          .split("\nasync def ")[0]
    assert 'if getattr(row, "effective_date", None) is not None:' in body
    assert 'await row.set({"effective_date": captured_at})' in body
    # captured_at is read, never written.
    assert '"captured_at":' not in body


def test_the_backfill_carries_the_existing_date_rather_than_today():
    body = _backfill_src().split("async def carry_over_conversion_decisions(")[1] \
                          .split("\nasync def ")[0]
    assert "effective_date=(entry.effective_date" in body
    assert "datetime.utcnow()" not in body


def test_the_backfill_sees_tombstoned_rows_when_dating_them():
    """LearnedMapping.find hides retired rows, so a retired decision would come
    back undated — and undated counts as older, which silently reorders it if
    the analyst ever restores it."""
    body = _backfill_src().split("async def date_the_library(")[1] \
                          .split("\nasync def ")[0]
    assert "include_deleted=True" in body


def test_the_backfill_reports_failures_rather_than_swallowing_them():
    body = _backfill_src().split("async def carry_over_grid_decisions(")[1] \
                          .split("\nasync def ")[0]
    assert 'totals["failed"] += 1' in body
    assert "errors" in body
    assert "pass\n" not in body


def test_only_a_persons_decision_is_carried_over():
    """A row stamped learning-engine is a COPY of a decision. Carrying it back
    in would let the store's own output re-enter the store and outrank the
    thing it was copied from."""
    assert store.entry_from_mapping(
        type("M", (), {"approved_by": store.ENGINE, "status": "approved",
                       "source_column": "X", "default_value": None,
                       "approved_at": d(31), "suggested_transformation": None})(),
        target_field="Supplier Name") is None
