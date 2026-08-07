"""Employee HDL AssignmentNumber + WorkTermsAssignmentId (07-Aug follow-up).

AssignmentNumber rule: if Worker_Type=Employee -> E<id> else -> C<id>. A contingent
worker's Employee_ID already carries a letter prefix (C12345), so "C{Employee_ID}"
doubled it to CC12345. The analyst wants "C" + the DIGITS of the id. The new
{Column|digits} interpolation modifier expresses that. WorkTermsAssignmentId must be
"Worker_Terms_" + Employee_ID (was blank) — a literal-segment CONCAT.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.transformations import engine as E            # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


_ASSIGN = {"if_column": "Worker_Type", "equals": "Employee",
           "then": "E{Employee_ID|digits}", "else": "C{Employee_ID|digits}"}


def test_employee_keeps_E_plus_numeric_id():
    out = E.apply_rule("CONDITIONAL", _ASSIGN, "",
                       row={"Worker_Type": "Employee", "Employee_ID": "1200077"})
    check("employee -> E1200077", out == "E1200077", out)


def test_contingent_id_already_has_C_no_longer_doubles():
    out = E.apply_rule("CONDITIONAL", _ASSIGN, "",
                       row={"Worker_Type": "Contingent Worker", "Employee_ID": "C12345"})
    check("C12345 -> C12345 (single C)", out == "C12345", out)


def test_contingent_id_with_letters_gets_C_plus_digits():
    out = E.apply_rule("CONDITIONAL", _ASSIGN, "",
                       row={"Worker_Type": "Contingent Worker", "Employee_ID": "CW98765"})
    check("CW98765 -> C98765", out == "C98765", out)


def test_digits_modifier_strips_all_non_digits():
    check("mixed -> digits", E.apply_rule("CONDITIONAL",
          {"if_column": "t", "equals": "x", "then": "{v|digits}", "else": "{v|digits}"},
          "", row={"t": "x", "v": "AB-12.34"}) == "1234")


def test_workterms_assignment_id_is_worker_terms_plus_employee_id():
    wt = {"parts": [{"literal": "Worker_Terms_"}, {"col": "Employee_ID"}]}
    out = E.apply_rule("CONCAT", wt, "", row={"Employee_ID": "1200077"})
    check("Worker_Terms_1200077", out == "Worker_Terms_1200077", out)


def test_plain_token_without_modifier_unchanged():
    # The existing {Column} behaviour must be untouched.
    check("plain token still works",
          E.apply_rule("CONDITIONAL",
                       {"if_column": "t", "equals": "x", "then": "E{v}", "else": ""},
                       "", row={"t": "x", "v": "1200077"}) == "E1200077")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nAssignmentNumber C+digits and WorkTermsAssignmentId Worker_Terms_<id> hold.")
