"""The one dated store — the resolver, against a table of competing entries.

Agreed with the analyst, 02-Aug-2026: one store, keyed (client, source system,
target field); the workbook, the gold standard, a learning, the steer box, a
grid edit and a custom rule are all just dated entries; newest wins; authorship
is provenance only.

These tests are pure. They build competing entries by hand and ask the resolver
who wins, because that question is the whole of the domain rule and it should be
answerable without a database.
"""
from datetime import datetime

import pytest

from app.services import mapping_store as store
from app.services.mapping_store import (
    DEFAULT_VALUE, RULE, SOURCE_COLUMN, SUPPRESS, Entry, resolve, resolve_all,
)

CLIENT = "6650f0000000000000000001"
OTHER_CLIENT = "6650f0000000000000000002"


class Row:
    """A stand-in for a stored LearnedMapping, with only the fields that matter."""

    def __init__(self, **kw):
        self.id = kw.pop("id", None)
        self.kind = kw.pop("kind", "column_mapping")
        self.target_field = kw.pop("target_field", "Supplier Name")
        self.target_object = kw.pop("target_object", None)
        self.original_value = kw.pop("original_value", None)
        self.resolved_value = kw.pop("resolved_value", None)
        self.rule_type = kw.pop("rule_type", None)
        self.rule_config = kw.pop("rule_config", None)
        self.client_id = kw.pop("client_id", None)
        self.source_erp = kw.pop("source_erp", None)
        self.effective_date = kw.pop("effective_date", None)
        self.captured_at = kw.pop("captured_at", None)
        self.captured_from = kw.pop("captured_from", None)
        self.captured_by = kw.pop("captured_by", None)
        self.sheets = kw.pop("sheets", [])
        self.exclude_sheets = kw.pop("exclude_sheets", [])
        self.is_deleted = kw.pop("is_deleted", False)
        assert not kw, f"unexpected {kw}"


def d(day, month=7, year=2026, hour=0):
    return datetime(year, month, day, hour)


def alias(src, *, on, **kw):
    return Row(kind="column_mapping", original_value=src, resolved_value="x",
               effective_date=on, **kw)


def default(value, *, on, **kw):
    return Row(kind="example_default", original_value="(default)",
               resolved_value=value, rule_config={"default_value": value},
               effective_date=on, **kw)


def suppress(*, on, **kw):
    return Row(kind="suppress_field", original_value="(blank)", resolved_value="",
               rule_type="suppress", effective_date=on, **kw)


# ── The rule ─────────────────────────────────────────────────────────────────

def test_the_newest_entry_wins():
    won = resolve([alias("VENDOR_NAME", on=d(13)),
                   alias("Legal Name", on=d(31))],
                  target_field="Supplier Name")
    assert won.value == "Legal Name"


def test_the_newest_entry_wins_no_matter_which_order_they_arrive_in():
    entries = [alias("Legal Name", on=d(31)), alias("VENDOR_NAME", on=d(13))]
    assert resolve(entries, target_field="Supplier Name").value == "Legal Name"
    assert resolve(entries[::-1], target_field="Supplier Name").value == "Legal Name"


@pytest.mark.parametrize("captured_from", [
    "NXT supplier field mapping doc",   # the mapping workbook
    "gold example",                     # the gold standard
    "auto-capture",                     # a learning
    "prompt",                           # the steer box
    "grid",                             # a grid edit
    "plain-English rule: blank it",     # a custom rule
])
def test_every_source_is_equal_and_only_the_date_decides(captured_from):
    """Workbook, gold, learning, steer, grid edit, custom rule — all just entries.

    Whichever of them spoke last wins, and it wins whether it is the one being
    compared against or the one being compared to. Authorship is provenance.
    """
    older = alias("OLD", on=d(13), captured_from=captured_from)
    newer = alias("NEW", on=d(31), captured_from="something else entirely")
    assert resolve([older, newer], target_field="Supplier Name").value == "NEW"

    older = alias("OLD", on=d(13), captured_from="something else entirely")
    newer = alias("NEW", on=d(31), captured_from=captured_from)
    assert resolve([older, newer], target_field="Supplier Name").value == "NEW"


