"""Numeric rule strategies migrated out of engine._apply_one_rule. Self-contained:
value + config + the domain text/number helpers. Verbatim reproductions of the branches."""
from __future__ import annotations
from typing import Any
from app.domain.text import to_str, to_float


class NumberFormatRule:
    rule_type = "NUMBER_FORMAT"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        decimals = int(config.get("decimals", 2))
        s = to_str(value).strip().replace(",", "")
        if s == "":
            return s
        try:
            return f"{float(s):.{decimals}f}"
        except ValueError:
            return value


class ArithmeticRule:
    rule_type = "ARITHMETIC"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        op = (config.get("op") or "round").lower()
        amount = to_float(config.get("amount"))
        decimals = config.get("decimals")
        n = to_float(value)
        if n is None:
            return value
        if op == "add" and amount is not None:
            n = n + amount
        elif op == "subtract" and amount is not None:
            n = n - amount
        elif op == "multiply" and amount is not None:
            n = n * amount
        elif op == "divide" and amount not in (None, 0):
            n = n / amount
        elif op == "abs":
            n = abs(n)
        elif op == "negate":
            n = -n
        if decimals not in (None, ""):
            try:
                return round(n, int(decimals))
            except (TypeError, ValueError):
                return n
        if op == "round":
            return round(n)
        return n
