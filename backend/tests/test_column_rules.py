"""The built output checked against Oracle's published column rules.

Companion to test_template_comments.py: that one proves the rules are read correctly,
this one proves they are applied correctly and reported in a shape a human can use.

The deliberate difference from validation/engine.py is aggregation. The engine walks
ROWS and emits one issue per offending row — right for a reject report, useless on a
review screen, where 8,000 rows of "row N: too long" is a wall rather than a finding.
This groups by COLUMN: one row per rule with a count, the template's own spec, and a
few real examples.

Only ``mandatory`` blocks. A missing mandatory value rejects every affected row, so
handing the file over only moves the failure to cutover. The others are reported: a
too-long value or an unexpected code needs a decision, and a gate that blocks on
everything gets switched off.

Pure: pandas + stdlib.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import column_rules_service as cr  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def F(**kw):
    base = {"field_name": "X", "db_column": "X_COL", "data_type": "Character"}
    base.update(kw)
    return base


def one(series_values, field):
    return cr.check_column(pd.Series(series_values), field)


def rules(findings):
    return {f["rule"] for f in findings}


# ── Mandatory — the only blocking rule ──────────────────────────────────────
def test_a_mandatory_column_with_blanks_blocks():
    f = one(["ACME", "", None, "Beta"], F(field_name="Supplier Name", required=True))
    check("one finding", len(f) == 1, f"got {rules(f)}")
    check("it is mandatory", f[0]["rule"] == cr.RULE_MANDATORY)
    check("blocking", f[0]["blocking"] is True)
    check("counts every blank form", f[0]["count"] == 2, f"got {f[0]['count']}")
    check("names the column and the totals", "2 of 4" in f[0]["message"],
          f"got {f[0]['message']!r}")


def test_the_blank_forms_a_frame_actually_carries_all_count():
    """Frames arrive with NaN, "nan", "NULL" and "" mixed together depending on which
    parser and merge produced them. Treating only "" as blank passes a column that is
    empty in every practical sense."""
    f = one(["", " ", "nan", "NaN", "NULL", "None", "na", "real"],
            F(required=True))
    check("7 of 8 blank", f[0]["count"] == 7, f"got {f[0]['count']}")


def test_a_full_mandatory_column_is_silent():
    check("nothing", one(["a", "b"], F(required=True)) == [])


def test_an_optional_column_with_blanks_is_silent():
    check("nothing", one(["a", "", ""], F(required=False)) == [])


# ── VARCHAR2(n) ─────────────────────────────────────────────────────────────
def test_values_over_the_column_length():
    f = one(["ok", "far too long for ten", "fine"], F(max_length=10))
    check("one finding", len(f) == 1 and f[0]["rule"] == cr.RULE_MAX_LENGTH)
    check("error, not blocking", f[0]["severity"] == "error" and not f[0]["blocking"])
    check("reports the limit", f[0]["limit"] == 10)
    check("reports the longest seen", "longest 20" in f[0]["message"],
          f"got {f[0]['message']!r}")
    check("shows the offending value",
          f[0]["examples"] == ["far too long for ten"], f"got {f[0]['examples']}")


def test_length_is_measured_on_the_value_not_the_blank():
    check("blanks are not over-length", one(["", None], F(max_length=1)) == [])


# ── NUMBER(p[,s]) ───────────────────────────────────────────────────────────
def test_non_numeric_in_a_number_column():
    f = one(["1", "2", "N/A ish", "4"], F(data_type="Number"))
    check("numeric rule", rules(f) == {cr.RULE_NUMERIC}, f"got {rules(f)}")
    check("one value", f[0]["count"] == 1)


def test_thousands_separators_are_still_numeric():
    """A merged extract routinely carries "1,234". Flagging it as non-numeric is a
    false positive that buries the real ones."""
    check("clean", one(["1,234", "5678"], F(data_type="Number")) == [])


def test_more_digits_than_the_column_holds():
    f = one(["1", "9" * 19], F(data_type="Number", precision=18))
    check("precision rule", cr.RULE_PRECISION in rules(f), f"got {rules(f)}")
    p = next(x for x in f if x["rule"] == cr.RULE_PRECISION)
    check("one value", p["count"] == 1)
    check("error", p["severity"] == "error")
    check("reports the precision", p["precision"] == 18)


def test_leading_zeros_do_not_count_as_digits():
    """"000000000000000000001" is 1. Counting the padding overflows an 18-digit
    column for a value that fits, and legacy ids are frequently zero-padded."""
    check("fits", not [x for x in one(["0" * 20 + "1"],
                                     F(data_type="Number", precision=18))
                       if x["rule"] == cr.RULE_PRECISION])


def test_the_scale_check_counts_only_significant_decimals():
    f = one(["1.500", "1.567"], F(data_type="Number", precision=10, scale=2))
    s = [x for x in f if x["rule"] == cr.RULE_SCALE]
    check("only the genuinely over-scaled one", s and s[0]["count"] == 1,
          f"got {[x['count'] for x in s]}")
    check("a warning, not an error", s[0]["severity"] == "warning")


def test_precision_is_measured_before_the_decimal_point():
    f = one(["12345.99"], F(data_type="Number", precision=5, scale=2))
    check("5 - 2 = 3 integer digits allowed, so this overflows",
          cr.RULE_PRECISION in rules(f), f"got {rules(f)}")


# ── DATE ────────────────────────────────────────────────────────────────────
def test_dates_not_in_the_stated_mask():
    f = one(["2020/01/15", "2020-01-15", "15-JAN-2020"],
            F(data_type="Date", format_mask="YYYY/MM/DD"))
    d = next(x for x in f if x["rule"] == cr.RULE_DATE_FORMAT)
    check("two bad", d["count"] == 2, f"got {d['count']}")
    check("mask reported", d["format_mask"] == "YYYY/MM/DD")


def test_a_column_whose_mask_is_not_the_usual_one_is_honoured():
    """The mask is read from the comment, so a template that states a different one
    must be checked against THAT, not against Oracle's usual YYYY/MM/DD."""
    f = one(["20200115"], F(data_type="Date", format_mask="YYYYMMDD"))
    check("accepted", not [x for x in f if x["rule"] == cr.RULE_DATE_FORMAT],
          f"got {f}")


