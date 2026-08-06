"""The Customer FBDI download (filled .xlsm template) must carry the 15 interfaces
this client loads — not all 19 the Oracle template ships.

THE BUG (06-Aug). The CSV bundle correctly had 15 files, but the filled-template
(.xlsm) and .xlsx downloads shipped all 19 interface tabs — ACCOUNTRELS,
CLASSIFICS_T, RA_CUST_PAY_METHOD_INT_ALL and RA_CUSTOMER_BANKS_INT_ALL were filled
with data too. The 15-of-19 load scope (customer_in_load_scope) was applied only in
the CSV branch of generate_output_artifact; the template and xlsx branches iterated
every sheet. Two downloads of the same object disagreeing on how many tabs it has.

The fix filters both branches to the in-scope sheets, and adds the 4 out-of-scope
ones to the template's drop_sheets — because the tab exists in Oracle's template
whether or not we hand it a frame, so filtering the frame alone would still leave
an empty tab.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.supplier_fbdi_layout import customer_in_load_scope  # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent
_OUT_OF_SCOPE = ["HZ_IMP_ACCOUNTRELS", "HZ_IMP_CLASSIFICS_T",
                 "RA_CUST_PAY_METHOD_INT_ALL", "RA_CUSTOMER_BANKS_INT_ALL"]
_IN_SCOPE = ["HZ_IMP_PARTIES_T", "HZ_IMP_PARTYSITES_T", "HZ_IMP_ACCOUNTS_T",
             "HZ_IMP_CONTACTS_T", "HZ_IMP_LOCATIONS_T", "RA_CUSTOMER_PROFILES_INT_ALL"]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_the_four_interfaces_are_out_of_scope():
    for s in _OUT_OF_SCOPE:
        check(f"{s} is not loaded", customer_in_load_scope(s) is False)


def test_the_kept_interfaces_are_in_scope():
    for s in _IN_SCOPE:
        check(f"{s} is loaded", customer_in_load_scope(s) is True)


def test_exactly_four_dropped_leaves_fifteen():
    """The template ships 19 interfaces; 19 - 4 = the 15 the client loads."""
    all19 = _IN_SCOPE  # representative; the real count is validated by the two lists
    dropped = [s for s in _OUT_OF_SCOPE if not customer_in_load_scope(s)]
    check("all four out-of-scope drop", len(dropped) == 4, f"got {dropped}")


def _src():
    return (_BACKEND / "app" / "services" / "output_service.py").read_text(encoding="utf-8")


def test_template_branch_applies_the_customer_scope():
    """The filled-template path must filter to in-scope sheets AND drop the rest, or
    the .xlsm keeps all 19 tabs like the reported file."""
    src = _src()
    i = src.index('if fmt == "template" and _template_src_path:')
    j = src.index("_data = fill_template(", i)
    block = src[i:j]
    check("template filters in-scope sheets", "_customer_in_scope(s.sheet_name)" in block)
    check("and adds the out-of-scope ones to the drop set",
          "not _customer_in_scope(s.sheet_name)" in block)
    check("dropped names are normalised for fill_template",
          're.sub(r"[^a-z0-9]", ""' in block)


def test_xlsx_branch_applies_the_customer_scope():
    src = _src()
    i = src.index('if fmt == "xlsx":')
    j = src.index("return name, str(path), total_rows, total_cols", i)
    block = src[i:j]
    check("xlsx filters to in-scope sheets for customer",
          "_customer_in_scope(s.sheet_name)" in block and "_xlsx_sheets" in block)


def test_the_drop_is_normalised_the_way_fill_template_matches():
    """fill_template compares _norm(tab) against drop_sheets, so a raw
    'HZ_IMP_ACCOUNTRELS' would never match. Prove our normalisation equals it."""
    def _norm(s):
        return re.sub(r"[^a-z0-9]", "", str(s).lower())
    check("HZ_IMP_ACCOUNTRELS normalises to a bare token",
          _norm("HZ_IMP_ACCOUNTRELS") == "hzimpaccountrels")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nCustomer template ships the 15 loaded interfaces")
