"""The mapping-vs-directive precedence decision (Phase 3, slice 3), pinned.

``mapping_outranks_directive`` is the ``_explicit`` gate the write-time strategy
overlay consults in ``output_service._transform_frame``: when it is True a strategy
``blank`` does not apply and a strategy ``constant`` only fills blanks. It encodes the
analyst's "whichever is latest" rule against one mapping's provenance. These tests are
the plain-language statement of that rule, with no Beanie/Mongo object in sight — the
function takes primitives.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.domain.precedence.policy import mapping_outranks_directive as outranks

D_OLD = datetime(2026, 7, 13)          # a 13-Jul strategy directive
D_MID = datetime(2026, 8, 3, 18, 32)
D_NEW = datetime(2026, 8, 4, 10, 57)   # a newer analyst approval


def _call(**kw):
    base = dict(status="approved", approved_by="alice", approved_at=D_NEW,
                source_column="City", default_value=None,
                authored_rule_asofs=[], directive_asof=D_OLD)
    base.update(kw)
    return outranks(**base)


# --- the person-set-value path ----------------------------------------------

def test_approved_source_column_newer_than_directive_wins():
    assert _call() is True


def test_typed_constant_counts_as_a_person_set_value():
    # Receipt Routing = "3" typed into the Fixed-value box, source_column null.
    assert _call(source_column=None, default_value="3") is True


def test_a_suggestion_does_not_count():
    assert _call(status="suggested") is False
    assert _call(status="rejected") is False
    assert _call(status="not_applicable") is False


def test_a_dated_learning_engine_approval_still_wins_on_date():
    # Authorship is provenance, the date decides — an engine approval newer than the
    # directive wins; it does not lose merely for being stamped "learning-engine".
    assert _call(approved_by="learning-engine", approved_at=D_NEW) is True


def test_an_undated_approval_cannot_beat_a_dated_directive():
    assert _call(approved_at=None) is False


def test_an_undated_directive_yields_to_any_human_approval():
    assert _call(approved_at=None, directive_asof=None) is True


def test_an_approval_older_than_the_directive_loses():
    assert _call(approved_at=D_OLD, directive_asof=D_NEW) is False


def test_nothing_set_does_not_outrank():
    assert _call(source_column=None, default_value=None,
                 approved_by=None, approved_at=None) is False


# --- the authored-rule path (independent of approval status) ----------------

def test_an_authored_rule_newer_than_the_directive_wins_even_when_suggested():
    assert outranks(status="suggested", approved_by=None, approved_at=None,
                    source_column=None, default_value=None,
                    authored_rule_asofs=[D_NEW], directive_asof=D_OLD) is True


def test_an_authored_rule_older_than_the_directive_loses():
    assert outranks(status="suggested", approved_by=None, approved_at=None,
                    source_column=None, default_value=None,
                    authored_rule_asofs=[D_OLD], directive_asof=D_NEW) is False


def test_an_undated_authored_rule_beats_an_undated_directive():
    # An undated directive cannot be shown newer than anything, so a rule beats it.
    assert outranks(status="suggested", approved_by=None, approved_at=None,
                    source_column=None, default_value=None,
                    authored_rule_asofs=[None], directive_asof=None) is True


def test_no_authored_rules_is_not_a_win_by_itself():
    assert outranks(status="suggested", approved_by=None, approved_at=None,
                    source_column=None, default_value=None,
                    authored_rule_asofs=[], directive_asof=None) is False
