"""A transformation rule as a strategy object. Each concrete rule implements ``apply``;
the RuleEngine dispatches by ``rule_type``. Replaces the long ``if rt == ...`` chain in
engine._apply_one_rule — one class per rule type, each independently testable."""
from __future__ import annotations
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RuleStrategy(Protocol):
    rule_type: str
    def apply(self, value: Any, config: dict, row: dict | None, ctx: dict | None) -> Any: ...
