"""A CASE/WHEN result can build itself from other columns: ``E{Employee_ID}``.

REPORTED 05-Aug, with the live preview as the evidence: a rule whose branch result
was ``E{Employee_ID}`` shipped the literal text ``E{Employee_ID}`` — braces and
all — both in the preview and in the file. The analyst means "the letter E, then
this row's Employee_ID", the natural way to say "prefix the id with a letter that
depends on Worker Type" in one rule.

The engine returned every ``then`` verbatim, so no result value could reference
another column. This adds ``{Column}`` interpolation to the CASE_WHEN and
CONDITIONAL result values, and pins that literal results (SA, AE, a bare code)
still pass through untouched.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.transformations.engine import apply_rule                # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _worker_rule():
    return {
        "branches": [
            {"if_column": "Worker_Type", "op": "contains", "value": "Employee",
             "then": "E{Employee_ID}"},
            {"if_column": "Worker_Type", "op": "contains", "value": "Contingent",
             "then": "C{Employee_ID}"},
        ],
        "default": "",
    }


def test_a_branch_result_interpolates_another_column():
    r = apply_rule("CASE_WHEN", _worker_rule(), "P-1001898",
                   row={"Worker_Type": "Employee", "Employee_ID": "1001898"})
    check("E + the employee id", r == "E1001898", f"got {r!r}")

    r2 = apply_rule("CASE_WHEN", _worker_rule(), "P-1007802",
                    row={"Worker_Type": "Contingent Worker", "Employee_ID": "7802"})
    check("C + the employee id", r2 == "C7802", f"got {r2!r}")


def test_the_interpolated_column_name_is_matched_loosely():
    """The frame header and what the analyst typed rarely agree on case/spacing —
    an EBS path upper-cases headers, so a typed ``{Employee_ID}`` must still find
    ``EMPLOYEE_ID``. (The branch's own if_column is matched by the engine's
    existing resolver and is exact here so the branch fires.)"""
    r = apply_rule("CASE_WHEN", _worker_rule(), "x",
                   row={"Worker_Type": "Employee", "EMPLOYEE_ID": "42"})
    check("EMPLOYEE_ID fills {Employee_ID}", r == "E42", f"got {r!r}")


def test_a_literal_result_is_untouched():
    """The Country rule — SA, AE, IL — has no braces and must pass straight
    through, or fixing interpolation would break every plain code map."""
    cfg = {"branches": [
        {"if_column": "Country", "op": "eq", "value": "Saudi Arabia", "then": "SA"},
        {"if_column": "Country", "op": "eq", "value": "Israel", "then": "IL"},
    ], "default": ""}
    check("Saudi Arabia -> SA",
          apply_rule("CASE_WHEN", cfg, "Saudi Arabia",
                     row={"Country": "Saudi Arabia"}) == "SA")
    check("Israel -> IL",
          apply_rule("CASE_WHEN", cfg, "Israel", row={"Country": "Israel"}) == "IL")
    check("no match -> default", apply_rule("CASE_WHEN", cfg, "Mexico",
                                            row={"Country": "Mexico"}) == "")


def test_an_unknown_column_token_is_left_as_written():
    """A token that names no column on the row is NOT blanked — leaving it visible
    is what tells the analyst the column name is wrong, rather than silently
    shipping an empty cell."""
    cfg = {"branches": [{"if_column": "Worker_Type", "op": "contains",
                         "value": "Employee", "then": "E{Nonexistent}"}],
           "default": ""}
    r = apply_rule("CASE_WHEN", cfg, "x", row={"Worker_Type": "Employee"})
    check("unknown token stays literal", r == "E{Nonexistent}", f"got {r!r}")


def test_a_resolved_but_empty_column_becomes_empty():
    """A row where the id column exists but is blank -> just the prefix, "E"."""
    cfg = {"branches": [{"if_column": "Worker_Type", "op": "contains",
                         "value": "Employee", "then": "E{Employee_ID}"}],
           "default": ""}
    r = apply_rule("CASE_WHEN", cfg, "x",
                   row={"Worker_Type": "Employee", "Employee_ID": ""})
    check("empty id -> just the prefix", r == "E", f"got {r!r}")


def test_the_default_interpolates_too():
    cfg = {"branches": [{"if_column": "Worker_Type", "op": "eq", "value": "Z",
                         "then": "Z"}],
           "default": "X{Employee_ID}"}
    r = apply_rule("CASE_WHEN", cfg, "x",
                   row={"Worker_Type": "Employee", "Employee_ID": "9"})
    check("the default builds from the row too", r == "X9", f"got {r!r}")


def test_conditional_interpolates_its_chosen_value():
    cfg = {"if_column": "Worker_Type", "equals": "Employee",
           "then": "E{Employee_ID}", "else": "C{Employee_ID}"}
    hit = apply_rule("CONDITIONAL", cfg, "x",
                     row={"Worker_Type": "Employee", "Employee_ID": "5"})
    check("then interpolates", hit == "E5", f"got {hit!r}")
    miss = apply_rule("CONDITIONAL", cfg, "x",
                      row={"Worker_Type": "Contingent", "Employee_ID": "5"})
    check("else interpolates", miss == "C5", f"got {miss!r}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\ninterpolation holds")
