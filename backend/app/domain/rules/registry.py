"""Compose the standard rule engine. Adding a migrated rule type = one import + one
register() line here (and its strategy class + differential test)."""
from __future__ import annotations
from app.domain.rules.engine import RuleEngine
from app.domain.rules.library.string_ops import (
    TrimRule, UppercaseRule, LowercaseRule, TitleCaseRule,
    RemoveHyphenRule, RemoveSpecialCharsRule, ReplaceRule,
)


def standard_rule_engine() -> RuleEngine:
    return (RuleEngine()
            .register(TrimRule())
            .register(UppercaseRule())
            .register(LowercaseRule())
            .register(TitleCaseRule())
            .register(RemoveHyphenRule())
            .register(RemoveSpecialCharsRule())
            .register(ReplaceRule()))