# ── Value set ───────────────────────────────────────────────────────────────
def test_values_outside_the_codes_oracle_lists():
    f = one(["Y", "N", "Yes", "1"],
            F(allowed_values=[{"code": "Y"}, {"code": "N"}]))
    v = next(x for x in f if x["rule"] == cr.RULE_VALUE_SET)
    check("two rejected", v["count"] == 2, f"got {v['count']}")
    check("lists what is accepted", v["allowed_values"] == ["Y", "N"])
    check("names them in the message", "Y, N" in v["message"], f"got {v['message']!r}")


def test_the_value_set_check_ignores_case_and_padding():
    """Oracle's own loader is case-tolerant on these codes, so flagging " y " would be
    a false positive."""
    f = one([" y ", "N"], F(allowed_values=[{"code": "Y"}, {"code": "N"}]))
    check("clean", not [x for x in f if x["rule"] == cr.RULE_VALUE_SET], f"got {f}")


def test_plain_string_allowed_values_work_too():
    f = one(["A", "Z"], F(allowed_values=["A", "B"]))
    check("flagged", cr.RULE_VALUE_SET in rules(f), f"got {rules(f)}")


# ── Do-not-populate ─────────────────────────────────────────────────────────
def test_a_populated_column_oracle_says_not_to_use():
    f = one(["", "junk", ""], F(field_name="Attribute 24", do_not_populate=True))
    d = next(x for x in f if x["rule"] == cr.RULE_DO_NOT_POPULATE)
    check("one value", d["count"] == 1)
    check("warning, not blocking", d["severity"] == "warning" and not d["blocking"])
    check("blank rows are fine", d["count"] == 1)


