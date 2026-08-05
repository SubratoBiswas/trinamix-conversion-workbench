"""Unit tests for the EBS → Oracle Fusion FBDI output transformations.

These are pure-function tests (no DB / no live EBS) that lock in the formatting
the output service applies when generating an FBDI file from live Oracle EBS
rows — UPPER_UNDERSCORE headers and YYYYMMDD dates.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from app.services.output_service import _normalize_columns, _format_date_columns  # noqa: E402


class _Field:
    """Minimal stand-in for FBDIField (only the attrs the helper reads)."""
    def __init__(self, field_name: str, data_type: str):
        self.field_name = field_name
        self.data_type = data_type


def test_normalize_columns_to_fbdi_headers():
    df = pd.DataFrame({"UOM Code": [1], "Base-UOM Flag": [2], "uom_class": [3]})
    out = _normalize_columns(df)
    assert list(out.columns) == ["UOM_CODE", "BASE_UOM_FLAG", "UOM_CLASS"]


def test_format_date_columns_to_yyyy_slash_mm_slash_dd():
    # Analyst, 05-Aug: "all dates should be yyyy/mm/dd format." A compact 20221231
    # (Oracle's own FBDI spelling) still converts to the slash form.
    fields = [_Field("EffectiveStartDate", "Date"), _Field("EffectiveEndDate", "date")]
    df = pd.DataFrame({
        "EFFECTIVESTARTDATE": ["2020-01-15", "2021/03/02", "03/04/2022", ""],
        "EFFECTIVEENDDATE": ["2020-01-15 00:00:00", "", "20221231", "not-a-date"],
    })
    out = _format_date_columns(df, fields)
    assert out["EFFECTIVESTARTDATE"].tolist() == ["2020/01/15", "2021/03/02", "2022/03/04", ""]
    # datetime strings parse; blanks pass through; unparseable values are left as-is
    assert out["EFFECTIVEENDDATE"].tolist() == ["2020/01/15", "", "2022/12/31", "not-a-date"]


def test_non_date_columns_untouched():
    fields = [_Field("UOMCode", "Character")]
    df = pd.DataFrame({"UOMCODE": ["EA", "BOX"], "UOMNAME": ["Each", "Box"]})
    out = _format_date_columns(df, fields)
    assert out["UOMCODE"].tolist() == ["EA", "BOX"]
    assert out["UOMNAME"].tolist() == ["Each", "Box"]
