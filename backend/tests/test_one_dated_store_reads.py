"""The one dated store — generation reads it, and the rows are a view of it.

The failure this whole change exists to end is two layers disagreeing: the
screen said one thing, the file said another, and neither could be shown to be
wrong because the library had been COPIED into the per-conversion rows and the
copy was what generation read.

So the claims here are:

1. Generation resolves through the store, on every object — including the heavy
   multi-sheet ones, which used to skip the pass entirely and were therefore the
   most likely to ship against a stale copy.
2. A row the store wrote is marked ``derived``, and a row a person edited is not.
3. The precedence tiers that used to compete with the date are gone.

Pure: source seams and the resolver, no DB.
"""
import os
from datetime import datetime
from pathlib import Path

from app.services import mapping_store as store
from app.services.mapping_store import DEFAULT_VALUE, SOURCE_COLUMN, SUPPRESS

_BACKEND = Path(__file__).resolve().parent.parent
_APP = _BACKEND / "app"


def _src(*parts):
    return (_APP.joinpath(*parts)).read_text(encoding="utf-8")


def _apply_body():
    s = _src("services", "learning_service.py")
    return s.split("async def apply_learned_to_conversion(")[1].split("\nasync def ")[0]


# ── 1. Generation resolves through the store ─────────────────────────────────

def test_generation_resolves_before_it_builds_the_file():
    out = _src("services", "output_service.py")
    assert "RESOLVE THROUGH THE ONE DATED STORE" in out
    assert "apply_learned_to_conversion(\n            conversion, _pre_maps, force=True)" in out


def test_the_heavy_object_shortcut_is_gone():
    """A 19-sheet Customer load used to skip the pass altogether, so the biggest
    objects were the ones most likely to ship against a stale copy."""
    out = _src("services", "output_service.py")
    head = out.split("_learning_error: str | None = None")[0]
    tail = out.split("_learning_error: str | None = None")[1].split("# Per-source frames")[0]
    assert "if not _heavy:" not in tail, "the resolve pass is gated again"
    assert "apply_learned_to_conversion" in tail


def test_the_resolve_pass_reads_the_store_once():
    """One query and an in-memory resolve, rather than a query per kind per
    object spelling — which is what made it too slow to run on heavy objects and
    got it skipped there."""
    body = _apply_body()
    assert body.count("await LearnedMapping.find(") == 1
    assert "mapping_store.entries_of(_rows)" in body
    assert "mapping_store.resolve(" in body


def test_the_resolve_pass_writes_only_what_changes():
    """A generate that resolves to what the rows already say should not touch
    them — that is what lets the pass run everywhere instead of being skipped.

    The rule is unchanged; only the delivery moved. `_write` is no longer a
    coroutine because it now queues the patch rather than sending it, so the
    signature assertion moved with it — the guarantee under test is "nothing is
    written unless it changed", and that is the `if not changed` line."""
    body = _apply_body()
    assert "def _write(m, patch: dict) -> bool:" in body
    assert "if not changed:" in body
    assert "await m.set(update)" not in body


def test_the_resolve_pass_sends_its_writes_in_one_round_trip():
    """WHY THE TOOL WAS SLOW FROM INDIA, and it is not the work.

    This pass writes one document per target field — well over a thousand on a
    19-sheet Customer conversion. Sent one at a time the cost is LATENCY, not
    computation: at 2ms to the database that is a couple of seconds nobody
    notices, and at 250ms it is four minutes for exactly the same code. Which is
    why it read as random — the same click was instant on one desk and a timeout
    on another, and the profiler on the fast machine showed nothing wrong."""
    body = _apply_body()
    assert "bulk = BulkPatcher()" in body
    assert "bulk.set(m, patch)" in body
    assert "await bulk.flush()" in body
    # And no per-document write survived in the pass.
    assert "await m.set(" not in body, "a per-row round trip is still in there"
    assert "await lm.set(" not in body
    assert "await entry.row.set(" not in body


def test_the_query_is_not_scoped_by_object():
    body = _apply_body()
    query = body.split("await LearnedMapping.find(")[1].split(").to_list()")[0]
    assert "target_object" not in query


# ── 2. The rows are a view ───────────────────────────────────────────────────

def test_a_mapping_row_can_say_it_is_derived():
    model = _src("models", "mapping.py")
    assert "derived: bool = False" in model
    assert "derived_from: Optional[str] = None" in model


def test_every_row_the_store_writes_is_marked_derived():
    body = _apply_body()
    writes = body.count('"approved_by": "learning-engine"')
    assert writes >= 3, f"expected the column, suppression and default passes, got {writes}"
    assert body.count('"derived": True') == writes


