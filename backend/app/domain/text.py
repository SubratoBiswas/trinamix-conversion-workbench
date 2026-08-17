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
