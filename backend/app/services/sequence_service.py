"""Issue stable generated keys — the same entity gets the same number every run.

CW row 23, and the analyst's follow-up that defines it: "write a logic in which this
should be generated once and next time onwards the same should be repeated, not
different number each run."

The rule this module exists to enforce:

    A number, once issued to a natural key, is never re-issued and never changes.

Everything else follows. Allocation is keyed on a key from the SOURCE, so a re-sorted,
filtered or re-uploaded extract cannot renumber anybody; a regenerate re-reads and
re-uses; only a key never seen before draws from the counter. The alternative — a
counter reset per run — hands the same customer a different Party Number each time,
and because Party Number is what every child interface points at, the second load
creates duplicate parties instead of updating the first.

Formats, from the analyst's example:

    ORGANIZATION   NXT000001            prefix + zero-padded ordinal
    PERSON         NXT000001_C1         the parent org's number + child counter

A PERSON is therefore numbered RELATIVE TO ITS ORGANISATION, not from the same pool —
which is why the child counter is stored per parent rather than globally.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from app.models.sequence import SequenceAllocation

_WS = re.compile(r"\s+")


def normalize_key(value) -> str:
    """The natural key, reduced so trivial differences do not split an entity in two.

    "  12345 " and "12345" are the same customer, and if they allocate separately the
    same entity ends up with two Party Numbers — the exact failure this module exists
    to prevent, arriving through the back door.
    """
    s = "" if value is None else str(value)
    return _WS.sub(" ", s).strip().casefold()


def format_value(prefix: str, seq: int, width: int = 6) -> str:
    return f"{prefix}{seq:0{width}d}"


def format_child(parent_value: str, child_seq: int, child_prefix: str = "_C") -> str:
    return f"{parent_value}{child_prefix}{child_seq}"


async def _next_seq(client_id, target_object: str, target_field: str) -> int:
    """One past the highest ordinal ever issued for this sequence.

    Deliberately the MAX over all allocations rather than a count: allocations are
    never deleted in normal use, but if one ever were, counting would re-issue a
    number that is already sitting in a loaded Oracle record.
    """
    top = await SequenceAllocation.find(
        SequenceAllocation.client_id == client_id,
        SequenceAllocation.target_object == target_object,
        SequenceAllocation.target_field == target_field,
    ).sort("-seq").limit(1).to_list()
    return (int(top[0].seq) + 1) if top else 1


async def allocate(
    *, client_id, target_object: str, target_field: str, natural_key,
    prefix: str = "NXT", width: int = 6, parent_key=None, child_prefix: str = "_C",
    project_id=None,
) -> str:
    """Return this entity's number, issuing one only if it has never had one.

    ``parent_key`` makes it a CHILD allocation: the value becomes the parent's number
    plus a per-parent counter (NXT000123_C1), which is how the analyst's PERSON case
    is numbered. The parent is allocated first if it has no number yet, so the two
    cannot disagree.
    """
    key = normalize_key(natural_key)
    if not key:
        return ""

    existing = await SequenceAllocation.find_one(
        SequenceAllocation.client_id == client_id,
        SequenceAllocation.target_object == target_object,
        SequenceAllocation.target_field == target_field,
        SequenceAllocation.natural_key == key,
    )
    if existing is not None:
        return existing.value            # ← the whole point: same run, same answer

    if parent_key is not None and normalize_key(parent_key):
        pkey = normalize_key(parent_key)
        parent = await SequenceAllocation.find_one(
            SequenceAllocation.client_id == client_id,
            SequenceAllocation.target_object == target_object,
            SequenceAllocation.target_field == target_field,
            SequenceAllocation.natural_key == pkey,
        )
        if parent is None:
            parent_value = await allocate(
                client_id=client_id, target_object=target_object,
                target_field=target_field, natural_key=pkey, prefix=prefix,
                width=width, project_id=project_id)
            parent = await SequenceAllocation.find_one(
                SequenceAllocation.client_id == client_id,
                SequenceAllocation.target_object == target_object,
                SequenceAllocation.target_field == target_field,
                SequenceAllocation.natural_key == pkey,
            )
        else:
            parent_value = parent.value
        siblings = await SequenceAllocation.find(
            SequenceAllocation.client_id == client_id,
            SequenceAllocation.target_object == target_object,
            SequenceAllocation.target_field == target_field,
            SequenceAllocation.parent_key == pkey,
        ).to_list()
        cseq = max([int(s.child_seq or 0) for s in siblings], default=0) + 1
        value = format_child(parent_value, cseq, child_prefix)
        row = SequenceAllocation(
            client_id=client_id, target_object=target_object,
            target_field=target_field, natural_key=key, value=value,
            seq=int(getattr(parent, "seq", 0) or 0), parent_key=pkey,
            child_seq=cseq, issued_in_project_id=project_id)
    else:
        seq = await _next_seq(client_id, target_object, target_field)
        row = SequenceAllocation(
            client_id=client_id, target_object=target_object,
            target_field=target_field, natural_key=key,
            value=format_value(prefix, seq, width), seq=seq,
            issued_in_project_id=project_id)

    try:
        await row.insert()
    except Exception:                                           # noqa: BLE001
        # Lost a race on the unique index. The winner's number is the correct one —
        # re-read rather than retrying with a fresh ordinal, which is how a duplicate
        # would be born.
        again = await SequenceAllocation.find_one(
            SequenceAllocation.client_id == client_id,
            SequenceAllocation.target_object == target_object,
            SequenceAllocation.target_field == target_field,
            SequenceAllocation.natural_key == key,
        )
        if again is not None:
            return again.value
        raise
    return row.value


async def allocate_many(
    *, client_id, target_object: str, target_field: str,
    keys: Iterable, prefix: str = "NXT", width: int = 6,
    parent_keys: Optional[dict] = None, project_id=None,
) -> dict:
    """Numbers for a whole column, in one pass. Returns {natural_key: value}.

    Order matters only for keys that have never been issued: those are numbered in the
    order given, which is the frame order, so a first run reads NXT000001, NXT000002…
    down the file. Every subsequent run returns what is stored, so re-sorting the
    extract afterwards changes nothing.
    """
    out: dict = {}
    parent_keys = parent_keys or {}
    for k in keys:
        nk = normalize_key(k)
        if not nk or nk in out:
            continue
        out[nk] = await allocate(
            client_id=client_id, target_object=target_object,
            target_field=target_field, natural_key=nk, prefix=prefix, width=width,
            parent_key=parent_keys.get(nk), project_id=project_id)
    return out
