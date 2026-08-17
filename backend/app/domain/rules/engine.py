"""Registry-dispatch rule engine. Strategies self-register by type; engine._apply_one_rule
delegates a migrated rule type here and keeps its if/elif for the rest, so migration is
incremental and behaviour-preserving."""
from __future__ import annotations
from typing import Any
from app.domain.rules.strategy import RuleStrategy


class RuleEngine:
    def __init__(self) -> None:
        self._by_type: dict[str, RuleStrategy] = {}

    def register(self, strategy: RuleStrategy) -> "RuleEngine":
        self._by_type[strategy.rule_type.upper()] = strategy
        return self

    def __contains__(self, rule_type: str) -> bool:
        return (rule_type or "").upper() in self._by_type

    def apply(self, rule_type: str, config: dict, value: Any,
              row: dict | None = None, ctx: dict | None = None) -> Any:
        return self._by_type[(rule_type or "").upper()].apply(value, config, row, ctx)
