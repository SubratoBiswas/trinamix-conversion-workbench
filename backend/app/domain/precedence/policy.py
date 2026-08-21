"""Precedence policy — the analyst's "whichever is latest" rule, in one place.

The same date comparison was re-derived at three sites, each behind essay-length comments:
  * output_service._conversion_rule_wins             -> conversion_rule_wins
  * the output_service _person_is_newer guard        -> person_is_newer
  * strategy_overlay.directive_for exact/wide tiebreak-> wide_directive_wins

Each caller keeps its own orchestration and delegates only the comparison, so behaviour is
byte-identical. Pure — no framework, no I/O — and unit-tested. This is the first step of
the precedence consolidation; a later slice can compose these into a single
Statement-based resolver once the call sites build Statement lists.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional


def conversion_rule_wins(rule_asofs: Iterable[Optional[datetime]], directive_asof) -> bool:
    """Does a conversion's OWN rule outrank the overlay directive?

    No rule -> False. An undated directive cannot be shown newer than anything, so a rule
    beats it -> True. Otherwise any rule that is undated, or dated on/after the directive,
    wins. Order-preserving: the first qualifying rule short-circuits (matches the loop the
    caller used)."""
    asofs = list(rule_asofs)
    if not asofs:
        return False
    if directive_asof is None:
        return True
    for when in asofs:
        if when is None or when >= directive_asof:
            return True
    return False


def person_is_newer(approved_at, directive_asof) -> bool:
    """Does a person's dated approval outrank the directive?

    An undated directive cannot be placed in time, so the person wins -> True. Otherwise
    the approval must carry a timestamp and be on/after the directive's date."""
    if directive_asof is None:
        return True
    return bool(approved_at) and approved_at >= directive_asof


def wide_directive_wins(exact_asof, wide_asof) -> bool:
    """When a sheet-specific ("exact") directive and a bundle-wide one both apply, the
    exact one wins a tie for precision — UNLESS the wide one is strictly newer."""
    return wide_asof is not None and (exact_asof is None or wide_asof > exact_asof)


def mapping_outranks_directive(status, approved_by, approved_at, source_column,
                               default_value, authored_rule_asofs,
                               directive_asof) -> bool:
    """Does the analyst's own statement on ONE mapping outrank the strategy directive?

    This is the ``_explicit`` decision the write-time overlay in ``output_service``
    consults: when True, a strategy ``blank`` is not applied and a strategy
    ``constant`` only fills blanks instead of replacing every row. It is the same
    "whichever is latest" rule the rest of this module encodes, applied to a mapping's
    provenance. Extracted here so all of precedence lives in one place; the caller
    keeps its orchestration and passes primitives, so behaviour is byte-identical.

    Two independent ways a mapping can be the later, more specific statement:

    A PERSON-SET VALUE, approved and newer than the directive.
      * ``_person_set_a_value`` — a bound source column OR a typed fixed value counts.
        It read ``source_column`` alone once, so a "Receipt Routing = 3" typed into
        the Fixed-value box and approved left ``source_column`` null and was ignored,
        and the 13-Jul strategy constant shipped DIRECT over it. Typing a constant is
        as deliberate as binding a column.
      * status in ("approved", "overridden") — a deliberate approve/override, never a
        "suggested" auto-map guess, which is exactly what the strategy constants exist
        to correct.
      * ``decision_outranks`` — the row must carry a real approval. Authorship is
        provenance, not the decider: an approval stamped "learning-engine" is not a
        person, but a *dated* engine approval still wins on its date. Making
        authorship decisive was the bug where a 13-Jul directive beat 03/04-Aug
        approvals purely because the newer statement was signed "learning-engine".
      * ``person_is_newer`` — the approval must be datable and on/after the directive.
        An undated directive cannot be placed in time, so a human approval beats it;
        an undated approval cannot be shown newer, so it does not resurrect an old
        seeded row over a later correction.

    OR AN AUTHORED RULE newer than the directive.
      A rule the analyst typed is speaking too, and it has no status to approve — so
      requiring one silenced custom rules on fields still sitting at "suggested". A
      rule is ranked by date like everything else: written after the document it wins,
      before it does not. ``authored_rule_asofs`` are the ``as_of`` stamps of the
      analyst's OWN rules (the engine's suggested_transformation is excluded upstream).

    ``bool(...) or authored_rule_wins`` mirrors the original expression exactly.
    """
    approver = str(approved_by or "").strip()
    by_a_person = bool(approver) and approver != "learning-engine"
    decision_outranks = bool(approver) or by_a_person
    person_new = person_is_newer(approved_at, directive_asof)
    person_set_a_value = bool(str(source_column or "").strip()
                              or str(default_value or "").strip())
    asofs = list(authored_rule_asofs or [])
    authored_rule_wins = bool(asofs) and conversion_rule_wins(asofs, directive_asof)
    return bool(person_set_a_value and status in ("approved", "overridden")
                and decision_outranks and person_new) or authored_rule_wins
