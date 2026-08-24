"""Precedence policy — the analyst's "whichever is latest" rule, in one place.

The same date comparison was re-derived at three sites, each behind essay-length comments:
  * output_service._conversion_rule_wins             -> conversion_rule_wins
  * the output_service _person_is_newer guard        -> person_is_newer
  * strategy_overlay.directive_for exact/wide tiebreak-> wide_directive_wins
and the write-time overlay's ``_explicit`` decision  -> mapping_outranks_directive

Phase 3 slice 5 finishes the consolidation the earlier note pointed at: all four now
delegate to ONE ordering — ``pick_latest`` over a common ``Statement`` — so "whichever
is latest" is written once and the tie-breaks the four contests used to spell with
scattered ``>=`` / ``>`` are now explicit, named terms on the Statement. Each caller
keeps its own orchestration and its own signature, so behaviour is byte-identical
(verified by a differential against the previous four functions). Pure — no framework,
no I/O — and unit-tested.

Scope: this unifies the two WRITE-TIME contests — a conversion's mapping/rule/approval
vs a strategy directive, and a sheet-specific directive vs a bundle-wide one. The
library-entry store (``app.domain.store.resolver``) keeps its own ordering by design; a
conversion statement that cannot be placed in time is "left alone" (treated as newest)
here, which is the OPPOSITE of the store's "undated sorts last", so the two orderings
are deliberately not merged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

# ── Specificity: who wins a SAME-INSTANT tie ─────────────────────────────────
# Higher wins. A conversion's own statement (a person's approval, an analyst's rule)
# outranks any directive on a tie — the mapping is the more specific thing the client
# said about THIS field. Between two directives, the sheet-specific ("exact") one
# outranks the bundle-wide one for precision. These encode, as data, the ``>=`` the
# overlay used (conversion wins the tie) and the ``>`` the directive tiebreak used
# (exact wins the tie unless wide is STRICTLY newer).
CONVERSION = 2   # a conversion's own approval or authored rule
EXACT = 1        # a sheet-specific directive
WIDE = 0         # a bundle-wide directive


@dataclass(frozen=True)
class Statement:
    """One dated claim in a write-time contest.

    ``as_of`` is the ordering key — newest wins. ``specificity`` breaks a same-instant
    tie (see the constants above). ``undated_is_newest`` is the one place the contests
    genuinely disagree about time: an authored conversion RULE that carries no date is
    "unplaceable, left alone" and treated as the NEWEST thing in its contest, while an
    undated approval or directive cannot be shown newer than anything and sorts as the
    OLDEST. That single flag is the whole of divergence (a).
    """
    as_of: Optional[datetime]
    specificity: int
    undated_is_newest: bool = False


def _rank(s: Statement) -> tuple:
    """Sort key. Larger is later/stronger, so ``max`` is the winner. An undated
    statement anchors to the end of time when it is the left-alone kind, otherwise to
    the start of time (it cannot be shown to be later than a dated rival)."""
    if s.as_of is None:
        anchor = datetime.max if s.undated_is_newest else datetime.min
    else:
        anchor = s.as_of
    return (anchor, s.specificity)


def pick_latest(statements: Sequence[Statement]) -> Optional[Statement]:
    """The winning statement — newest wins; a same-instant tie goes to the more
    specific one. Keeps the first statement on an exact key tie, but within a single
    contest the specificities are always distinct, so the winner is unambiguous.
    """
    best = None
    best_key = None
    for s in statements:
        k = _rank(s)
        if best_key is None or k > best_key:
            best, best_key = s, k
    return best


# ── The four contests, each a thin query over pick_latest ────────────────────

def conversion_rule_wins(rule_asofs: Iterable[Optional[datetime]], directive_asof) -> bool:
    """Does a conversion's OWN rule outrank the overlay directive?

    No rule -> False. An undated directive cannot be shown newer than anything, so a rule
    beats it. Otherwise any rule that is undated (left alone), or dated on/after the
    directive, wins. Order-preserving: the first qualifying rule short-circuits (matches
    the loop the caller used)."""
    directive = Statement(directive_asof, WIDE)
    for a in rule_asofs:
        rule = Statement(a, CONVERSION, undated_is_newest=True)
        if pick_latest((rule, directive)) is rule:
            return True
    return False


def person_is_newer(approved_at, directive_asof) -> bool:
    """Does a person's dated approval outrank the directive?

    An undated directive cannot be placed in time, so the approval wins. Otherwise the
    approval must carry a timestamp and be on/after the directive's date — an undated
    approval cannot be shown newer and loses."""
    approval = Statement(approved_at, CONVERSION)          # undated approval -> oldest
    directive = Statement(directive_asof, WIDE)
    return pick_latest((approval, directive)) is approval


def wide_directive_wins(exact_asof, wide_asof) -> bool:
    """When a sheet-specific ("exact") directive and a bundle-wide one both apply, the
    exact one wins a tie for precision — UNLESS the wide one is strictly newer."""
    exact = Statement(exact_asof, EXACT)
    wide = Statement(wide_asof, WIDE)
    return pick_latest((exact, wide)) is wide


def mapping_outranks_directive(status, approved_by, approved_at, source_column,
                               default_value, authored_rule_asofs,
                               directive_asof) -> bool:
    """Does the analyst's own statement on ONE mapping outrank the strategy directive?

    This is the ``_explicit`` decision the write-time overlay in ``output_service``
    consults: when True, a strategy ``blank`` is not applied and a strategy ``constant``
    only fills blanks instead of replacing every row. The ORDERING now lives in
    ``pick_latest`` (via ``person_is_newer`` and ``conversion_rule_wins``); the GATES
    below are qualification, not ordering — they decide whether the mapping is even a
    statement, not which dated statement is later.

    Two independent ways a mapping can be the later, more specific statement:

    A PERSON-SET VALUE, approved and newer than the directive.
      * ``person_set_a_value`` — a bound source column OR a typed fixed value counts.
      * status in ("approved", "overridden") — a deliberate approve/override, never a
        "suggested" auto-map guess, which is what the strategy constants exist to fix.
      * ``decision_outranks`` — the row must carry a real approval. Authorship is
        provenance, not the decider: a *dated* engine approval still wins on its date.
      * ``person_is_newer`` — the approval must be datable and on/after the directive.

    OR AN AUTHORED RULE newer than the directive (``conversion_rule_wins``). A rule the
    analyst typed is speaking too and has no status to approve; it is ranked by date like
    everything else — and an undated one is left alone (wins), per divergence (a).

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
