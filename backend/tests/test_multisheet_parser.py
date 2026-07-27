"""Unit tests for multi-sheet workbook handling (Issue #1): list_excel_sheets +
parse_tabular(sheet=...). Builds a 2-sheet workbook in a temp file so the test is
self-contained (no fixture files)."""
import os
import sys
import tempfile

import openpyxl

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.parsers.tabular_parser import list_excel_sheets, parse_tabular  # noqa: E402


def _make_wb(path):
    wb = openpyxl.Workbook()
    cust = wb.active
    cust.title = "Customer"
    cust.append(["entityid", "companyname", "vatregnumber"])
    for i in range(5):
        cust.append([f"C{i}", f"Co {i}", f"VAT{i}"])
    addr = wb.create_sheet("Address")
    addr.append(["address_label", "city", "country", "postalcode"])
    for i in range(9):  # Address is the larger sheet
        addr.append([f"Addr {i}", "Boston", "US", f"0210{i}"])
    wb.save(path)


def test_list_sheets_largest_first():
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
        p = tf.name
    try:
        _make_wb(p)
        sheets = list_excel_sheets(p)
        names = [s["name"] for s in sheets]
        assert set(names) == {"Customer", "Address"}
        # Address has more rows*cols → ranked first
        assert names[0] == "Address"
        assert all(s["rows"] > 0 and s["cols"] > 0 for s in sheets)
    finally:
        os.unlink(p)


def test_parse_specific_sheet_columns_differ():
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
        p = tf.name
    try:
        _make_wb(p)
        cust = parse_tabular(p, sheet="Customer")
        addr = parse_tabular(p, sheet="Address")
        assert list(cust.columns) == ["entityid", "companyname", "vatregnumber"]
        assert list(addr.columns) == ["address_label", "city", "country", "postalcode"]
        # default (no sheet) reads the LARGEST sheet (Address) — the old behavior
        default = parse_tabular(p)
        assert list(default.columns) == list(addr.columns)
    finally:
        os.unlink(p)


def test_csv_reports_single_sheet():
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as tf:
        tf.write("a,b\n1,2\n")
        p = tf.name
    try:
        sheets = list_excel_sheets(p)
        assert len(sheets) == 1
    finally:
        os.unlink(p)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for t in tests:
        t(); print("PASS ", t.__name__); p += 1
    print(f"\n{p}/{len(tests)} passed")
