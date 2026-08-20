"""The directive selection policy (Phase 3, slice 1), pinned as pure decisions.

``app.services.strategy_overlay`` was split into a STORE (reads the JSON files,
builds the caches) and a POLICY (``app.domain.directives.policy`` — decides which
directive applies and what a set of directives implies). The policy takes the
caches as arguments and touches no disk, so every rule below is exercised with a
hand-built cache and no Mongo/Beanie/data-directory in sight.

Each test names the behaviour the generator depends on. If one breaks, the FBDI
output moves, which is the whole thing this refactor promised would not happen.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.domain.directives import policy as P


def _dir(**kw):
    """A directive with sane defaults for the fields the policy reads."""
    d = {"as_of": None, "source_erp": None}
    d.update(kw)
    return d


# --- key normalisation -------------------------------------------------------

def test_norm_strips_to_lower_alnum():
    # object/field keys are matched on this shape, so "Supplier Site" and
    # "supplier_site" and "SupplierSite" are one key.
    assert P.norm("Supplier Site") == P.norm("supplier_site") == "suppliersite"
    assert P.norm(None) == "" and P.norm("  ") == ""


def test_label_is_the_control_default_key_spelling():
    # _apply_control_defaults keys its suppression set lower-cased with a trailing
    # '*' stripped; a blank directive can only reach it in the same spelling.
    assert P.label("Batch ID*") == "batch id"
    assert P.label("  Supplier Name  ") == "supplier name"
    assert P.label(None) == ""


# --- source scoping ----------------------------------------------------------

def test_untagged_directive_applies_to_every_source():
    d = _dir(constant="X")
    assert P.src_ok(d, None) is True
    assert P.src_ok(d, "netsuite") is True
    assert P.src_ok(d, "arena_ebos") is True


def test_tagged_directive_matches_only_its_source_case_insensitively():
    d = _dir(constant="X", source_erp="NetSuite")
    assert P.src_ok(d, "netsuite") is True      # normalised match
    assert P.src_ok(d, "arena_ebos") is False
    # caller source unknown -> a signed rule still applies (safer default)
    assert P.src_ok(d, None) is True


def test_select_directive_drops_a_source_scoped_exact_on_mismatch():
    ex = {"supplier": {"taxid": _dir(constant="US", source_erp="netsuite")}}
    assert P.select_directive(ex, {}, "Supplier", "Tax ID", "netsuite") is not None
    # arena_ebos conversion never sees the NetSuite-only rule
    assert P.select_directive(ex, {}, "Supplier", "Tax ID", "arena_ebos") is None


# --- all-sheets (wild) inheritance & precedence ------------------------------

def test_wild_rule_reaches_prefixed_sheets_only():
    wild = {"supplier": {"batchid": _dir(blank=True)}}
    assert P.prefix_hits("Supplier Site", wild) == ["supplier"]
    assert P.prefix_hits("Customer", wild) == []          # different bundle
    # a field with only a wild rule resolves to it on a Supplier* sheet
    assert P.select_directive({}, wild, "Supplier Site", "Batch ID") is not None
    assert P.select_directive({}, wild, "Customer", "Batch ID") is None


def test_exact_wins_a_tie_but_a_newer_wild_wins():
    import datetime as _dt
    old = _dt.datetime(2026, 7, 13)
    new = _dt.datetime(2026, 7, 30)
    exact = {"suppliersite": {"deliverymethod": _dir(rule={"rule_type": "X"}, as_of=old)}}
    wild = {"supplier": {"deliverymethod": _dir(rule={"rule_type": "Y"}, as_of=new)}}
    # sheet-specific is more precise, but the bundle-wide rule is NEWER -> wild wins
    got = P.select_directive(exact, wild, "Supplier Site", "Delivery Method")
    assert got["rule"]["rule_type"] == "Y"
    # flip the dates: the exact rule is newer, precision keeps it
    exact["suppliersite"]["deliverymethod"]["as_of"] = new
    wild["supplier"]["deliverymethod"]["as_of"] = old
    got = P.select_directive(exact, wild, "Supplier Site", "Delivery Method")
    assert got["rule"]["rule_type"] == "X"


def test_wide_is_exact_identity_returns_that_directive():
    # the store stores one all-sheets directive object under BOTH exact and wild;
    # the policy must not treat it as a conflict to arbitrate.
    d = _dir(rule={"rule_type": "Z"})
    exact = {"supplier": {"batchid": d}}
    wild = {"supplier": {"batchid": d}}
    assert P.select_directive(exact, wild, "Supplier", "Batch ID") is d


def test_missing_object_or_field_is_none():
    ex = {"supplier": {"x": _dir(constant="1")}}
    assert P.select_directive(ex, {}, None, "x") is None
    assert P.select_directive(ex, {}, "Supplier", None) is None
    assert P.select_directive(ex, {}, "Supplier", "absent") is None


# --- blank fields ------------------------------------------------------------

def test_blank_fields_union_object_and_wild_bundle():
    blank = {"supplier": {"batch id"}}          # keyed by exact norm(object)
    wild_blank = {"supplier": {"rfq or bidding"}}
    wild = {"supplier": {"batchid": _dir(blank=True)}}
    # querying the object that owns the set: its own blanks + the wild bundle it inherits
    assert P.blank_fields_for(blank, wild_blank, wild, "Supplier") == {"batch id", "rfq or bidding"}
    # a child sheet inherits ONLY the wild bundle, not the parent's exact set
    assert P.blank_fields_for(blank, wild_blank, wild, "Supplier Site") == {"rfq or bidding"}


# --- referenced columns ------------------------------------------------------

def test_referenced_columns_collects_rule_source_columns_only():
    exact = {"suppliersite": {
        "sitename": _dir(rule={"rule_type": "CONCAT",
                               "config": {"columns": ["Country Code", "City"]}}),
        "constantfield": _dir(constant="X"),        # a constant reads no column
    }}
    got = P.referenced_columns_for(exact, {}, "Supplier Site")
    assert got == {"Country Code", "City"}


# --- configs of type ---------------------------------------------------------

def test_configs_of_type_filters_by_rule_type_case_insensitively():
    exact = {"supplier": {
        "parent": _dir(rule={"rule_type": "SELF_LOOKUP", "config": {"key": "num"}}),
        "seq": _dir(rule={"rule_type": "SEQUENCE", "config": {"start": 1}}),
    }}
    assert P.configs_of_type(exact, {}, "Supplier", "self_lookup") == [{"key": "num"}]
    assert P.configs_of_type(exact, {}, "Supplier", "SEQUENCE") == [{"start": 1}]
    assert P.configs_of_type(exact, {}, "Supplier", "NOPE") == []


# --- frame rules (BLANK_IF_EQUALS) -------------------------------------------

def test_apply_frame_rules_blanks_equal_output_columns_case_ws_insensitive():
    exact = {"supplier": {"alternatename": _dir(
        rule={"rule_type": "BLANK_IF_EQUALS",
              "config": {"other_column": "Supplier Name"}})}}
    df = pd.DataFrame({
        "Alternate Name": ["Acme", "Acme", " acme ", "Beta", ""],
        "Supplier Name":  ["Acme", "acme", "ACME",   "Gamma", ""],
    })
    out = P.apply_frame_rules(df, exact, "Supplier")
    # rows 0,1,2 are equal ignoring case/whitespace -> blanked; 3 differs; 4 both blank
    assert list(out["Alternate Name"]) == ["", "", "", "Beta", ""]


def test_apply_frame_rules_skips_a_source_scoped_rule_on_mismatch():
    exact = {"supplier": {"alternatename": _dir(
        rule={"rule_type": "BLANK_IF_EQUALS", "config": {"other_column": "Supplier Name"}},
        source_erp="netsuite")}}
    df = pd.DataFrame({"Alternate Name": ["Acme"], "Supplier Name": ["Acme"]})
    # arena_ebos keeps its Alternate Name; netsuite blanks it
    assert list(P.apply_frame_rules(df.copy(), exact, "Supplier", "arena_ebos")["Alternate Name"]) == ["Acme"]
    assert list(P.apply_frame_rules(df.copy(), exact, "Supplier", "netsuite")["Alternate Name"]) == [""]


# --- the facade still speaks the same public API -----------------------------

def test_facade_delegates_and_loads_the_real_store():
    import app.services.strategy_overlay as SO
    # smoke: the store loads and the public surface is intact and callable
    assert isinstance(SO.blank_fields("Supplier"), set)
    assert isinstance(SO.referenced_columns("Supplier"), set)
    assert isinstance(SO.rule_configs_of_type("Supplier", "SELF_LOOKUP"), list)
    assert SO.self_lookup_configs("Supplier") == SO.rule_configs_of_type("Supplier", "SELF_LOOKUP")
    assert isinstance(SO.sheets_to_drop(), set)
    # directive_for tolerates absent keys
    assert SO.directive_for(None, None) is None
