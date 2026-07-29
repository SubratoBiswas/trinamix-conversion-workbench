"""Oracle FBDI date coercion — the second live-audit finding of 29-Jul.

tests/test_ebs_output.py has asserted the intended behaviour since it was written,
and had been FAILING in the repository: ``_format_date_columns`` compared frame
headers to template field names with a case- and punctuation-SENSITIVE ``in``, so on
the EBS path (where ``_normalize_columns`` has already turned
``EffectiveStartDate`` into ``EFFECTIVE_START_DATE``) it matched nothing at all. And
the accepted-format list omitted ``%Y-%m-%d %H:%M:%S`` — the spelling every SQL and
ODBC export produces.

Either defect ships ``2020-01-15`` into a loader that accepts only ``20200115``:
Oracle rejects every dated row, with no warning anywhere in the tool.

I had reported "285 tests, all green" for this suite set. That was wrong — I ran the
suites named in the handoff, and this file was not among them. It is now.

Pure: pandas + stdlib.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.output_service import (  # noqa: E402
    _format_date_columns, to_fbdi_date,
)

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


class F:
    def __init__(self, field_name, data_type="Date"):
        self.field_name = field_name
        self.data_type = data_type


# ── Header matching (defect 1) ───────────────────────────────────────────────
def test_header_spelling_does_not_decide_whether_dates_convert():
    """Every spelling of one field name must reach the same YYYYMMDD output.

    ``EFFECTIVE_START_DATE`` is not hypothetical — it is exactly what
    ``_normalize_columns`` produces on the live EBS path.
    """
    for header in ("EffectiveStartDate", "EFFECTIVESTARTDATE",
                   "EFFECTIVE_START_DATE", "effective start date",
                   "Effective-Start-Date", " EffectiveStartDate "):
        out = _format_date_columns(
            pd.DataFrame({header: ["2020-01-15"]}), [F("EffectiveStartDate")])
        check(f"{header!r} converts", out[header].tolist() == ["20200115"],
              f"got {out[header].tolist()}")


def test_a_non_date_column_is_never_touched():
    out = _format_date_columns(
        pd.DataFrame({"UOMCODE": ["EA"], "UOM_NAME": ["2020-01-15"]}),
        [F("UOMCode", "Character")])
    check("code untouched", out["UOMCODE"].tolist() == ["EA"])
    check("a date-LOOKING value in a text column is left alone",
          out["UOM_NAME"].tolist() == ["2020-01-15"])


def test_a_field_with_no_name_does_not_match_every_column():
    """A blank normalises to "", and "" would otherwise match a blank header —
    turning one malformed template row into a frame-wide rewrite."""
    out = _format_date_columns(pd.DataFrame({"": ["2020-01-15"]}), [F("", "Date")])
    check("blank header untouched", out[""].tolist() == ["2020-01-15"])


# ── Accepted input spellings (defect 2) ──────────────────────────────────────
def test_the_sql_timestamp_spelling_converts():
    """The regression that mattered most: this is what a database export writes."""
    for s in ("2020-01-15 00:00:00", "2020-01-15 00:00:00.000",
              "2020-01-15T09:30:00", "2020-01-15 09:30"):
        check(f"{s!r} -> 20200115", to_fbdi_date(s) == "20200115",
              f"got {to_fbdi_date(s)!r}")


def test_oracles_own_date_display_converts():
    """DD-MON-YYYY is the default DATE format in SQL*Plus, so EBS extracts are
    full of it."""
    check("15-JAN-2020", to_fbdi_date("15-JAN-2020") == "20200115")
    check("15-Jan-20", to_fbdi_date("15-Jan-20") == "20200115")
    check("15 Jan 2020", to_fbdi_date("15 Jan 2020") == "20200115")
    check("Jan 15, 2020", to_fbdi_date("Jan 15, 2020") == "20200115")


def test_slash_and_dash_dates():
    check("2020/01/15", to_fbdi_date("2020/01/15") == "20200115")
    check("2020/01/15 08:00:00", to_fbdi_date("2020/01/15 08:00:00") == "20200115")
    check("01/15/2020 (US first)", to_fbdi_date("01/15/2020") == "20200115")
    check("01-15-2020", to_fbdi_date("01-15-2020") == "20200115")


def test_already_formatted_values_are_idempotent():
    """Generation can run twice over the same frame; a second pass must be a no-op
    rather than reinterpreting 20200115 as something else."""
    check("20200115 stays", to_fbdi_date("20200115") == "20200115")
    check("idempotent", to_fbdi_date(to_fbdi_date("2020-01-15")) == "20200115")


def test_ambiguous_day_month_resolves_us_first():
    """03/04/2022 is 4 March in the US reading. Locked in deliberately: the
    extracts in play are US-sourced, and silently switching would move thousands of
    dates by up to eleven months with nothing in the file to show it."""
    check("03/04/2022 -> 20220304", to_fbdi_date("03/04/2022") == "20220304")
    check("25/12/2022 (only valid as DD/MM) -> 20221225",
          to_fbdi_date("25/12/2022") == "20221225")


# ── Values that must survive untouched ───────────────────────────────────────
def test_blanks_and_none_pass_through():
    for v in (None, "", "   "):
        check(f"{v!r} unchanged", to_fbdi_date(v) == v)


def test_unparseable_text_is_left_as_the_analyst_wrote_it():
    """Better in the reject report as the original string than as a blank — a blank
    loses the only clue to what went wrong."""
    for v in ("not-a-date", "ASAP", "TBD", "0000-00-00", "99/99/9999"):
        check(f"{v!r} unchanged", to_fbdi_date(v) == v, f"got {to_fbdi_date(v)!r}")


def test_a_number_is_not_coerced_into_a_date():
    """A quantity column mistyped as Date in the template must not become a date.
    8 digits is the trap: 20200115 IS a date, but 12345678 is not."""
    check("12345678 unchanged", to_fbdi_date("12345678") == "12345678")
    check("0 unchanged", to_fbdi_date(0) == 0)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except AssertionError:
            pass
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("all checks passed")
