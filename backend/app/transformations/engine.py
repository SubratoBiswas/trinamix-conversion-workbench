"""Transformation rule engine.

Each rule has a `rule_type` and a `config` dict. Rules execute serially over
either a single value (per-cell) or a row dict (for rules that pull other
columns: CONCAT, COALESCE, CONDITIONAL, CASE_WHEN). Some rules also need a
broader runtime context (row index, current user, today's date, named
crosswalks) — that's the optional ``ctx`` argument.

Adding a rule type
------------------

* Implement a ``RuleStrategy`` in ``app/domain/rules/library`` and register it in
  ``app/domain/rules/registry.py`` (one class + one register() line).
* Add the string to ``RULE_TYPES`` in ``app/models/transformation.py``.
* Add a default config + a typed form on the frontend
  ``TransformationStudioPage``. The form contributes the same JSON the engine
  consumes here.
"""
from __future__ import annotations

from typing import Any

from app.domain.rules.registry import standard_rule_engine

# Phase 1c: rule types migrated to app.domain.rules dispatch through this registry;
# the rest keep their if/elif branch below. Adding a type is one class + one register().
_RULE_REGISTRY = standard_rule_engine()


# Phase 1c COMPLETE: every rule type now dispatches through _RULE_REGISTRY. All rule
# logic, helpers and reference data live in app.domain (rules.library.*, dates.fbdi_date,
# geo.country, phone.parse, text, rules.context). _apply_one_rule is pure dispatch;
# engine.py owns no transformation logic itself.


def apply_rule(
    rule_type: str,
    config: dict[str, Any],
    value: Any,
    row: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
) -> Any:
    """One rule, plus any rules chained after it in ``config["then"]``.

    The chain exists because THE STORE HOLDS ONE RULE PER FIELD. That is the
    point of the one dated store — a field has a single live answer, so "newest
    wins" is decidable — but an analyst's single sentence is not always a single
    rule type: "concatenate entityid and internalid, and add _B on a billing row"
    is one instruction that needs a CONCAT and then a SUFFIX_WHEN. Split across
    two entries the store could keep only one of them, and the field would ship
    half its key. Chained inside one config, one entry carries the whole sentence.
    """
    out = _apply_one_rule(rule_type, config, value, row=row, ctx=ctx)
    for nxt in ((config or {}).get("then") or []):
        if isinstance(nxt, dict) and nxt.get("rule_type"):
            out = apply_rule(nxt["rule_type"], nxt.get("config") or {}, out,
                             row=row, ctx=ctx)
    return out


def _apply_one_rule(
    rule_type: str,
    config: dict[str, Any],
    value: Any,
    row: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
) -> Any:
    rt = (rule_type or "").upper().strip()
    cfg = config or {}
    ctx = ctx or {}

    # Every rule type dispatches to its app.domain.rules strategy. An unknown type
    # returns the value unchanged — the historic fallback, preserved.
    if rt in _RULE_REGISTRY:
        return _RULE_REGISTRY.apply(rt, cfg, value, row, ctx)

    return value


def apply_pipeline(
    rules: list[dict[str, Any]],
    value: Any,
    row: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
) -> Any:
    out = value
    for r in rules:
        out = apply_rule(
            r.get("rule_type", ""), r.get("config", {}), out, row=row, ctx=ctx
        )
    return out
