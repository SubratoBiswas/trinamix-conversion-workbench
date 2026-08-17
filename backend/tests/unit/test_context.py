"""Unit tests for the shared rule helpers relocated to the domain (interpolation, row
resolution, branch conditions). Pins their contract so the stateful rule strategies that
will build on them have a stable base."""
from app.domain.rules.context import (
    _interpolate, _resolve_column, _row_value_ci, _branch_holds, _COMPARISON_OPS)


def test_interpolate():
    assert _interpolate("E{id}", {"id": "123"}) == "E123"
    assert _interpolate("C{Employee_ID|digits}", {"Employee_ID": "C12345"}) == "C12345"
    assert _interpolate("x{nope}", {"a": "1"}) == "x{nope}"      # unknown token kept
    assert _interpolate("no braces", {"a": "1"}) == "no braces"
    assert _interpolate("{a}", None) == "{a}"                    # no row -> passthrough


def test_resolve_and_row_value():
    assert _resolve_column(["A", "B"], {"B": "v"}) == "B"
    assert _resolve_column("plain", {"x": 1}) == "plain"
    assert _row_value_ci({"Parent_Vendor_Id": "P1"}, "Parent Vendor Id") == "P1"
    assert _row_value_ci(None, "x") == ""


def test_branch_holds():
    assert _branch_holds({"if_column": "x", "op": "eq", "value": "1"}, None, {"x": "1"}) is True
    assert _branch_holds({"if_column": "x", "op": "eq", "value": "1"}, None, {"x": "2"}) is False
    assert _branch_holds({"op": "notblank"}, "val", None) is True
    assert _branch_holds(
        {"all": [{"if_column": "a", "op": "notblank"},
                 {"if_column": "b", "op": "isblank"}]}, None, {"a": "X", "b": ""}) is True
    assert _COMPARISON_OPS["istrue"]("Yes", None) is True
    assert _COMPARISON_OPS["isfalse"]("No", None) is True
