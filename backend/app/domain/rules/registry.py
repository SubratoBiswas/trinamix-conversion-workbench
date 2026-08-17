"""Compose the standard rule engine. Adding a migrated rule type = one import + one
register() line here (and its strategy class + differential test)."""
from __future__ import annotations
from app.domain.rules.engine import RuleEngine
from app.domain.rules.library.string_ops import (
    TrimRule, UppercaseRule, LowercaseRule, TitleCaseRule,
    RemoveHyphenRule, RemoveSpecialCharsRule, ReplaceRule, PadRule, SubstringRule,
)
from app.domain.rules.library.regex_ops import RegexReplaceRule, RegexExtractRule
from app.domain.rules.library.value_ops import (
    DefaultValueRule, ConstantRule, ValueMapRule,
)


def standard_rule_engine() -> RuleEngine:
    eng = RuleEngine()
    for strat in (
        TrimRule(), UppercaseRule(), LowercaseRule(), TitleCaseRule(),
        RemoveHyphenRule(), RemoveSpecialCharsRule(), ReplaceRule(),
        PadRule(), SubstringRule(),
        RegexReplaceRule(), RegexExtractRule(),
        DefaultValueRule(), ConstantRule(), ValueMapRule(),
    ):
        eng.register(strat)
    return eng
