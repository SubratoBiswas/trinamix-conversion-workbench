"""Unit tests for the frame-formatting cluster (Phase 2, slice 3). Pins each function's
contract; mirrors cases verified byte-identical against the old output_service code.
Today-token resolution is asserted structurally (utcnow is non-deterministic)."""
import re
import pandas as pd
from app.domain.frames import (
    to_fbdi_date, format_date_columns, blank_null_sentinels,
    resolve_today_tokens, dedup, mask_supplier_emails, safe_sheet_name,
)

_YMD = re.compile(r"^\d{4}/\d{2}/\d{2}$")


def test_to_fbdi_date():
    assert to_fbdi_date("2020-01-15") == "2020/01/15"
    assert to_fbdi_date("2020-01-15 00:00:00.000") == "2020/01/15"   # fractional seconds
    assert to_fbdi_date("15-JAN-2020") == "2020/01/15"
    assert to_fbdi_date("15/01/2020", dayfirst=True) == "2020/01/15"
    assert to_fbdi_date("not a date") == "not a date"                # untouched
    assert to_fbdi_date("") == "" and to_fbdi_date(None) is None
    assert _YMD.match(to_fbdi_date("SYSDATE"))                       # token -> today


def test_format_date_columns_normalised_name_match():
    class F:
        def __init__(s, n, d): s.field_name, s.data_type = n, d
    fields = [F("EffectiveStartDate", "Date"), F("Amount", "Number")]
    df = pd.DataFrame({"EFFECTIVE_START_DATE": ["2020-01-15", ""], "Amount": ["1", "2"]})
    out = format_date_columns(df, fields)
    assert out["EFFECTIVE_START_DATE"].tolist() == ["2020/01/15", ""]
    assert out["Amount"].tolist() == ["1", "2"]                     # non-date untouched


def test_blank_null_sentinels_keeps_person_none():
    df = pd.DataFrame({"Desc": ["NULL", "real null value"],
                       "person first name": ["None", "N/A"]})
    out = blank_null_sentinels(df)
    assert out["Desc"].tolist() == ["", "real null value"]
    assert out["person first name"].tolist() == ["None", ""]        # keeps literal None; blanks N/A


def test_resolve_today_tokens():
    out = resolve_today_tokens(pd.DataFrame({"D": ["SYSDATE", "real", "NOW"]}))
    vals = out["D"].tolist()
    assert _YMD.match(vals[0]) and vals[1] == "real" and _YMD.match(vals[2])


def test_dedup():
    assert dedup(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_mask_supplier_emails_idempotent():
    df = pd.DataFrame({"Email Address": ["ap@x.com", "xxap@x.com", "", "NULL"], "Other": ["a", "b", "c", "d"]})
    out = mask_supplier_emails(df)
    assert out["Email Address"].tolist() == ["xxap@x.com", "xxap@x.com", "", "NULL"]
    assert out["Other"].tolist() == ["a", "b", "c", "d"]           # non-email untouched


def test_safe_sheet_name():
    assert safe_sheet_name("HZ_IMP_ACCOUNTS_T") == "HZ_IMP_ACCOUNTS_T"
    assert safe_sheet_name("weird/name*here") == "weird_name_here"
    assert safe_sheet_name("---") == "---"          # hyphens are allowed, not stripped
    assert safe_sheet_name("***") == "sheet"        # all-illegal collapses to empty -> "sheet"
    assert safe_sheet_name("") == "sheet"
