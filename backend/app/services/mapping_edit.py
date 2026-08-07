"""Turning an ordinary grid edit into a FINAL decision — the pure part.

Subrato, 06-Aug: "user should not click on approve, any edit should be considered
final". The reliability reason this matters, beyond clicks: a mapping row still
marked ``suggested`` is treated as fair game by ``apply_learned_to_conversion`` and
gets overwritten from the library on the next generate/refresh, regardless of who
last touched it — the "I saved it, refreshed, it's gone" bug. Giving an edit a
DECIDED status (with a person as approver) makes it a protected human decision that
the library can only beat by being dated later.

The status decision is a pure function of the patch and the row's current values, so
it lives here where it can be tested without a database. The router adds the
``approved_by`` / ``approved_at`` stamps (it alone knows who and when).
"""
from __future__ import annotations

from typing import Any

# The fields whose change IS a mapping decision. Editing any of them is the analyst
# saying what the field should be; a comment or a review flag is not.
CONTENT_FIELDS = frozenset({"source_column", "default_value", "suggested_transformation"})


def _merged(patch: dict, key: str, current: Any) -> Any:
    """The value a field WILL have after this patch — the patch if it names the
    field, otherwise what the row already carries."""
    return patch[key] if key in patch else current


def finalize_content_edit(patch: dict, *, cur_source: Any, cur_default: Any,
                          cur_transform: Any) -> dict:
    """The extra fields that make a content edit final, or ``{}``.

    Applies only when the patch actually touches a content field and carries NO
    explicit status — so Approve / Reject / the explicit override paths, which send
    their own status, are left to say exactly what they mean.

    A positive mapping (a source column, a fixed value, or a rule) becomes an
    ``overridden`` human decision. An edit that clears everything is "leave this
    blank": ``not_applicable`` with no source or default and nothing left to review,
    which is what actually keeps the generator from refilling the column.
    """
    if "status" in patch or not (CONTENT_FIELDS & set(patch)):
        return {}
    new_source = _merged(patch, "source_column", cur_source)
    new_source = str(new_source).strip() if new_source is not None else ""
    new_default = _merged(patch, "default_value", cur_default)
    new_default = str(new_default).strip() if new_default is not None else ""
    has_tx = bool(_merged(patch, "suggested_transformation", cur_transform))
    if new_source or new_default or has_tx:
        return {"status": "overridden"}
    return {"status": "not_applicable", "source_column": None,
            "default_value": None, "review_required": 0}
