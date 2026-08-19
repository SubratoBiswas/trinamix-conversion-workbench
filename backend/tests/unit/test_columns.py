"""Unit tests for the rule column-reference analysis (Phase 2, slice 2). Pins which
source columns each rule type declares so the frame keeps them; mirrors cases verified
byte-identical against the old output_service code."""
from app.domain.rules.columns import (
    flat_cols, branch_columns, interpolated_columns, rule_referenced_columns,
)


def test_flat_cols():
    assert flat_cols("A") == ["A"]
    assert flat_cols(["A", ["B", None, ""], "C"]) == ["A", "B", "C"]
    assert flat_cols(None) == []


def test_branch_columns_nested():
    br = [{"all": [{"if_column": "x"}], "any": [{"if_column": "y"}]}, {"if_column": "z"}]
    assert branch_columns(br) == {"x", "y", "z"}


def test_interpolated_columns():
    assert interpolated_columns("E{a}x", "{b}{c}", "plain", 5) == {"a", "b", "c"}


def test_rule_referenced_columns_concat_parts():
    rules = [{"rule_type": "CONCAT", "source_column": "Src",
              "config": {"columns": ["A", ["B", "b2"]],
                         "parts": [{"col": "C"}, {"literal": "_RS"}, "D"]}}]
    assert rule_referenced_columns(rules) == {"Src", "A", "B", "b2", "C", "D"}


def test_rule_referenced_columns_case_when_interp():
    rules = [{"rule_type": "CASE_WHEN", "config": {
        "branches": [{"if_column": "P", "then": "{tax_id}"},
                     {"all": [{"if_column": "orgname", "op": "isblank"},
                              {"if_column": "person", "op": "notblank"}], "then": "PERSON"}],
        "default": "{fallback_col}"}}]
    assert rule_referenced_columns(rules) == {"P", "tax_id", "orgname", "person", "fallback_col"}


def test_rule_referenced_columns_self_lookup_and_sequence():
    assert rule_referenced_columns([{"rule_type": "SELF_LOOKUP", "config": {
        "key_column": "Parent Vendor Id", "match_column": "Internal Id",
        "value_column": "Name"}}]) == {"Parent Vendor Id", "Internal Id", "Name"}
    assert rule_referenced_columns([{"rule_type": "SEQUENCE", "config": {
        "key_column": "entityid",
        "variant": {"if_column": "Party Type"}}}]) == {"entityid", "Party Type"}


def test_rule_referenced_columns_chained_then_and_empty():
    rules = [{"rule_type": "CONCAT", "config": {"columns": ["A"], "then": [
        {"rule_type": "SUFFIX_WHEN", "config": {"branches": [
            {"if_column": "Q", "op": "notblank", "suffix": "_s"}]}}]}}]
    assert rule_referenced_columns(rules) == {"A", "Q"}
    assert rule_referenced_columns([]) == set()
    assert rule_referenced_columns([{"rule_type": "TRIM", "config": {}}]) == set()
