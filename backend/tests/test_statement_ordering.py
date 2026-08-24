"""The unified precedence ordering (Phase 3 slice 5), pinned.

All four write-time contests now delegate to one ``pick_latest`` over a ``Statement``.
These tests pin the ordering itself and the three tie-break/undated terms that the
contests used to spell with scattered ``>=`` / ``>``, plus each public function against
its documented rule. Pure — no database, no framework.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.domain.precedence import policy as P
from app.domain.precedence.policy import Statement, pick_latest, CONVERSION, EXACT, WIDE

T1 = datetime(2026, 7, 13)
T2 = datetime(2026, 8, 3, 18, 32)
T2B = datetime(2026, 8, 3, 18, 32)   # a genuinely equal instant
T3 = datetime(2026, 8, 4, 10, 57)


# --- pick_latest core --------------------------------------------------------

def test_newest_wins():
    a = Statement(T1, CONVERSION)
    b = Statement(T3, WIDE)
    assert pick_latest((a, b)) is b


def test_same_instant_more_specific_wins():
    # conversion outranks a directive on a tie ( the overlay's ">=" )
    conv = Statement(T2, CONVERSION)
    drv = Statement(T2B, WIDE)
    assert pick_latest((conv, drv)) is conv
    # exact directive outranks a wide one on a tie ( the directive tiebreak's exact-wins )
    exact = Statement(T2, EXACT)
    wide = Statement(T2B, WIDE)
    assert pick_latest((exact, wide)) is exact


def test_undated_conversion_rule_is_treated_as_newest():
    rule = Statement(None, CONVERSION, undated_is_newest=True)
    dated_directive = Statement(T3, WIDE)
    assert pick_latest((rule, dated_directive)) is rule


def test_undated_without_the_flag_is_oldest():
    undated = Statement(None, CONVERSION)          # e.g. an approval with no timestamp
    dated = Statement(T1, WIDE)
    assert pick_latest((undated, dated)) is dated


def test_empty_pool_is_none():
    assert pick_latest(()) is None


# --- wide_directive_wins -----------------------------------------------------

def test_wide_wins_only_when_strictly_newer():
    assert P.wide_directive_wins(T2, T3) is True          # wide newer -> wide
    assert P.wide_directive_wins(T2, T2B) is False         # tie -> exact wins
    assert P.wide_directive_wins(T3, T1) is False          # exact newer -> exact
    assert P.wide_directive_wins(None, T1) is True         # exact undated -> wide
    assert P.wide_directive_wins(T1, None) is False        # wide undated -> exact
    assert P.wide_directive_wins(None, None) is False      # neither -> exact


# --- person_is_newer ---------------------------------------------------------

def test_person_is_newer_ge_and_undated():
    assert P.person_is_newer(T3, T2) is True               # later approval wins
    assert P.person_is_newer(T2, T2B) is True              # SAME instant -> person wins (>=)
    assert P.person_is_newer(T1, T3) is False              # older approval loses
    assert P.person_is_newer(None, T1) is False            # undated approval loses
    assert P.person_is_newer(None, None) is True           # undated directive -> person wins
    assert P.person_is_newer(T1, None) is True             # undated directive -> person wins


# --- conversion_rule_wins ----------------------------------------------------

def test_conversion_rule_wins_rules_and_undated():
    assert P.conversion_rule_wins([], T1) is False         # no rule
    assert P.conversion_rule_wins([T3], T2) is True        # newer rule wins
    assert P.conversion_rule_wins([T2], T2B) is True       # same instant -> rule wins (>=)
    assert P.conversion_rule_wins([T1], T3) is False       # older rule loses
    assert P.conversion_rule_wins([None], T3) is True      # undated rule is left alone -> wins
    assert P.conversion_rule_wins([T1, T3], T2) is True    # any qualifying rule wins
    assert P.conversion_rule_wins([T1], None) is True      # undated directive -> rule wins


# --- mapping_outranks_directive ----------------------------------------------

def _call(**kw):
    base = dict(status="approved", approved_by="alice", approved_at=T3, source_column="City",
                default_value=None, authored_rule_asofs=[], directive_asof=T1)
    base.update(kw)
    return P.mapping_outranks_directive(**base)


def test_mapping_person_value_newer_outranks():
    assert _call() is True                                   # approved, valued, newer


def test_mapping_a_suggestion_does_not_outrank():
    assert _call(status="suggested") is False               # gate: not approved/overridden


def test_mapping_needs_a_value_or_a_rule():
    assert _call(source_column="", default_value=None, authored_rule_asofs=[]) is False
    # a typed fixed value counts as much as a bound column
    assert _call(source_column="", default_value="3") is True


def test_mapping_older_approval_loses_but_an_authored_rule_can_still_win():
    assert _call(approved_at=T1, directive_asof=T3) is False           # approval older
    assert _call(approved_at=T1, directive_asof=T3, authored_rule_asofs=[T3]) is True


def test_mapping_dated_engine_approval_still_wins_on_its_date():
    # authorship is provenance, not the decider: a dated learning-engine approval wins
    assert _call(approved_by="learning-engine") is True
    # ...but an undated engine approval cannot be shown newer
    assert _call(approved_by="learning-engine", approved_at=None, directive_asof=T1) is False
