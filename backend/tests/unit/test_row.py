"""Unit tests for the per-row context object (Phase 2, slice 4). Pins the dict-like
contract the rule engine relies on; mirrors cases verified byte-identical against the old
output_service class."""
import pytest
from app.domain.rules.row import RowWithTargets


def test_source_wins_over_target():
    r = RowWithTargets({"A": "srcA"}, {"A": ["tgtA0", "tgtA1"], "C": ["c0", "c1"]}, 1)
    assert r.get("A") == "srcA"          # source shadows a same-named target
    assert r.get("C") == "c1"            # target read at index i
    assert r["A"] == "srcA" and r["C"] == "c1"


def test_target_fallback_and_default():
    r = RowWithTargets({"A": "srcA"}, {"B": ["b0", "b1", "b2"]}, 2)
    assert r.get("B") == "b2"
    assert r.get("MISSING") is None
    assert r.get("MISSING", "D") == "D"


def test_getitem_raises_keyerror():
    r = RowWithTargets({"A": "x"}, {}, 0)
    with pytest.raises(KeyError):
        r["NOPE"]


def test_contains():
    r = RowWithTargets({"A": "x"}, {"B": ["b"]}, 0)
    assert "A" in r and "B" in r and "Z" not in r


def test_iteration_dedup_source_first():
    # a source column of the same name wins and nothing is yielded twice
    r = RowWithTargets({"A": "x", "B": "y"}, {"A": ["ta"], "C": ["tc"]}, 0)
    assert list(r) == ["A", "B", "C"]
    assert r.keys() == ["A", "B", "C"]
    assert len(r) == 3


def test_dict_comprehension_over_row_does_not_crash():
    # the __iter__ crash case: `{norm(k): k for k in row}`
    r = RowWithTargets({"Party Type": "ORG"}, {"Party Number": ["P1"]}, 0)
    assert {k.lower(): k for k in r} == {"party type": "Party Type", "party number": "Party Number"}
