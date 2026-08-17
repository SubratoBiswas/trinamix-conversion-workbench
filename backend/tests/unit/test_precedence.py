"""Unit tests for the precedence policy (Phase 4 core extraction).

Pins the exact "whichever is latest" decisions the three former sites made
(_conversion_rule_wins, the _person_is_newer guard, directive_for's exact/wide tiebreak),
so the consolidated policy cannot drift. Pure and fast.
"""
from datetime import datetime
from app.domain.precedence.policy import (
    conversion_rule_wins, person_is_newer, wide_directive_wins)

OLD = datetime(2026, 7, 13)   # a dated directive
MID = datetime(2026, 8, 3)
NEW = datetime(2026, 8, 17)


def test_conversion_rule_wins():
    assert conversion_rule_wins([], NEW) is False           # no rule -> directive stands
    assert conversion_rule_wins([MID], None) is True         # undated directive loses
    assert conversion_rule_wins([None], OLD) is True         # undated rule is left alone
    assert conversion_rule_wins([NEW], OLD) is True          # newer rule supersedes
    assert conversion_rule_wins([OLD], NEW) is False         # older rule does not
    assert conversion_rule_wins([OLD, NEW], NEW) is True     # any qualifying rule wins
    assert conversion_rule_wins([NEW], NEW) is True          # on-the-day counts (>=)


def test_person_is_newer():
    assert person_is_newer(None, None) is True               # undated directive -> person
    assert person_is_newer(NEW, None) is True
    assert person_is_newer(None, OLD) is False               # no approval date -> loses
    assert person_is_newer(NEW, OLD) is True
    assert person_is_newer(OLD, NEW) is False
    assert person_is_newer(NEW, NEW) is True                 # on-the-day counts (>=)


def test_wide_directive_wins():
    assert wide_directive_wins(OLD, None) is False           # no wide date -> exact wins
    assert wide_directive_wins(None, NEW) is True            # undated exact -> wide wins
    assert wide_directive_wins(OLD, NEW) is True             # newer wide supersedes
    assert wide_directive_wins(NEW, OLD) is False
    assert wide_directive_wins(NEW, NEW) is False            # tie -> exact keeps precision
