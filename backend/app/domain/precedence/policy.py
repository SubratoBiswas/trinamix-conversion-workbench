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
