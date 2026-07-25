"""Unit tests for synthetic test-data generation (pure)."""
import os
import re
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.synthetic_data_service import synthetic_frame  # noqa: E402


FIELDS = [
    {"field_name": "Batch ID", "required": True, "data_type": "NUMBER", "max_length": 18},
    {"field_name": "Import Action", "required": True, "data_type": "VARCHAR2", "max_length": 10,
     "allowed_values": [{"code": "CREATE"}, {"code": "UPDATE"}]},
    {"field_name": "Supplier Name", "required": True, "data_type": "VARCHAR2", "max_length": 360},
    {"field_name": "Supplier Number", "required": True, "data_type": "VARCHAR2", "max_length": 30},
    {"field_name": "Inactive Date", "required": False, "data_type": "DATE", "format_mask": "YYYYMMDD"},
    {"field_name": "Email Address", "required": False, "data_type": "VARCHAR2", "max_length": 50},
    {"field_name": "Notes", "required": False, "data_type": "VARCHAR2", "max_length": 8},
]


def test_row_count_and_columns():
    df = synthetic_frame(FIELDS, n=10, seed=1)
    assert len(df) == 10
    assert list(df.columns) == [f["field_name"] for f in FIELDS]


def test_required_always_populated():
    df = synthetic_frame(FIELDS, n=20, seed=2)
    for col in ["Batch ID", "Import Action", "Supplier Name", "Supplier Number"]:
        assert (df[col].astype(str).str.strip() != "").all(), col


def test_lov_values_respected():
    df = synthetic_frame(FIELDS, n=30, seed=3)
    assert set(df["Import Action"]) <= {"CREATE", "UPDATE"}


def test_business_keys_unique():
    df = synthetic_frame(FIELDS, n=25, seed=4)
    assert df["Supplier Number"].nunique() == 25
    assert df["Batch ID"].nunique() == 25


def test_max_length_respected():
    df = synthetic_frame(FIELDS, n=15, seed=5)
    assert df["Notes"].map(lambda x: len(str(x)) <= 8).all()


def test_date_format_mask():
    df = synthetic_frame(FIELDS, n=40, seed=6)
    dates = [d for d in df["Inactive Date"] if str(d).strip()]
    assert dates, "expected some dates populated"
    assert all(re.fullmatch(r"\d{8}", str(d)) for d in dates)


def test_reproducible_with_seed():
    a = synthetic_frame(FIELDS, n=8, seed=99)
    b = synthetic_frame(FIELDS, n=8, seed=99)
    assert a.equals(b)


def test_empty_fields():
    assert synthetic_frame([], n=5).empty


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for t in tests:
        t(); print("PASS ", t.__name__); p += 1
    print(f"\n{p}/{len(tests)} passed")
