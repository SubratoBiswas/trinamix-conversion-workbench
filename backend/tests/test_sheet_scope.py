"""Per-sheet scoping of a learning.

CW_Issues 29/07 raised five variants of one problem: a learning is keyed by
target field NAME, and Oracle repeats names across interface sheets (Customer has
19), so approving one mapping applied it everywhere.

    #11  approving a mapping hits every sheet; bank / pay should be "not applicable"
    #12  id -> Party Original System Reference, all sheets EXCEPT HZ_IMP_CLASSIFICS_T
    #13  entityid -> Account Number, EXCEPT three named sheets
    #14  internalid -> Account Site SSR, EXCEPT four named sheets
    #25  Receipt Method / Start Date on RA_CUSTOMER_BANKS_INT_ALL must stay blank

Pure: stdlib only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.learning_service import sheet_allowed  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


class L:
    def __init__(self, sheets=None, exclude_sheets=None):
        self.sheets = sheets or []
        self.exclude_sheets = exclude_sheets or []


# ── Default: unchanged behaviour ─────────────────────────────────────────────
def test_no_scope_means_every_sheet():
    """Every learning captured before this existed has empty lists, so switching
    it on must change nothing until someone narrows one deliberately."""
    lm = L()
    for s in ("HZ_IMP_PARTIES_T", "RA_CUSTOMER_BANKS_INT_ALL", None, ""):
        check(f"allowed on {s!r}", sheet_allowed(lm, s))


# ── Exclusion (issues 12, 13, 14, 25) ────────────────────────────────────────
def test_excluded_sheet_is_refused():
    lm = L(exclude_sheets=["HZ_IMP_CLASSIFICS_T"])
    check("excluded sheet refused", not sheet_allowed(lm, "HZ_IMP_CLASSIFICS_T"))
    check("every other sheet still allowed", sheet_allowed(lm, "HZ_IMP_PARTIES_T"))


def test_several_exclusions():
    """Issue #14: internalid everywhere except four named sheets."""
    lm = L(exclude_sheets=["RA_CUSTOMER_BANKS_INT_ALL", "RA_CUST_PAY_METHOD_INT_ALL",
                           "RA_CUSTOMER_PROFILES_INT_ALL", "HZ_IMP_ACCTCONTACTS_T"])
    for s in lm.exclude_sheets:
        check(f"{s} refused", not sheet_allowed(lm, s))
    check("HZ_IMP_ADDRESSES_T still allowed", sheet_allowed(lm, "HZ_IMP_ADDRESSES_T"))


def test_matching_ignores_case_and_punctuation():
    """The analyst types sheet names by hand; a stray space or case difference
    must not silently turn an exclusion into a no-op."""
    lm = L(exclude_sheets=["ra_customer_banks_int_all"])
    for spelling in ("RA_CUSTOMER_BANKS_INT_ALL", "RA CUSTOMER BANKS INT ALL",
                     " ra_customer_banks_int_all "):
        check(f"{spelling!r} refused", not sheet_allowed(lm, spelling))


# ── Inclusion ────────────────────────────────────────────────────────────────
def test_allow_list_restricts_to_named_sheets():
    lm = L(sheets=["HZ_IMP_PARTIES_T", "HZ_IMP_ACCOUNTS_T"])
    check("named sheet allowed", sheet_allowed(lm, "HZ_IMP_ACCOUNTS_T"))
    check("unnamed sheet refused", not sheet_allowed(lm, "HZ_IMP_ADDRESSES_T"))


def test_exclusion_wins_over_inclusion():
    """A sheet in both lists is refused — listing it under "never" is the
    stronger statement of intent."""
    lm = L(sheets=["HZ_IMP_PARTIES_T"], exclude_sheets=["HZ_IMP_PARTIES_T"])
    check("refused", not sheet_allowed(lm, "HZ_IMP_PARTIES_T"))


# ── Unknown sheet ────────────────────────────────────────────────────────────
def test_unknown_sheet_with_only_exclusions_is_allowed():
    """Nothing says this is one of the excluded sheets, and refusing would drop
    coverage for templates whose sheet names could not be resolved."""
    check("allowed", sheet_allowed(L(exclude_sheets=["X_T"]), None))


def test_unknown_sheet_with_an_allow_list_is_refused():
    """It cannot be shown to be in the list, and applying a deliberately narrowed
    learning to an unidentified sheet is the failure this whole feature exists
    to prevent."""
    check("refused", not sheet_allowed(L(sheets=["HZ_IMP_PARTIES_T"]), None))


def test_blank_entries_are_ignored():
    lm = L(sheets=["", "   "], exclude_sheets=[""])
    check("treated as no scope at all", sheet_allowed(lm, "ANY_SHEET_T"))


def test_a_learning_object_without_the_fields_is_allowed():
    """Rows written before the fields existed have no attributes at all."""
    class Old:
        pass
    check("legacy row allowed", sheet_allowed(Old(), "HZ_IMP_PARTIES_T"))


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