def test_an_analyst_grid_edit_beats_the_library_when_it_is_later():
    """"That's fine, the analyst mapping wins as that's the latest mapping as
    per date." — the analyst, on per-conversion overrides."""
    workbook = alias("VENDOR_NAME", on=d(31), captured_from="mapping workbook")
    edit = alias("Legal Name", on=d(2, month=8), captured_from="grid",
                 captured_by="analyst@nextpower.com")
    assert resolve([workbook, edit], target_field="Supplier Name").value == "Legal Name"


def test_and_loses_when_the_library_is_later_still():
    """The same rule in the other direction. A person's edit is not permanent —
    it stands until the client says something newer, which is the point of
    dating it rather than ranking who said it."""
    edit = alias("Legal Name", on=d(31), captured_from="grid",
                 captured_by="analyst@nextpower.com")
    workbook = alias("VENDOR_NAME", on=d(2, month=8), captured_from="mapping workbook")
    assert resolve([edit, workbook], target_field="Supplier Name").value == "VENDOR_NAME"


def test_a_suppression_and_a_mapping_compete_on_the_same_terms():
    """Not two kinds of thing to be ranked — two statements about one field."""
    won = resolve([alias("Legal Name", on=d(13)), suppress(on=d(31))],
                  target_field="Supplier Name")
    assert won.decision == SUPPRESS

    won = resolve([suppress(on=d(13)), alias("Legal Name", on=d(31))],
                  target_field="Supplier Name")
    assert won.decision == SOURCE_COLUMN and won.value == "Legal Name"


def test_a_default_can_take_over_from_a_column_and_hand_back():
    won = resolve([alias("Legal Name", on=d(13)), default("PRIMARY", on=d(20))],
                  target_field="Supplier Name")
    assert (won.decision, won.value) == (DEFAULT_VALUE, "PRIMARY")

    won = resolve([alias("Legal Name", on=d(13)), default("PRIMARY", on=d(20)),
                   alias("VENDOR_NAME", on=d(31))], target_field="Supplier Name")
    assert (won.decision, won.value) == (SOURCE_COLUMN, "VENDOR_NAME")


# ── Dates ────────────────────────────────────────────────────────────────────

def test_an_undated_decision_counts_as_older():
    """It cannot be shown to have come later, and reading it as newer is what
    made corrections vanish."""
    undated = Row(kind="column_mapping", original_value="GUESS",
                  resolved_value="x", effective_date=None, captured_at=None)
    dated = alias("Legal Name", on=d(13))
    assert resolve([undated, dated], target_field="Supplier Name").value == "Legal Name"


def test_captured_at_is_the_fallback_when_there_is_no_effective_date():
    typed_in_the_ui = Row(kind="column_mapping", original_value="Legal Name",
                          resolved_value="x", effective_date=None,
                          captured_at=d(31))
    seeded = alias("VENDOR_NAME", on=d(13))
    assert resolve([typed_in_the_ui, seeded],
                   target_field="Supplier Name").value == "Legal Name"


def test_effective_date_is_preferred_over_captured_at():
    """A startup seed re-stamps captured_at on every boot. Ordering on it
    inverts the whole precedence after a redeploy, which is why the instruction
    date is a separate field and why it is the one that is read."""
    reseeded_today = alias("OLD", on=d(13))
    reseeded_today.captured_at = d(3, month=8)          # what the redeploy wrote
    analyst = alias("Legal Name", on=d(31))
    analyst.captured_at = d(31)
    assert resolve([reseeded_today, analyst],
                   target_field="Supplier Name").value == "Legal Name"


def test_a_tie_is_broken_the_same_way_every_time():
    a = alias("A", on=d(31), id="aaa")
    b = alias("B", on=d(31), id="bbb")
    assert resolve([a, b], target_field="Supplier Name").value == \
           resolve([b, a], target_field="Supplier Name").value


