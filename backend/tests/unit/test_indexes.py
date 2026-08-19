"""Unit tests for the ctx-index builders (Phase 2, slice 1). Pins each builder's
contract; mirrors cases verified byte-identical against the old output_service code."""
import pandas as pd
from app.domain.rules.indexes import (
    build_self_index, build_sequence_index, build_group_first_index,
    build_city_country_index, build_city_case_index,
)


def test_self_index_first_win_and_dot_zero():
    df = pd.DataFrame({"Internal ID": ["1", "2", "2", "", "5.0"],
                       "Name": ["ACME", "BETA", "GAMMA", "X", "DELTA"]})
    idx = build_self_index(df, [{"match_column": "Internal Id", "value_column": "Name"}])
    # case/space-insensitive column match; first win on dup key; .0 integral spelling indexed
    assert idx == {"Internal Id->Name": {"1": "ACME", "2": "BETA", "5.0": "DELTA", "5": "DELTA"}}


def test_self_index_empty_paths():
    df = pd.DataFrame({"a": ["1"]})
    assert build_self_index(df, []) == {}
    assert build_self_index(df, [{"match_column": "nope", "value_column": "a"}]) == {}
    assert build_self_index(pd.DataFrame(), [{"match_column": "a", "value_column": "b"}]) == {}


def test_sequence_index_first_appearance():
    df = pd.DataFrame({"entityid": ["C1", "C2", "C1", "C3", "10.0", ""]})
    idx = build_sequence_index(df, [{"key_column": "entityid"}])
    assert idx == {"entityid": {"C1": 0, "C2": 1, "C3": 2, "10.0": 3, "10": 3}}
    # list-spec resolves to the present column
    assert build_sequence_index(df, [{"key_column": ["Alt", "entityid"]}]) == idx


def test_group_first_index():
    df = pd.DataFrame({"entityid": ["C1", "C2", "C1", "C3"]})
    assert build_group_first_index(df, [{"key_column": "entityid"}]) == \
        {"entityid": {"C1": 0, "C2": 1, "C3": 3}}


def test_city_country_index_majority():
    df = pd.DataFrame({"Country Code": ["US", "US", "CN", "IN"],
                       "City": ["New York", "New York", "New York", "Hyderabad"]})
    assert build_city_country_index(
        df, [{"country_column": "Country Code", "city_column": "City"}]) == \
        {"newyork": "US", "hyderabad": "IN"}


def test_city_case_index_non_caps_wins():
    df = pd.DataFrame({"City": ["ABU DHABI", "Abu Dhabi", "HYDERABAD", "Hyderabad",
                                "Hyderabad", "Rio de Janeiro"]})
    # a non-all-caps spelling wins ahead of frequency; title() is never applied
    assert build_city_case_index(df, [{"city_column": "City"}]) == \
        {"abudhabi": "Abu Dhabi", "hyderabad": "Hyderabad", "riodejaneiro": "Rio de Janeiro"}
