"""Why the tool is slow from India, and it is not the work.

The analysts working from India reported latency on mapping and output
generation. The transforms themselves are not the cost — those run on pandas in a
worker thread and finish in milliseconds. The cost is how many times a single
request goes to the database and back.

Every ``await doc.set({...})`` is one round trip, and they run in sequence. A
mapping run over the 19-sheet Customer template touches well over a thousand
rows; the apply-learnings pass at generate touches one per target field. At 2ms
to the cluster that is a couple of seconds nobody notices. At 250ms — an app and
a cluster in different regions, or a long link to a cold instance — it is four
minutes, for byte-identical code.

That is why it read as intermittent and unfixable: the same click is instant on
one desk and a timeout on another, and a profiler on the fast machine shows
nothing wrong. Nothing in the code distinguishes the two cases, so nothing in the
code was ever suspected.

These tests count round trips rather than measure time — a timing assertion on a
shared runner is a flake, and the number of hops is the thing that actually
changed.

Pure: a fake document and a recording collection. No database.
"""
import asyncio
from pathlib import Path

from app.services.bulk_write import BulkPatcher, bulk_increment

_BACKEND = Path(__file__).resolve().parent.parent


class _Coll:
    """Records every bulk_write, so the tests can count hops."""

    def __init__(self):
        self.calls = []

    async def bulk_write(self, requests, ordered=True):
        self.calls.append({"n": len(requests), "ordered": ordered,
                           "requests": requests})
        return len(requests)


def _model(coll):
    class Doc:
        _coll = coll

        @classmethod
        def get_motor_collection(cls):
            return cls._coll

        def __init__(self, _id, **kw):
            self.id = _id
            for k, v in kw.items():
                setattr(self, k, v)
    return Doc


# ── The hop count ────────────────────────────────────────────────────────────

def test_a_thousand_patches_go_out_in_one_round_trip():
    """The whole point. A thousand sequential writes at 250ms is four minutes;
    one bulk_write is one hop."""
    coll = _Coll()
    Doc = _model(coll)
    bulk = BulkPatcher()
    for i in range(1000):
        bulk.set(Doc(i, status="suggested"), {"status": "approved"})
    assert asyncio.run(bulk.flush()) == 1000
    assert len(coll.calls) == 1, f"{len(coll.calls)} round trips"
    assert coll.calls[0]["n"] == 1000


def test_two_collections_are_two_writes_and_no_more():
    """One bulk_write addresses one collection, so the floor is the number of
    collections touched — not the number of documents."""
    a, b = _Coll(), _Coll()
    A, B = _model(a), _model(b)
    bulk = BulkPatcher()
    for i in range(50):
        bulk.set(A(i), {"x": 1})
        bulk.set(B(i), {"y": 2})
    asyncio.run(bulk.flush())
    assert len(a.calls) == 1 and len(b.calls) == 1


def test_nothing_queued_is_nothing_sent():
    """A re-generate that resolves to exactly what is stored must cost zero hops,
    which is what makes the pass safe to run on every object instead of being
    skipped on the heavy ones."""
    coll = _Coll()
    bulk = BulkPatcher()
    assert asyncio.run(bulk.flush()) == 0
    assert coll.calls == []


def test_an_empty_patch_is_not_a_write():
    coll = _Coll()
    Doc = _model(coll)
    bulk = BulkPatcher()
    bulk.set(Doc(1), {})
    assert len(bulk) == 0
    asyncio.run(bulk.flush())
    assert coll.calls == []


def test_the_batch_is_unordered_so_one_failure_does_not_abandon_the_rest():
    """These are independent single-document updates with no dependency between
    them. Ordered, a single rejected update would drop every one after it."""
    coll = _Coll()
    Doc = _model(coll)
    bulk = BulkPatcher()
    bulk.set(Doc(1), {"a": 1})
    asyncio.run(bulk.flush())
    assert coll.calls[0]["ordered"] is False


def test_counters_are_incremented_in_one_hop_too():
    """Counters are the worst offenders per unit of value: a field nobody reads
    during the run, costing a full round trip every time it moves."""
    coll = _Coll()
    Doc = _model(coll)
    assert asyncio.run(bulk_increment(Doc, {i: 1 for i in range(300)},
                                      "records_auto_fixed")) == 300
    assert len(coll.calls) == 1


