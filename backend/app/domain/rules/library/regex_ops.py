"""Regex rule strategies migrated out of engine._apply_one_rule. Self-contained: value +
config + re. Each reproduces its former branch verbatim (Phase 1c differential test)."""
from __future__ import annotations
import re
from typing import Any
from app.domain.text import to_str


class RegexReplaceRule:
    rule_type = "REGEX_REPLACE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        pattern = config.get("pattern", "")
        # Accept both "replace" and "replacement" (the authoring UI/translator have
        # emitted either); reading both keeps them behaving the same.
        repl = config.get("replace")
        if repl is None:
            repl = config.get("replacement", "")
        flags_s = config.get("flags", "") or ""
        flags = 0
        if "i" in flags_s.lower():
            flags |= re.IGNORECASE
        if "m" in flags_s.lower():
            flags |= re.MULTILINE
        try:
            return re.sub(pattern, repl, to_str(value), flags=flags)
        except re.error:
            return value


class RegexExtractRule:
    rule_type = "REGEX_EXTRACT"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        pattern = config.get("pattern", "")
        group = int(config.get("group", 0))
        try:
            m = re.search(pattern, to_str(value))
        except re.error:
            return value
        if not m:
            return config.get("default", "")
        try:
            return m.group(group)
        except IndexError:
            return config.get("default", "")