def test_a_same_day_statement_about_this_client_beats_one_about_everybody():
    """Only ever a tie-break. It never overrides a later date."""
    everybody = alias("GENERIC", on=d(31))
    this_client = alias("Legal Name", on=d(31), client_id=CLIENT)
    won = resolve([everybody, this_client], target_field="Supplier Name",
                  client_id=CLIENT)
    assert won.value == "Legal Name"

    everybody_later = alias("GENERIC", on=d(2, month=8))
    won = resolve([everybody_later, this_client], target_field="Supplier Name",
                  client_id=CLIENT)
    assert won.value == "GENERIC"


# ── The key: client, source system, target field ─────────────────────────────

def test_another_clients_decision_is_not_this_clients_decision():
    theirs = alias("THEIR_COLUMN", on=d(31), client_id=OTHER_CLIENT)
    mine = alias("Legal Name", on=d(13), client_id=CLIENT)
    assert resolve([theirs, mine], target_field="Supplier Name",
                   client_id=CLIENT).value == "Legal Name"


def test_an_untagged_decision_applies_to_every_client():
    """That is the entire library captured before client scoping existed, so
    filtering it out would silently strand it."""
    won = resolve([alias("Legal Name", on=d(31))], target_field="Supplier Name",
                  client_id=CLIENT)
    assert won is not None and won.value == "Legal Name"


def test_a_netsuite_decision_is_not_a_syteline_decision():
    """The same target field is fed by a different column depending on which
    legacy system the extract came from."""
    netsuite = alias("Legal Name", on=d(31), source_erp="netsuite")
    syteline = alias("VENDNAME", on=d(13), source_erp="syteline")
    assert resolve([netsuite, syteline], target_field="Supplier Name",
                   source_erp="syteline").value == "VENDNAME"
    assert resolve([netsuite, syteline], target_field="Supplier Name",
                   source_erp="netsuite").value == "Legal Name"


def test_source_system_names_are_matched_case_and_punctuation_insensitively():
    won = resolve([alias("Legal Name", on=d(31), source_erp="NetSuite")],
                  target_field="Supplier Name", source_erp="netsuite")
    assert won is not None


def test_the_field_name_is_matched_however_it_is_spelled():
    row = alias("Legal Name", on=d(31))
    row.target_field = "PARTY_ORIGINAL_SYSTEM_REFERENCE"
    for spelling in ("Party Original System Reference",
                     "PartyOriginalSystemReference",
                     "party original system reference"):
        assert resolve([row], target_field=spelling) is not None


def test_a_decision_about_a_different_field_is_not_an_answer():
    assert resolve([alias("Legal Name", on=d(31))],
                   target_field="Supplier Number") is None


def test_nobody_has_said_anything():
    assert resolve([], target_field="Supplier Name") is None


# ── No object scope ──────────────────────────────────────────────────────────

def test_the_object_a_decision_was_captured_under_does_not_narrow_it():
    """"An edit is the client's newest statement about that field and applies
    everywhere." The object is recorded as provenance and read by nobody."""
    captured_on_supplier = alias("Legal Name", on=d(31), target_object="Supplier")
    won = resolve([captured_on_supplier], target_field="Supplier Name")
    assert won is not None and won.target_object == "Supplier"


def test_an_object_spelling_cannot_hide_a_decision():
    """Learnings written under the template's business_object and read under the
    conversion's target_object was a real way for a decision to disappear. With
    no object in the key there is nothing left to spell differently."""
    rows = [alias("Legal Name", on=d(31), target_object=spelling)
            for spelling in ("Supplier", "supplier", "Supplier_Import",
                             "SUPPLIER IMPORT", None)]
    for row in rows:
        assert resolve([row], target_field="Supplier Name") is not None


def test_the_newest_wins_across_objects_too():
    on_customer = alias("Party Name", on=d(2, month=8), target_object="Customer",
                        target_field="Address Line 1")
    on_supplier = alias("Legal Name", on=d(31), target_object="Supplier",
                        target_field="Address Line 1")
    assert resolve([on_supplier, on_customer],
                   target_field="Address Line 1").value == "Party Name"


# ── Sheets ───────────────────────────────────────────────────────────────────