def test_a_row_a_person_edits_stops_being_derived():
    """Their edit is a statement in its own right, carrying its own date, and it
    goes into the store beside every other one."""
    router = _src("routers", "mapping.py")
    assert router.count('"derived": False') + router.count('data["derived"] = False') >= 3


def test_the_engine_never_reads_its_own_copy_back_as_a_decision():
    """Otherwise the store's output re-enters the store and outranks the thing it
    was copied from."""
    assert store.entry_from_mapping(
        _Mapping(approved_by=store.ENGINE), target_field="Supplier Name") is None
    assert store.entry_from_mapping(
        _Mapping(approved_by="analyst@nextpower.com"),
        target_field="Supplier Name") is not None


class _Mapping:
    def __init__(self, approved_by):
        self.approved_by = approved_by
        self.status = "approved"
        self.source_column = "Legal Name"
        self.default_value = None
        self.approved_at = datetime(2026, 7, 31)
        self.suggested_transformation = None


# ── 3. The competing precedence tiers are gone ───────────────────────────────

def test_suppression_no_longer_loses_to_a_mapping_by_rule():
    """It was a hand-written tier: "a column mapping always beats a keep-blank".
    They are two statements about one field now, and the later one wins."""
    body = _apply_body()
    assert "suppressed_targets -= set(by_target.keys())" not in body
    assert "_suppressed_norm" not in body


def test_the_strong_transform_ordering_no_longer_decides_a_winner():
    body = _apply_body()
    assert "_candidate_order" not in body


def test_the_apply_pass_no_longer_ranks_defaults_its_own_way():
    body = _apply_body()
    assert "sorted(defaults, key=_effective_of" not in body


def test_a_human_approval_is_no_longer_permanently_immune():
    """The only precedence path that had no date test at all. An analyst
    correction made in June out-ranked the mapping workbook handed over in
    August, and nothing on screen said why."""
    gate = _apply_body().split("def _eligible(")[1].split("\n    business_object")[0]
    assert "approved_at" in gate and "effective_date" in gate
    assert "decided_by_a_person" in gate


def test_an_approval_with_no_timestamp_counts_as_older():
    gate = _apply_body().split("def _eligible(")[1].split("\n    business_object")[0]
    assert "when is not None" in gate


# ── The backfill is wired in ─────────────────────────────────────────────────

def test_the_backfill_runs_at_startup_and_on_demand():
    """Data with no seeder is an inert feature one layer up — this repo's habit."""
    main = _src("main.py")
    assert "from app.services.mapping_store_backfill import backfill" in main
    api = _src("routers", "learned.py")
    assert "backfill-dated-store" in api


def test_the_backfill_runs_after_the_seeders():
    """So a decision made in the grid is compared against a library that is
    already up to date, rather than one this boot is about to change."""
    main = _src("main.py")
    assert main.index("seed_customer_sheet_scope()") < \
           main.index("mapping_store_backfill")


# ── The whole rule, end to end, on one field ─────────────────────────────────

def test_a_field_walks_through_every_source_and_the_latest_always_wins():
    """Workbook, then gold, then a learning, then a steer, then a grid edit,
    then a custom rule — each one later than the last, each one taking over."""
    entries, expected = [], []
    timeline = [
        ("mapping workbook", SOURCE_COLUMN, "VENDOR_NAME", 13),
        ("gold example", SOURCE_COLUMN, "Legal Name", 20),
        ("auto-capture", DEFAULT_VALUE, "PRIMARY", 25),
        ("prompt", SUPPRESS, None, 28),
        ("grid", SOURCE_COLUMN, "Vendor Legal Name", 31),
        ("plain-English rule: upper-case it", SOURCE_COLUMN, "vendor_name", 2),
    ]
    for i, (origin, decision, value, day) in enumerate(timeline):
        month = 8 if i == len(timeline) - 1 else 7
        entries.append(store.Entry(
            target_field="Supplier Name", decision=decision, value=value,
            effective_date=datetime(2026, month, day), captured_from=origin))
        expected.append((decision, value))
        won = store.resolve(entries, target_field="Supplier Name")
        assert (won.decision, won.value) == expected[-1], (
            f"after {origin} the winner should be {expected[-1]}, "
            f"got {(won.decision, won.value)}")
        assert won.captured_from == origin


def test_and_shuffling_the_order_they_arrive_in_changes_nothing():
    entries = [
        store.Entry(target_field="Supplier Name", decision=SOURCE_COLUMN,
                    value=v, effective_date=datetime(2026, 7, d),
                    captured_from=f)
        for v, d, f in (("A", 13, "workbook"), ("B", 31, "grid"),
                        ("C", 20, "gold"))]
    for order in ([0, 1, 2], [2, 1, 0], [1, 0, 2], [0, 2, 1]):
        won = store.resolve([entries[i] for i in order],
                            target_field="Supplier Name")
        assert won.value == "B"
