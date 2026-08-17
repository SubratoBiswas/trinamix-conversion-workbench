"""Value/lookup rule strategies migrated out of engine._apply_one_rule. Self-contained:
value + config + the domain text helpers. Each reproduces its former branch verbatim."""
from __future__ import annotations
from typing import Any
from app.domain.text import to_str, is_blank


class DefaultValueRule:
    rule_type = "DEFAULT_VALUE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        return config.get("value", "") if is_blank(value) else value


class ConstantRule:
    rule_type = "CONSTANT"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        # Always overwrite with the configured value, regardless of source.
        return config.get("value", "")


class ValueMapRule:
    rule_type = "VALUE_MAP"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        # Direct dict lookup, optionally case-insensitive. Reserved keys
        # (case_insensitive, default, _prompt) are stripped from the lookup.
        s = to_str(value)
        case_insensitive = config.get("case_insensitive", True)
        default = config.get("default")
        mapping = {
            k: v for k, v in config.items()
            if k not in ("case_insensitive", "default", "_prompt")
        }
        if case_insensitive:
            for k, v in mapping.items():
                if isinstance(k, str) and k.lower() == s.lower():
                    return v
        else:
            if s in mapping:
                return mapping[s]
        return default if default is not None else value