def test_a_sheet_the_analyst_excluded_does_not_get_the_decision():
    """Part of what the analyst said, not a competing tier: "id maps to Party
    Original System Reference, but not on HZ_IMP_CLASSIFICS_T" is one
    instruction."""
    row = alias("id", on=d(31), exclude_sheets=["HZ_IMP_CLASSIFICS_T"])
    assert resolve([row], target_field="Supplier Name",
                   sheet="HZ_IMP_PARTIES_T") is not None
    assert resolve([row], target_field="Supplier Name",
                   sheet="HZ_IMP_CLASSIFICS_T") is None


def test_a_sheet_allow_list_keeps_the_decision_off_every_other_sheet():
    row = alias("id", on=d(31), sheets=["HZ_IMP_PARTIES_T"])
    assert resolve([row], target_field="Supplier Name",
                   sheet="HZ_IMP_PARTIES_T") is not None
    assert resolve([row], target_field="Supplier Name",
                   sheet="HZ_IMP_CLASSIFICS_T") is None


def test_excluding_a_sheet_beats_naming_it():
    row = alias("id", on=d(31), sheets=["A", "B"], exclude_sheets=["B"])
    assert resolve([row], target_field="Supplier Name", sheet="A") is not None
    assert resolve([row], target_field="Supplier Name", sheet="B") is None


def test_a_scoped_decision_still_loses_to_a_newer_unscoped_one_on_its_own_sheet():
    scoped = alias("id", on=d(13), sheets=["HZ_IMP_PARTIES_T"])
    newer = alias("PartyId", on=d(31))
    assert resolve([scoped, newer], target_field="Supplier Name",
                   sheet="HZ_IMP_PARTIES_T").value == "PartyId"


# ── Tombstones ───────────────────────────────────────────────────────────────

def test_a_retired_decision_is_not_a_decision():
    retired = alias("Legal Name", on=d(2, month=8), is_deleted=True)
    older = alias("VENDOR_NAME", on=d(13))
    assert resolve([retired, older],
                   target_field="Supplier Name").value == "VENDOR_NAME"
    assert resolve([retired], target_field="Supplier Name") is None


# ── Reading the value ────────────────────────────────────────────────────────

def test_a_default_reads_its_constant_from_the_rule_config_then_the_row():
    from_config = default("PRIMARY", on=d(31))
    assert store.value_of(from_config) == "PRIMARY"
    legacy = Row(kind="example_default", original_value="(constant)",
                 resolved_value="900001", rule_config={}, effective_date=d(31))
    assert store.value_of(legacy) == "900001"


def test_a_suppression_has_nothing_to_write():
    assert store.value_of(suppress(on=d(31))) is None


def test_a_rule_entry_carries_its_type_and_config():
    row = Row(kind="rule", original_value="Country", resolved_value="Country Code",
              rule_type="VALUE_MAP", rule_config={"map": {"US": "USA"}},
              effective_date=d(31))
    won = resolve([row], target_field="Supplier Name")
    assert won.decision == RULE
    assert won.rule_type == "VALUE_MAP"
    assert won.rule_config == {"map": {"US": "USA"}}
    assert won.value == "Country"


def test_kinds_that_are_not_statements_about_a_field_are_not_entries():
    """crosswalk is one row per source VALUE, file_signature identifies an
    uploaded file, ignore_source is about a source column. None of them is an
    answer to "what should this target field be"."""
    for kind in ("crosswalk", "file_signature", "ignore_source",
                 "reference_standard"):
        assert store.entry_of(Row(kind=kind)) is None


def test_a_row_with_no_target_field_is_not_an_entry():
    assert store.entry_of(Row(kind="column_mapping", target_field=None)) is None


# ── Resolving a whole sheet at once ──────────────────────────────────────────

def test_resolve_all_answers_every_field_from_one_read():
    name_old = alias("VENDOR_NAME", on=d(13))
    name_new = alias("Legal Name", on=d(31))
    number = alias("VENDOR_ID", on=d(20))
    number.target_field = "Supplier Number"
    blank = suppress(on=d(31))
    blank.target_field = "Delivery Channel"

    won = resolve_all([name_old, name_new, number, blank])
    assert won[store.normalise_field("Supplier Name")].value == "Legal Name"
    assert won[store.normalise_field("Supplier Number")].value == "VENDOR_ID"
    assert won[store.normalise_field("Delivery Channel")].decision == SUPPRESS


