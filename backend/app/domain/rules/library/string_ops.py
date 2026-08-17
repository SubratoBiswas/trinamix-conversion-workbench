"""String-transform rule strategies — the first branches migrated out of
engine._apply_one_rule. Each reproduces its former branch exactly (proved by the Phase 1c
differential test); they depend only on the value, the config, and the domain text
helpers, so they carry no engine coupling."""
from __future__ import annotations
import re
from typing import Any
from app.domain.text import to_str


class TrimRule:
    rule_type = "TRIM"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        return to_str(value).strip()


class UppercaseRule:
    rule_type = "UPPERCASE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        return to_str(value).upper()


class LowercaseRule:
    rule_type = "LOWERCASE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        return to_str(value).lower()


class TitleCaseRule:
    rule_type = "TITLE_CASE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        return to_str(value).title()


class RemoveHyphenRule:
    rule_type = "REMOVE_HYPHEN"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        return to_str(value).replace("-", "")


class RemoveSpecialCharsRule:
    rule_type = "REMOVE_SPECIAL_CHARS"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        keep = config.get("keep", "")
        pattern = re.compile(rf"[^A-Za-z0-9{re.escape(keep)} ]")
        return pattern.sub("", to_str(value))


class ReplaceRule:
    rule_type = "REPLACE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        return to_str(value).replace(config.get("find", ""), config.get("replace", ""))


class PadRule:
    rule_type = "PAD"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        side = (config.get("side") or "left").lower()
        length = int(config.get("length", 0))
        char = (config.get("char") or "0")[:1] or "0"
        s = to_str(value)
        if length <= 0 or len(s) >= length:
            return s
        return s.rjust(length, char) if side == "left" else s.ljust(length, char)


class SubstringRule:
    rule_type = "SUBSTRING"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        s = to_str(value)
        start = int(config.get("start", 0))
        length = config.get("length")
        if length is None or length == "":
            return s[start:]
        try:
            length = int(length)
        except (TypeError, ValueError):
            return s
        return s[start : start + length]


class SplitRule:
    rule_type = "SPLIT"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        sep = config.get("separator", " ")
        idx = int(config.get("index", 0))
        parts = to_str(value).split(sep)
        return parts[idx] if 0 <= idx < len(parts) else value
