"""Compose the standard rule engine. Adding a migrated rule type = one import + one
register() line here (and its strategy class + differential test)."""
from __future__ import annotations
from app.domain.rules.engine import RuleEngine
from app.domain.rules.library.string_ops import (
    TrimRule, UppercaseRule, LowercaseRule, TitleCaseRule,
    RemoveHyphenRule, RemoveSpecialCharsRule, ReplaceRule, PadRule, SubstringRule, SplitRule,
)
from app.domain.rules.library.regex_ops import RegexReplaceRule, RegexExtractRule
from app.domain.rules.library.value_ops import (
    DefaultValueRule, ConstantRule, ValueMapRule, MapBooleanRule,
)
from app.domain.rules.library.numeric_ops import NumberFormatRule, ArithmeticRule
from app.domain.rules.library.stateful_ops import (
    ConcatRule, CoalesceRule, BlankIfEqualsRule, ConditionalRule, CaseWhenRule,
    PrefixRule, SuffixRule, SuffixWhenRule,
)
from app.domain.rules.library.date_ops import (
    FormatDateRule, DateFormatRule, ConditionalDateRule, ComputedRule,
)


def standard_rule_engine() -> RuleEngine:
    eng = RuleEngine()
    for strat in (
        TrimRule(), UppercaseRule(), LowercaseRule(), TitleCaseRule(),
        RemoveHyphenRule(), RemoveSpecialCharsRule(), ReplaceRule(),
        PadRule(), SubstringRule(), SplitRule(),
        RegexReplaceRule(), RegexExtractRule(),
        DefaultValueRule(), ConstantRule(), ValueMapRule(), MapBooleanRule(),
        NumberFormatRule(), ArithmeticRule(),
        ConcatRule(), CoalesceRule(), BlankIfEqualsRule(), ConditionalRule(),
        CaseWhenRule(), PrefixRule(), SuffixRule(), SuffixWhenRule(),
        FormatDateRule(), DateFormatRule(), ConditionalDateRule(), ComputedRule(),
    ):
        eng.register(strat)
    return eng
