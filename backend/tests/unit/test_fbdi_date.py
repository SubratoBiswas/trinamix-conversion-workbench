"""Unit tests for the FBDI date value object (Phase 1a).

Pins the exact contract that customer_merge._fbdi_date shipped before delegation, so the
value object cannot silently drift as the remaining parsers (to_fbdi_date, _parse_any_date,
_norm_date) are folded into it in Phase 1b. Pure and fast — no DB, no framework.
"""
import pytest
from app.domain.dates.fbdi_date import FbdiDate, DateOrder, fbdi_date


@pytest.mark.parametrize("raw,dayfirst,expected", [
    # blank / NA -> ""
    ("", False, ""), ("   ", False, ""), ("nan", False, ""), ("<NA>", False, ""),
    ("None", True, ""), ("null", False, ""),
    # YYYY-first is unambiguous (dayfirst irrelevant), prefix match keeps time out
    ("2018-08-20", False, "2018/08/20"), ("2018/8/2", True, "2018/08/02"),
    ("2018-08-20 00:00:00", False, "2018/08/20"),
    # dd-mm-yyyy read by order
    ("20-08-2018", True, "2018/08/20"),      # day-first, unambiguous
    ("01-02-2018", True, "2018/02/01"),      # day-first, ambiguous -> 1 Feb
    ("01-02-2018", False, "2018/01/02"),     # month-first -> 2 Jan (historic default)
    ("06-12-2019", True, "2019/12/06"),
    # NON-validating: an out-of-range month passes through as-is (legacy behaviour that
    # the DateOrder fix, not this refactor, is responsible for avoiding)
    ("20-08-2018", False, "2018/20/08"),
    # free text / numbers / partials pass through as the stripped original
    ("Net 30", True, "Net 30"), ("23746721.89", True, "23746721.89"),
    ("20180820", True, "20180820"), ("2018", False, "2018"),
])
def test_fbdi_date_contract(raw, dayfirst, expected):
    assert fbdi_date(raw, dayfirst) == expected


def test_value_object_formatting_and_validity():
    assert FbdiDate(2018, 8, 20).to_fbdi("/") == "2018/08/20"
    assert FbdiDate(2018, 8, 20).to_fbdi("-") == "2018-08-20"
    assert FbdiDate(2018, 8, 20).is_valid is True
    assert FbdiDate(2018, 20, 8).is_valid is False   # holds an invalid triple, reports it


def test_from_regex_order():
    assert FbdiDate.from_regex("01-02-2018", DateOrder.DAY_FIRST) == FbdiDate(2018, 2, 1)
    assert FbdiDate.from_regex("01-02-2018", DateOrder.MONTH_FIRST) == FbdiDate(2018, 1, 2)
    assert FbdiDate.from_regex("not a date", DateOrder.DAY_FIRST) is None