def test_resolve_all_and_resolve_never_disagree():
    """Two readers of one store that can differ is the failure this whole
    change exists to end, so the batch answer is asserted against the single
    answer rather than assumed to match it."""
    rows = [alias("A", on=d(13)), alias("B", on=d(31)), suppress(on=d(20)),
            default("X", on=d(2, month=8), client_id=CLIENT),
            alias("C", on=d(31), source_erp="netsuite")]
    for source in (None, "netsuite", "syteline"):
        for client in (None, CLIENT, OTHER_CLIENT):
            batch = resolve_all(rows, client_id=client, source_erp=source)
            one = resolve(rows, target_field="Supplier Name", client_id=client,
                          source_erp=source)
            key = store.normalise_field("Supplier Name")
            assert (batch.get(key).value if batch.get(key) else None) == \
                   (one.value if one else None)


def test_resolve_all_can_be_asked_about_only_the_fields_on_this_sheet():
    other = alias("VENDOR_ID", on=d(31))
    other.target_field = "Supplier Number"
    won = resolve_all([alias("Legal Name", on=d(31)), other],
                      target_fields=["Supplier Name"])
    assert set(won) == {store.normalise_field("Supplier Name")}


# ── A per-conversion row read as an entry ────────────────────────────────────

class Mapping:
    def __init__(self, **kw):
        self.source_column = kw.pop("source_column", None)
        self.default_value = kw.pop("default_value", None)
        self.status = kw.pop("status", "approved")
        self.approved_by = kw.pop("approved_by", "analyst@nextpower.com")
        self.approved_at = kw.pop("approved_at", d(31))
        self.suggested_transformation = kw.pop("suggested_transformation", None)
        assert not kw, f"unexpected {kw}"


def _from(mapping):
    return store.entry_from_mapping(mapping, target_field="Supplier Name")


def test_a_persons_grid_edit_becomes_an_entry_carrying_its_own_date():
    entry = _from(Mapping(source_column="Legal Name", approved_at=d(2, month=8)))
    assert entry.decision == SOURCE_COLUMN
    assert entry.value == "Legal Name"
    assert entry.effective_date == d(2, month=8)
    assert entry.captured_by == "analyst@nextpower.com"


def test_a_row_the_engine_wrote_is_not_a_statement():
    """It is a copy of one. Reading it back would let the store's own output
    re-enter the store and outrank the thing it was copied from."""
    assert _from(Mapping(source_column="Legal Name",
                         approved_by=store.ENGINE)) is None
    assert _from(Mapping(source_column="Legal Name", approved_by=None)) is None


def test_keep_blank_becomes_a_suppression():
    assert _from(Mapping(status="not_applicable", source_column=None)).decision \
           == SUPPRESS


def test_keep_blank_with_a_constant_is_a_default_not_a_suppression():
    entry = _from(Mapping(status="not_applicable", default_value="900001"))
    assert (entry.decision, entry.value) == (DEFAULT_VALUE, "900001")


def test_a_rejected_suggestion_with_no_replacement_says_nothing():
    assert _from(Mapping(status="rejected", source_column=None)) is None


def test_a_grid_edit_with_no_timestamp_counts_as_older():
    undated = _from(Mapping(source_column="Legal Name", approved_at=None))
    assert resolve([undated, alias("VENDOR_NAME", on=d(13))],
                   target_field="Supplier Name").value == "VENDOR_NAME"


def test_a_grid_edit_competes_with_the_library_on_date_alone():
    edit = _from(Mapping(source_column="Legal Name", approved_at=d(2, month=8)))
    library = alias("VENDOR_NAME", on=d(31))
    assert resolve([library, edit], target_field="Supplier Name").value == "Legal Name"
    later_library = alias("VENDOR_NAME", on=d(3, month=8))
    assert resolve([later_library, edit],
                   target_field="Supplier Name").value == "VENDOR_NAME"


def test_an_entry_knows_its_own_key():
    entry = store.entry_of(alias("Legal Name", on=d(31), client_id=CLIENT,
                                 source_erp="NetSuite"))
    assert entry.key == (CLIENT, "netsuite", "suppliername")
