"""FORMAT_DATE — the rule the engine never handled.

Customer and Employee mappings carried rule_type FORMAT_DATE (Oracle-token config:
from_format / to_format, e.g. "YYYY-MM-DD HH:MM:SS" -> "YYYY/MM/DD"), but the engine
only implemented DATE_FORMAT (Python-token config). An unknown rule_type is a no-op,
so an Employee EffectiveStartDate shipped "2024-02-12" (hyphens) instead of the
"YYYY/MM/DD" its own rule asked for — a live test on NextPower Employee 05-08-2026.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.transformations import engine as E            # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


_CFG = {"from_format": "YYYY-MM-DD HH:MM:SS", "to_format": "YYYY/MM/DD"}


def test_the_reported_case_iso_hyphen_to_slash():
    check("2024-02-12 -> 2024/02/12",
          E.apply_rule("FORMAT_DATE", _CFG, "2024-02-12") == "2024/02/12")


def test_value_carries_a_time_component():
    check("date+time -> date only, slashes",
          E.apply_rule("FORMAT_DATE", _CFG, "2024-02-12 00:00:00") == "2024/02/12")


def test_input_is_parsed_forgivingly_not_by_the_declared_from_format():
    # Extracts do not reliably match the declared from_format; the value is probed.
    for raw, want in [("2024/02/12", "2024/02/12"), ("02/12/2024", "2024/02/12"),
                      ("20240212", "2024/02/12"), ("12-Feb-2024", "2024/02/12")]:
        check(f"{raw} -> {want}", E.apply_rule("FORMAT_DATE", _CFG, raw) == want, raw)


def test_default_output_is_yyyy_slash_mm_slash_dd():
    check("no to_format -> the standard yyyy/mm/dd",
          E.apply_rule("FORMAT_DATE", {}, "2024-02-12") == "2024/02/12")


def test_blank_and_unparseable_are_left_alone():
    check("blank stays blank", E.apply_rule("FORMAT_DATE", _CFG, "") == "")
    check("unparseable value passes through for validation to flag",
          E.apply_rule("FORMAT_DATE", _CFG, "N/A") == "N/A")


def test_date_format_still_works_unchanged():
    # The pre-existing Python-token rule must be untouched.
    out = E.apply_rule("DATE_FORMAT", {"input_format": "%Y-%m-%d", "output_format": "%Y/%m/%d"}, "2024-02-12")
    check("DATE_FORMAT 2024-02-12 -> 2024/02/12", out == "2024/02/12", out)


def test_oracle_token_translation():
    # Phase 1c: the Oracle-token translator was relocated from engine into the domain
    # (app.domain.dates.fbdi_date.oracle_date_to_py). Same function, new home.
    from app.domain.dates.fbdi_date import oracle_date_to_py
    check("YYYY-MM-DD -> %Y-%m-%d", oracle_date_to_py("YYYY-MM-DD") == "%Y-%m-%d")
    check("YYYY/MM/DD -> %Y/%m/%d", oracle_date_to_py("YYYY/MM/DD") == "%Y/%m/%d")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nFORMAT_DATE now normalises dates to yyyy/mm/dd")