def test_leaving_such_a_column_blank_raises_nothing():
    check("silent", one(["", None], F(do_not_populate=True)) == [])


# ── Frame level ─────────────────────────────────────────────────────────────
def test_columns_are_matched_on_a_normalised_name():
    """The frame may carry Oracle's header label — with the '*' marker and spacing —
    while the field record holds the clean name."""
    df = pd.DataFrame({"*Supplier  Name": ["", "A"]})
    res = cr.check_frame(df, [F(field_name="Supplier Name", required=True)])
    check("matched", res["findings"] and res["findings"][0]["count"] == 1,
          f"got {res}")


def test_a_column_the_template_says_nothing_about_is_not_counted_as_checked():
    """The honest denominator. Counting rule-less columns as checked would make a
    template with no comments at all read as fully verified — which is exactly the
    bundled Item workbook's situation."""
    df = pd.DataFrame({"A": ["1"], "B": ["2"]})
    res = cr.check_frame(df, [F(field_name="A", required=True), F(field_name="B")])
    check("both matched", res["columns_checked"] == 2)
    check("only one carries a rule", res["columns_with_rules"] == 1,
          f"got {res['columns_with_rules']}")


def test_findings_are_ordered_worst_first():
    df = pd.DataFrame({"Opt": ["much too long"], "Req": [""]})
    res = cr.check_frame(df, [F(field_name="Opt", max_length=3),
                              F(field_name="Req", required=True)])
    check("blocking first", res["findings"][0]["rule"] == cr.RULE_MANDATORY,
          f"got {[f['rule'] for f in res['findings']]}")


def test_an_empty_frame_reports_nothing_rather_than_failing():
    res = cr.check_frame(pd.DataFrame(), [F(required=True)])
    check("no findings", res["findings"] == [])
    check("not blocked", res["blocked"] is False)
    check("and says nothing was checked", res["columns_checked"] == 0)


# ── Roll-up ─────────────────────────────────────────────────────────────────
def test_the_summary_names_the_scope_it_actually_checked():
    df = pd.DataFrame({"Req": ["", "A"]})
    res = cr.summarize({"POZ_SUPPLIERS_INT":
                        cr.check_frame(df, [F(field_name="Req", required=True)])})
    check("blocked", res["blocked"] is True)
    check("the sheet is on the finding", res["findings"][0]["sheet"] == "POZ_SUPPLIERS_INT")
    check("message states the rule count",
          "1 column rule" in res["message"], f"got {res['message']!r}")
    check("and what is wrong", "mandatory" in res["message"], f"got {res['message']!r}")


def test_a_template_with_no_rules_says_so_instead_of_passing():
    df = pd.DataFrame({"A": ["1"]})
    res = cr.summarize({"S": cr.check_frame(df, [F(field_name="A")])})
    check("not blocked", res["blocked"] is False)
    check("explains why nothing was checked",
          "no header comments" in res["message"], f"got {res['message']!r}")


def test_a_clean_sheet_says_it_checked_and_found_nothing():
    df = pd.DataFrame({"Req": ["A", "B"]})
    res = cr.summarize({"S": cr.check_frame(df, [F(field_name="Req", required=True)])})
    check("nothing violated", "Nothing violates them" in res["message"],
          f"got {res['message']!r}")
    check("no findings", res["findings"] == [])


def test_by_rule_tally_is_present_for_the_pills():
    df = pd.DataFrame({"A": ["toolong"], "B": [""]})
    res = cr.summarize({"S": cr.check_frame(
        df, [F(field_name="A", max_length=2), F(field_name="B", required=True)])})
    tally = {r["rule"]: r["count"] for r in res["by_rule"]}
    check("both rules tallied",
          tally.get(cr.RULE_MANDATORY) == 1 and tally.get(cr.RULE_MAX_LENGTH) == 1,
          f"got {tally}")
    check("counts split by severity", res["error_count"] == 2, f"got {res}")


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