# ── It must not become a correctness bug ─────────────────────────────────────

def test_the_document_is_updated_in_memory_immediately():
    """Several callers read a field back after setting it. Making that read wait
    for the flush would turn a latency fix into a silent data bug."""
    Doc = _model(_Coll())
    d = Doc(1, status="suggested", source_column=None)
    BulkPatcher().set(d, {"status": "approved", "source_column": "vend_num"})
    assert d.status == "approved"
    assert d.source_column == "vend_num"


def test_the_patch_that_goes_on_the_wire_is_the_patch_that_was_queued():
    """A shared mutable dict would let a later edit rewrite an earlier queued
    patch, and the row would ship a value nobody chose."""
    coll = _Coll()
    Doc = _model(coll)
    bulk = BulkPatcher()
    patch = {"status": "approved"}
    bulk.set(Doc(1), patch)
    patch["status"] = "rejected"          # the caller reuses its dict
    asyncio.run(bulk.flush())
    sent = coll.calls[0]["requests"][0]._doc["$set"]
    assert sent["status"] == "approved"


def test_a_document_with_no_id_is_not_written_blind():
    """An unsaved document has no _id, and {"_id": None} would either match
    nothing or — far worse — match some other document that happens to have a
    null id."""
    coll = _Coll()
    Doc = _model(coll)
    bulk = BulkPatcher()
    bulk.set(Doc(None), {"status": "approved"})
    asyncio.run(bulk.flush())
    assert coll.calls == []


def test_a_driver_failure_is_raised_not_swallowed():
    """A batch that quietly dropped half its updates would be far worse than a
    slow one: the screen would show the new value and the file would carry the
    old. That is the exact class of bug this codebase keeps paying for."""
    class _Boom(_Coll):
        async def bulk_write(self, requests, ordered=True):
            raise RuntimeError("write concern failed")

    Doc = _model(_Boom())
    bulk = BulkPatcher()
    bulk.set(Doc(1), {"a": 1})
    try:
        asyncio.run(bulk.flush())
    except RuntimeError:
        return
    raise AssertionError("the failure was swallowed")


def test_flushing_twice_does_not_write_twice():
    coll = _Coll()
    Doc = _model(coll)
    bulk = BulkPatcher()
    bulk.set(Doc(1), {"a": 1})
    asyncio.run(bulk.flush())
    asyncio.run(bulk.flush())
    assert len(coll.calls) == 1


# ── The hot paths actually use it ────────────────────────────────────────────

def _src(*parts):
    return _BACKEND.joinpath(*parts).read_text(encoding="utf-8")


def test_the_mapping_run_no_longer_writes_one_row_at_a_time():
    """The other half of the reported latency. A mapping run over the Customer
    template saves well over a thousand suggestions."""
    body = _src("app", "services", "mapping_service.py").split(
        "saved: list[MappingSuggestion] = []")[1][:2600]
    assert "bulk.set(m, {" in body
    assert "await bulk.flush()" in body
    assert "await m.set({" not in body, "a per-row update is still in there"
    assert "await m.insert()" not in body, "a per-row insert is still in there"
    assert "insert_many(fresh)" in body


def test_the_new_rows_get_their_ids_back():
    """`insert_many` is only safe here because the apply-learnings pass runs on
    these same objects immediately afterwards. Rows without ids would queue
    updates against None and silently do nothing — a faster no-op."""
    body = _src("app", "services", "mapping_service.py").split(
        "saved: list[MappingSuggestion] = []")[1][:2600]
    assert "if m.id is None:" in body
    assert "m.id = _new_ids.get(m.target_field_id)" in body


def test_the_apply_pass_flushes_before_it_returns():
    """A queue that is never flushed is not a fast write, it is a lost one."""
    body = _src("app", "services", "learning_service.py").split(
        "async def apply_learned_to_conversion(")[1].split("\nasync def ")[0]
    assert "await bulk.flush()" in body
    assert body.index("await bulk.flush()") < body.rindex("return auto_count")
