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
