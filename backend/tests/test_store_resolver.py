"""The dated mapping store's read/resolve core (Phase 3, slice 4), pinned.

The whole of the analyst's "one key, one date, one winner" rule now lives in
``app.domain.store.resolver`` — pure, so it is tested here against a table of
competing entries with no database. A row is any object with the stored attributes;
``types.SimpleNamespace`` stands in for a ``LearnedMapping``.
"""
import os
import sys
from datetime import datetime
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.domain.store import resolver as R

D1 = datetime(2026, 7, 13)
D2 = datetime(2026, 8, 3, 18, 32)
D3 = datetime(2026, 8, 4, 10, 57)
TF = "Party Original System Reference"


def _row(**kw):
    base = dict(kind="column_mapping", target_field=TF, client_id=None, source_erp=None,
                effective_date=None, captured_at=None, original_value="entityid",
                resolved_value=None, rule_config={}, rule_type=None, sheets=[],
                exclude_sheets=[], is_deleted=False, id="r")
    base.update(kw)
    return NS(**base)


# --- keys --------------------------------------------------------------------

def test_field_name_variants_are_one_key():
    assert (R.normalise_field("Party Original System Reference")
            == R.normalise_field("PartyOriginalSystemReference")
            == R.normalise_field("PARTY_ORIGINAL_SYSTEM_REFERENCE"))


def test_source_and_client_keys():
    assert R.normalise_source("NetSuite") == R.normalise_source("netsuite")
    assert R.client_key(None) == "" and R.client_key(7) == "7"


# --- entry construction ------------------------------------------------------

def test_entry_of_reads_a_decision_row():
    e = R.entry_of(_row(kind="column_mapping", original_value="entityid"))
    assert e is not None and e.decision == R.SOURCE_COLUMN and e.value == "entityid"


def test_entry_of_ignores_non_decision_kinds():
    assert R.entry_of(_row(kind="crosswalk")) is None
    assert R.entry_of(_row(kind="file_signature")) is None
    assert R.entry_of(_row(target_field=None)) is None


def test_effective_of_prefers_effective_date_then_captured_then_min():
    assert R.effective_of(_row(effective_date=D2, captured_at=D1)) == D2
    assert R.effective_of(_row(effective_date=None, captured_at=D1)) == D1
    assert R.effective_of(_row(effective_date=None, captured_at=None)) == datetime.min


def test_value_of_by_decision():
    assert R.value_of(_row(kind="column_mapping", original_value="city")) == "city"
    assert R.value_of(_row(kind="suppress_field")) is None
    assert R.value_of(_row(kind="example_default", rule_config={"default_value": "PRIMARY"})) == "PRIMARY"


# --- applicability -----------------------------------------------------------

def test_sheet_allowed_empty_lists_mean_every_sheet():
    assert R.sheet_allowed(_row(), "HZ_IMP_PARTIES_T") is True


def test_sheet_allowed_exclusion_beats_inclusion():
    r = _row(sheets=["HZ_IMP_PARTIES_T"], exclude_sheets=["HZ_IMP_PARTIES_T"])
    assert R.sheet_allowed(r, "HZ_IMP_PARTIES_T") is False


def test_sheet_allowed_include_list_refuses_others():
    r = _row(sheets=["HZ_IMP_PARTIES_T"])
    assert R.sheet_allowed(r, "HZ_IMP_PARTIES_T") is True
    assert R.sheet_allowed(r, "HZ_IMP_CLASSIFICS_T") is False


def test_applies_narrows_by_client_and_source_and_skips_deleted():
    e = R.entry_of(_row(client_id="C1", source_erp="netsuite"))
    assert R.applies(e, client_id="C1", source_erp="netsuite", target_field=TF) is True
    assert R.applies(e, client_id="C2", source_erp="netsuite", target_field=TF) is False
    assert R.applies(e, client_id="C1", source_erp="arena_ebos", target_field=TF) is False
    gone = R.entry_of(_row(is_deleted=True))
    assert R.applies(gone, target_field=TF) is False


# --- resolution --------------------------------------------------------------

def test_newest_statement_wins():
    old = _row(id="old", effective_date=D1, original_value="OLD")
    new = _row(id="new", effective_date=D3, original_value="NEW")
    w = R.resolve([old, new], target_field=TF)
    assert w.value == "NEW"


def test_undated_entry_loses_to_any_dated_one():
    dated = _row(id="dated", effective_date=D1, original_value="DATED")
    undated = _row(id="undated", effective_date=None, original_value="UNDATED")
    assert R.resolve([undated, dated], target_field=TF).value == "DATED"


def test_same_instant_ship_blank_wins_the_tie():
    val = _row(id="v", kind="example_default", effective_date=D3,
               rule_config={"default_value": "X"})
    blank = _row(id="b", kind="suppress_field", effective_date=D3)
    assert R.resolve([val, blank], target_field=TF).decision == R.SUPPRESS
    # ...but a strictly newer value still wins — the date dominates the tie-break.
    newer = _row(id="n", kind="example_default", effective_date=datetime(2026, 8, 6),
                 rule_config={"default_value": "X"})
    assert R.resolve([blank, newer], target_field=TF).decision == R.DEFAULT_VALUE


def test_same_instant_the_more_specific_client_source_wins():
    generic = _row(id="g", effective_date=D2, original_value="GEN")
    exact = _row(id="e", effective_date=D2, client_id="C1", source_erp="netsuite",
                 original_value="EXACT")
    w = R.resolve([generic, exact], target_field=TF, client_id="C1", source_erp="netsuite")
    assert w.value == "EXACT"


def test_resolve_all_gives_one_winner_per_field():
    a1 = _row(id="a1", target_field="Field A", effective_date=D1, original_value="a-old")
    a2 = _row(id="a2", target_field="Field A", effective_date=D3, original_value="a-new")
    b1 = _row(id="b1", target_field="Field B", effective_date=D2, original_value="b")
    out = R.resolve_all([a1, a2, b1])
    assert out[R.normalise_field("Field A")].value == "a-new"
    assert out[R.normalise_field("Field B")].value == "b"


# --- a per-conversion mapping as a statement ---------------------------------

def test_entry_from_mapping_only_for_a_person():
    engine = NS(status="approved", approved_by="learning-engine", approved_at=D3,
                source_column="City", default_value=None, suggested_transformation=None)
    assert R.entry_from_mapping(engine, target_field=TF) is None
    person = NS(status="approved", approved_by="alice", approved_at=D3,
                source_column="City", default_value=None, suggested_transformation=None)
    e = R.entry_from_mapping(person, target_field=TF)
    assert e is not None and e.decision == R.SOURCE_COLUMN and e.value == "City"


def test_entry_from_mapping_decision_by_shape():
    def m(**kw):
        base = dict(status="approved", approved_by="alice", approved_at=D3,
                    source_column="", default_value=None, suggested_transformation=None)
        base.update(kw)
        return NS(**base)
    assert R.entry_from_mapping(m(status="not_applicable"), target_field=TF).decision == R.SUPPRESS
    assert R.entry_from_mapping(m(source_column="City"), target_field=TF).decision == R.SOURCE_COLUMN
    assert R.entry_from_mapping(m(default_value="3"), target_field=TF).decision == R.DEFAULT_VALUE
    # a rejected suggestion with no replacement is not an instruction
    assert R.entry_from_mapping(m(status="rejected"), target_field=TF) is None
