"""The per-row context object the rule engine reads (Phase 2, slice 4).

Relocated VERBATIM out of app.services.output_service. ``RowWithTargets`` is the dict-like
row a rule's ``row.get(...)`` / iteration sees during generation: it exposes the SOURCE
columns and, additively, any TARGET column already computed earlier in the target
sequence — so a rule can depend on a field computed before it (Party Number on Party
Type), which a source-only row could not express. The natural counterpart to the row
helpers in ``app.domain.rules.context`` (_resolve_column / _row_value_ci) that read it.
Pure: no pandas, no I/O, no service imports. output_service imports it back under its
historical underscore name, so its call sites are unchanged."""
from __future__ import annotations


_MISSING = object()


class RowWithTargets:
    """A per-row context that can also see the TARGET columns already computed.

    A rule whose condition names another TARGET field could not work at all. The
    per-row context is built from SOURCE columns, so ``row.get("Party Type")``
    returned None on every row, silently, and the rule fell through to its default.
    Three 31-Jul issues are that one fact:

        row 36  "Cannot apply transformation logic where the value of a target field
                 (Party Number) depends on the value of another target field
                 (Party Type)."
        row 23  Party Type "still shows blank rows instead of default as ORGANIZATION"
        row 16/22  "Tried using custom transformation rule, but its not working"

    It is the same shape as BLANK_IF_EQUALS, which had to be lifted out of the
    row-local engine entirely for exactly this reason.

    Targets are consulted only where the SOURCE has no column of that name, so this
    is purely additive: every rule that resolves today resolves identically, and only
    the lookups that used to return nothing now find a value. Fields are computed in
    target-sequence order, so a rule can read any field that precedes it — which is
    the order Oracle's own templates put dependencies in (Party Type is column 4,
    Party Number column 5).
    """
    __slots__ = ("_src", "_tgt", "_i")

    def __init__(self, src_row: dict, targets: dict, i: int):
        self._src = src_row
        self._tgt = targets
        self._i = i

    def get(self, key, default=None):
        v = self._src.get(key, _MISSING)
        if v is not _MISSING:
            return v
        col = self._tgt.get(key)
        return col[self._i] if col is not None else default

    def __getitem__(self, key):
        v = self.get(key, _MISSING)
        if v is _MISSING:
            raise KeyError(key)
        return v

    def __contains__(self, key):
        return key in self._src or key in self._tgt

    # ITERATION, which a dict has and this did not — and its absence was not a
    # missing convenience, it was a crash.
    #
    # A rule that asks "which of these column names does this row have?" writes
    # `{norm(k): k for k in row}`. With no __iter__, Python falls back to the LEGACY
    # sequence protocol: it calls row[0], row[1], … until IndexError. __getitem__
    # raised KeyError(0) instead, so the loop blew up on its first step — and because
    # generation runs in a background worker, it surfaced as a conversion that simply
    # never produced output. Supplier Site and Supplier Site Assignment both use the
    # site-key rule that iterates the row, which is exactly why those two of six sat
    # at "mapping_suggested" while the other four generated.
    #
    # Source keys first, then any TARGET column the source does not already have, so
    # iteration order matches what get() resolves: a source column of the same name
    # wins, and nothing is yielded twice.
    def __iter__(self):
        seen = set()
        for k in self._src:
            seen.add(k)
            yield k
        for k in self._tgt:
            if k not in seen:
                yield k

    def keys(self):
        return list(self)

    def __len__(self):
        return sum(1 for _ in self)
