"""ONE rule for which mapping row wins — used by the screen AND by the file.

THE BUG THIS EXISTS TO PREVENT
------------------------------
A target field can hold more than one MappingSuggestion row. ``mapping_service``
documents why: a re-run race on the suggest-mapping endpoint can insert a second
row for the same (conversion, target field), and there is no unique index
stopping it.

Before this module there were TWO rules for choosing between them.

The screen used ``collapse_mapping_dupes`` — status, then a row that actually
carries a source column, then the freshest. It was reachable from exactly one
caller: the endpoint Mapping Review reads.

Every generation path used its own inline copy instead — three in
``output_service``, one each in ``learning_service``, ``copilot_grounding`` and
``readiness_service``. Those copies compared status ALONE, with a strict ``>``
over the order Mongo happened to return rows in, so on a tie the FIRST row won —
which is the oldest, and has nothing to do with what the analyst decided. They
also ranked ``rejected`` and ``not_applicable`` the other way round.

The result is the most expensive shape of defect this codebase has: the analyst
edits a mapping, Mapping Review shows the edit because it collapses correctly,
and the generated FBDI carries the other row. The screen and the file disagree
and the screen looks right. It bit every track at once, because it had nothing to
do with any particular business object — only with which code path read the rows.

WHY THE STATUS TABLE IS THE SCREEN'S ONE
----------------------------------------
The two tables disagreed on ``rejected`` vs ``not_applicable``. The screen's
ordering wins, deliberately: when the two disagree, the file must move to the
screen. An analyst validates against what is in front of them, and a file that
quietly ranks decisions differently from the screen that produced it cannot be
checked by anybody.

Pure — no Beanie, no Mongo, no models. Anything with ``status``,
``source_column``, ``updated_at``/``created_at`` and ``target_field_id`` works,
so this is unit-testable without infrastructure and cannot grow a dependency that
would tempt someone to reimplement it locally again.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# Strongest human decision first. "" is a row whose status was never set.
#
# rejected outranks not_applicable: both mean "no source column", but rejected is
# a person turning a suggestion down, while not_applicable is most often the
# engine or a gold example saying a field does not apply here. A person outranks
# a rule. The generator used to have these the other way round.
MAP_STATUS_PRIORITY: dict[str, int] = {
    "overridden": 5, "approved": 4, "rejected": 3, "not_applicable": 2,
    "suggested": 1, "": 0,
}


def mapping_ts(m: Any) -> float:
    """When this row last said something. Falls back to creation.

    ``updated_at`` is only meaningful if every edit stamps it — see
    ``stamp_edit``. It did not, for a long time, which is why "freshest" silently
    meant "created last" and the tie-break did nothing it claimed to do.
    """
    d = getattr(m, "updated_at", None) or getattr(m, "created_at", None)
    try:
        return d.timestamp() if d else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def dedup_key(m: Any) -> tuple:
    """Strongest human status, then a row that actually carries a source, then
    freshest.

    The middle term is the one the generator copies were missing and it is not a
    nicety: two rows both sitting at "suggested" — one the analyst edited to
    point at a real column, one an auto-map twin with nothing in it — tie on
    status, and without this the winner is decided by insertion order. That is
    the exact path the analyst hits, because editing a source column and saving
    does NOT move the row out of "suggested".
    """
    return (
        MAP_STATUS_PRIORITY.get((getattr(m, "status", "") or ""), 0),
        1 if (getattr(m, "source_column", None) or "") else 0,
        mapping_ts(m),
    )


def best_mapping_by_target(items: list) -> dict:
    """{target_field_id -> the winning row}, keyed as the rows key it.

    The generation-side entry point. Keys are the raw ``target_field_id`` values,
    not strings, because every caller looks the result up against ``field.id``.
    """
    best: dict = {}
    for m in items:
        k = getattr(m, "target_field_id", None)
        cur = best.get(k)
        if cur is None or dedup_key(m) > dedup_key(cur):
            best[k] = m
    return best


def collapse_mapping_dupes(items: list) -> list:
    """One row per target field, preserving first-occurrence order.

    The read/export entry point. Non-destructive: physical cleanup happens at map
    time and through the dedupe-mappings endpoint.
    """
    best: dict = {}
    for m in items:
        k = str(getattr(m, "target_field_id", None))
        cur = best.get(k)
        if cur is None or dedup_key(m) > dedup_key(cur):
            best[k] = m
    seen: set = set()
    out: list = []
    for m in items:
        k = str(getattr(m, "target_field_id", None))
        if k in seen:
            continue
        seen.add(k)
        out.append(best[k])
    return out


def stamp_edit(patch: dict) -> dict:
    """Add ``updated_at`` to a mapping-row patch. Use on EVERY write.

    The model has carried ``updated_at`` from the start and nothing but the
    auto-mapper ever set it, so a human edit left it at the creation time while a
    re-run of auto-map stamped its own row with 'now'. Recency then pointed at
    the machine's guess rather than the person's decision — the opposite of the
    stated precedence, which is that the last statement by date is final.

    Returns a new dict; the caller's is untouched.
    """
    out = dict(patch or {})
    out["updated_at"] = datetime.utcnow()
    return out
