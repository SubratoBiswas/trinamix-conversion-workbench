"""Tiny text coercions shared across the domain. Moved verbatim from engine._to_str /
engine._is_blank so the rule strategies (and anything else in the domain) can use them
without importing the engine. engine.py aliases these back to its historical names."""
from __future__ import annotations
from typing import Any


def to_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def is_blank(v: Any) -> bool:
    return v is None or to_str(v).strip() == ""


def to_float(v: Any) -> float | None:
    if v is None:
        return None
    s = to_str(v).strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


# The boolean spellings these extracts actually carry. Shared so a rule written one way
# cannot disagree with a rule written the other (engine aliases these back to
# _TRUEISH / _FALSEISH).
TRUEISH = {"yes", "y", "1", "true", "t"}
FALSEISH = {"no", "n", "0", "false", "f"}
