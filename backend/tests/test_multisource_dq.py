"""Unit tests for multi-source converge/de-dup (Phase B) and generate-time
data quality — cleansing + validation (Phase C).

Runnable directly:  python3 backend/tests/test_multisource_dq.py
Calls the same dependency-light modules the generator uses
(app.services.merge_dedupe, app.services.generate_dq).
"""
import os
import sys

import pandas as pd

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.merge_dedupe import key_col_for, merge_dedupe  # noqa: E402
from app.services.generate_dq import apply_cleansing, build_report, validate_frame  # noqa: E402

KR = {"Supplier": ["SupplierNumber", "Supplier Number"], "Item": ["Item Number", "ItemNumber"]}


def test_master_dedup_source_priority():
    s1 = pd.DataFrame([{"Supplier Number": "S1", "Supplier Name": "Acme (src1)"},
                       {"Supplier Number": "S2", "Supplier Name": "Beta"}])
    s2 = pd.DataFrame([{"Supplier Number": "S1", "Supplier Name": "Acme (src2)"},
                       {"Supplier Number": "S3", "Supplier Name": "Gamma"}])
    m = merge_dedupe([s1, s2], "Supplier Import", KR)
    assert len(m) == 3, "S1 should collapse to one row across sources"
    assert sorted(m["Supplier Number"]) == ["S1", "S2", "S3"]
    kept = m[m["Supplier Number"] == "S1"]["Supplier Name"].iloc[0]
    assert kept == "Acme (src1)", "higher-priority source (first) must win"


def test_child_interface_keeps_distinct_rows():
    c1 = pd.DataFrame([{"Supplier Number": "S1", "Supplier Site": "SITE-A"},
                       {"Supplier Number": "S1", "Supplier Site": "SITE-B"}])
    c2 = pd.DataFrame([{"Supplier Number": "S1", "Supplier Site": "SITE-A"},
                       {"Supplier Number": "S1", "Supplier Site": "SITE-C"}])
    m = merge_dedupe([c1, c2], "Supplier Site", KR)
    # exact-dup SITE-A collapses; A/B/C distinct sites all kept (not collapsed to 1)
    assert sorted(m["Supplier Site"]) == ["SITE-A", "SITE-B", "SITE-C"]
    assert key_col_for(pd.concat([c1]), "Supplier Site", KR) is None


def test_single_frame_unchanged():
    s1 = pd.DataFrame([{"Item Number": "I1"}, {"Item Number": "I2"}])
    m = merge_dedupe([s1], "Item Import", KR)
    assert len(m) == 2


def test_cleansing_trims_and_applies_rules():
    df = pd.DataFrame([{"Supplier Name": "  Acme  ", "Code": "abc"}])
    cleaned, fixes = apply_cleansing(df, [{"field": "Supplier Name", "rule_type": "UPPERCASE"},
                                          {"field": "Code", "rule_type": "UPPERCASE"}])
    assert cleaned["Supplier Name"].iloc[0] == "ACME"
    assert cleaned["Code"].iloc[0] == "ABC"
    assert any(f["rule"] == "TRIM" for f in fixes)


def test_validation_and_report_block_on_hard_error():
    df = pd.DataFrame([{"Supplier Name": "Acme", "Supplier Number": "", "Amount": "-5"}])
    tf = [{"field_name": "Supplier Number", "required": True, "data_type": "text", "max_length": 30}]
    custom = [{"field": "Amount", "rule_type": "NOT_NEGATIVE", "severity": "error"}]
    issues = validate_frame(df, tf, custom, 2000)
    rep = build_report(issues, [])
    types = {i["issue_type"] for i in issues}
    assert "Missing Required Field" in types
    assert "Negative Value Not Allowed" in types
    # missing-required is NOT a hard block; negative value IS
    assert rep["hard_error_count"] == 1 and rep["blocked"] is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed")
