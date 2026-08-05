"""Paste SQL, get a rule — and the rule the parser emits must actually run.

Analyst, 05-Aug: "Any SQL query written in the new text box should be understood
by python or AI functions and it should be converted to a rule for that field."

A SQL CASE expression is the CASE/WHEN rule in another syntax, so it parses
offline. This pins two things the deterministic parser must get right:
  1. the config SHAPE (branches + default, correct ops), and
  2. that the emitted config, fed to the real engine, produces the right values —
     a parser that emits a plausible-but-wrong config is the failure mode.

Anything the parser cannot model is handed to the AI path (not covered here — it
needs an API key); the parser returning None for those is what routes them there.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.rule_translation_service import (               # noqa: E402
    parse_sql_case, _sql_result_to_template,
)
from app.transformations.engine import apply_rule                 # noqa: E402

_COLS = ["Country", "Worker_Type", "Employee_ID", "Position_ID"]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_a_country_code_case_parses_and_runs():
    sql = """
        CASE
          WHEN Country = 'Saudi Arabia' THEN 'SA'
          WHEN Country = 'United Arab Emirates' THEN 'AE'
          WHEN Country = 'Israel' THEN 'IL'
          ELSE ''
        END
    """
    res = parse_sql_case(sql, _COLS)
    check("it is a CASE_WHEN", res and res["rule_type"] == "CASE_WHEN")
    cfg = res["config"]
    check("three branches", len(cfg["branches"]) == 3, f"got {len(cfg['branches'])}")
    check("first branch is Country eq Saudi Arabia -> SA",
          cfg["branches"][0] == {"if_column": "Country", "op": "eq",
                                 "value": "Saudi Arabia", "then": "SA"},
          f"got {cfg['branches'][0]}")
    # And it RUNS.
    check("Israel -> IL",
          apply_rule("CASE_WHEN", cfg, "Israel", row={"Country": "Israel"}) == "IL")
    check("Mexico -> default blank",
          apply_rule("CASE_WHEN", cfg, "Mexico", row={"Country": "Mexico"}) == "")


def test_like_and_concat_the_employee_id_case():
    """The other rule from the same session: prefix Employee_ID by Worker_Type."""
    sql = ("CASE WHEN Worker_Type LIKE '%Employee%' THEN 'E' || Employee_ID "
           "WHEN Worker_Type LIKE '%Contingent%' THEN 'C' || Employee_ID "
           "ELSE Employee_ID END")
    res = parse_sql_case(sql, _COLS)
    cfg = res["config"]
    check("LIKE %..% becomes contains",
          cfg["branches"][0]["op"] == "contains"
          and cfg["branches"][0]["value"] == "Employee",
          f"got {cfg['branches'][0]}")
    check("'E' || Employee_ID becomes E{Employee_ID}",
          cfg["branches"][0]["then"] == "E{Employee_ID}",
          f"got {cfg['branches'][0]['then']!r}")
    check("the default is the bare column -> {Employee_ID}",
          cfg["default"] == "{Employee_ID}", f"got {cfg['default']!r}")
    # Runs end to end.
    r = apply_rule("CASE_WHEN", cfg, "P-1",
                   row={"Worker_Type": "Employee", "Employee_ID": "1001898"})
    check("Employee -> E1001898", r == "E1001898", f"got {r!r}")
    r2 = apply_rule("CASE_WHEN", cfg, "P-2",
                    row={"Worker_Type": "Contingent Worker", "Employee_ID": "7802"})
    check("Contingent -> C7802", r2 == "C7802", f"got {r2!r}")


def test_simple_case_form_with_an_operand():
    sql = "CASE Country WHEN 'Chile' THEN 'CL' WHEN 'South Africa' THEN 'ZA' END"
    res = parse_sql_case(sql, _COLS)
    cfg = res["config"]
    check("operand distributed to each branch",
          all(b["if_column"] == "Country" and b["op"] == "eq" for b in cfg["branches"]),
          f"got {cfg['branches']}")
    check("Chile -> CL",
          apply_rule("CASE_WHEN", cfg, "Chile", row={"Country": "Chile"}) == "CL")


def test_is_null_and_comparisons():
    sql = ("CASE WHEN Employee_ID IS NULL THEN 'MISSING' "
           "WHEN Employee_ID > 1000 THEN 'BIG' ELSE 'SMALL' END")
    cfg = parse_sql_case(sql, _COLS)["config"]
    check("IS NULL -> isblank", cfg["branches"][0]["op"] == "isblank")
    check("> 1000 -> gt", cfg["branches"][1]["op"] == "gt"
          and cfg["branches"][1]["value"] == "1000")
    check("blank id -> MISSING",
          apply_rule("CASE_WHEN", cfg, "", row={"Employee_ID": ""}) == "MISSING")
    check("2000 -> BIG",
          apply_rule("CASE_WHEN", cfg, "2000", row={"Employee_ID": "2000"}) == "BIG")


def test_not_a_case_returns_none_so_it_routes_to_ai():
    check("a plain SELECT is not a CASE",
          parse_sql_case("SELECT UPPER(name) FROM x", _COLS) is None)
    check("empty is None", parse_sql_case("", _COLS) is None)


def test_result_templating_helper():
    check("literal", _sql_result_to_template("'SA'") == "SA")
    check("bare column -> braces", _sql_result_to_template("Employee_ID") == "{Employee_ID}")
    check("concat", _sql_result_to_template("'E' || Employee_ID") == "E{Employee_ID}")
    check("concat both sides",
          _sql_result_to_template("'E' || Employee_ID || '-X'") == "E{Employee_ID}-X")
    check("CONCAT() function form",
          _sql_result_to_template("CONCAT('E', Employee_ID)") == "E{Employee_ID}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nSQL -> rule holds")
