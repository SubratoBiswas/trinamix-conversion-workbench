"""Unit tests for source-data anomaly / outlier detection (dependency-light)."""
import os
import sys

import pandas as pd

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.anomaly_service import detect_anomalies  # noqa: E402


def _types(res, column=None):
    return {f["issue_type"] for f in res["findings"] if column is None or f["column"] == column}


def test_high_null_rate():
    df = pd.DataFrame({"C": ["a", "", "", "", ""]})
    assert "High null rate" in _types(detect_anomalies(df), "C")


def test_leading_trailing_spaces():
    df = pd.DataFrame({"Name": ["Acme", " Beta", "Gamma "]})
    assert "Leading/trailing spaces" in _types(detect_anomalies(df), "Name")


def test_mixed_types():
    df = pd.DataFrame({"Val": ["1", "2", "3", "abc", "def", "4"]})
    assert "Mixed types (numbers + text)" in _types(detect_anomalies(df), "Val")


def test_numeric_outliers():
    df = pd.DataFrame({"Qty": [str(x) for x in [10, 11, 9, 12, 10, 11, 13, 9, 999999]]})
    assert "Numeric outliers" in _types(detect_anomalies(df), "Qty")


def test_embedded_units():
    df = pd.DataFrame({"Weight": ["10", "12", "5 kg", "8", "9"]})
    assert "Embedded units" in _types(detect_anomalies(df), "Weight")


def test_inconsistent_casing():
    df = pd.DataFrame({"Country": ["US", "us", "US", "US", "Canada"]})
    assert "Inconsistent casing/spacing" in _types(detect_anomalies(df), "Country")


def test_non_printable_chars():
    df = pd.DataFrame({"Code": ["OK", "GOOD", "BA\x00D", "FINE"]})
    assert "Non-printable characters" in _types(detect_anomalies(df), "Code")


def test_duplicate_rows_and_summary():
    df = pd.DataFrame({"A": ["1", "1", "2"], "B": ["x", "x", "y"]})
    res = detect_anomalies(df)
    assert res["duplicate_rows"] == 1
    assert "Duplicate rows" in _types(res)
    s = res["summary"]
    assert s["error"] + s["warning"] + s["info"] == len(res["findings"])


def test_clean_frame_has_no_findings():
    df = pd.DataFrame({"Id": ["1", "2", "3"], "Name": ["Acme", "Beta", "Gamma"]})
    assert detect_anomalies(df)["findings"] == []


def test_empty_frame():
    res = detect_anomalies(pd.DataFrame())
    assert res["findings"] == [] and res["rows"] == 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for t in tests:
        t(); print("PASS ", t.__name__); p += 1
    print(f"\n{p}/{len(tests)} passed")
