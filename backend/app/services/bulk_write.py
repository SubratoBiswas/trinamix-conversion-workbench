"""Queue document updates and send them in one round trip.

WHY THIS EXISTS
---------------
Analysts in India reported the tool being slow. The cause is not the work — the
transforms run on pandas in a worker thread and finish in milliseconds — it is
the number of times the request goes to the database and back.

Every ``await doc.set({...})`` is one network round trip, and they run in
sequence. That cost is invisible when the app and MongoDB sit in the same region
(a round trip is a millisecond or two, so a thousand of them is a second nobody
notices) and it is the whole experience when they do not: at 60ms it is a minute,
at 250ms it is four. NOTHING in the code changes between those two cases, which
is why the slowness reads as random and unfixable — the same click is instant for
one person and a timeout for another, and the profiler on the fast machine shows
nothing wrong.

The apply-learnings pass over a 19-sheet Customer conversion writes one document
per target field. Batched into a single ``bulk_write``, a thousand round trips
become one.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not change WHAT is written, or WHEN a write is decided. Callers keep
their own "only write what actually changed" logic — that is a separate and
already-load-bearing optimisation — and the in-memory document is still updated
at the moment of the decision, so code reading a field back after setting it sees
the new value exactly as it did before.

Nor does it hide failures. ``flush`` raises what the driver raises. A batch that
silently dropped half its updates would be far worse than a slow one: the screen
would show the new value and the file would carry the old.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class BulkPatcher:
    """Collect ``{document: patch}`` and apply them in one write per collection.

    Usage mirrors ``await doc.set(patch)``::

        bulk = BulkPatcher()
        bulk.set(row, {"status": "approved"})   # in-memory now, on the wire later
        ...
        await bulk.flush()                      # one round trip per collection

    The in-memory document is updated immediately, on purpose: several callers
    read a field back after setting it, and making that read wait for a flush
    would turn a latency fix into a correctness bug.
    """

    __slots__ = ("_ops", "_count")

    def __init__(self) -> None:
        # {model class: [(id, patch)]} — grouped by collection because one
        # bulk_write addresses one collection.
        self._ops: dict[Any, list[tuple[Any, dict]]] = {}
        self._count = 0

    def __len__(self) -> int:
        return self._count

    @property
    def pending(self) -> int:
        return self._count

    def set(self, doc: Any, patch: dict) -> None:
        """Queue ``patch`` for ``doc`` and apply it in memory at once."""
        if not patch:
            return
        for k, v in patch.items():
            try:
                setattr(doc, k, v)
            except Exception:                                   # noqa: BLE001
                # A patch key that is not a model field is the caller's business;
                # it still goes to the database, where it is a real column.
                pass
        self._ops.setdefault(type(doc), []).append((doc.id, dict(patch)))
        self._count += 1

    async def flush(self) -> int:
        """Send every queued patch. Returns how many documents were addressed.

        Raises whatever the driver raises. A batch that quietly dropped half its
        updates would be worse than a slow one — the screen would show the new
        value and the generated file would carry the old.
        """
        if not self._ops:
            return 0
        from pymongo import UpdateOne

        total = 0
        for model, items in self._ops.items():
            requests = [UpdateOne({"_id": _id}, {"$set": patch})
                        for _id, patch in items if _id is not None]
            if not requests:
                continue
            coll = getattr(model, "get_motor_collection", None)
            if coll is None:
                # Not a Beanie document — a stand-in with the same ``.set``
                # surface, which is what the unit tests for these passes use. The
                # in-memory patch has already been applied by ``set`` above, so
                # there is nothing left to do and nothing to hide: every real
                # model has this method, so this branch cannot quietly put a
                # production path back on one round trip per row.
                total += len(requests)
                continue
            coll = coll()
            # ordered=False: these are independent single-document updates with no
            # dependency between them, so one failure must not abandon the rest,
            # and the driver may pipeline them.
            await coll.bulk_write(requests, ordered=False)
            total += len(requests)
        log.debug("bulk flush: %d document(s) across %d collection(s)",
                  total, len(self._ops))
        self._ops.clear()
        self._count = 0
        return total


async def bulk_increment(model: Any, ids: dict, field: str) -> int:
    """``{id: amount}`` → one ``$inc`` per document, in one round trip.

    Counters are the worst offenders per unit of value: a field nobody reads
    during the run, costing a full round trip each time it moves.
    """
    if not ids:
        return 0
    from pymongo import UpdateOne

    requests = [UpdateOne({"_id": _id}, {"$inc": {field: amount}})
                for _id, amount in ids.items() if _id is not None and amount]
    if not requests:
        return 0
    coll = getattr(model, "get_motor_collection", None)
    if coll is None:
        return len(requests)      # a test stand-in; see BulkPatcher.flush
    await coll().bulk_write(requests, ordered=False)
    return len(requests)
