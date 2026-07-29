"""Required-field gate for Supplier.

Pure: pandas + stdlib. Fixtures use the analyst's own sheet and field names so a
change to the curated list shows up here rather than at cutover.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.required_fields_service import (  # noqa: E402
    STATUS_EMPTY, STATUS_MISSING, STATUS_OK, STATUS_PARTIAL,
    check_frame, check_sheets, explain, load_required,
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


# ── The curated list ─────────────────────────────────────────────────────────
def test_supplier_list_loads():
    req = load_required("Supplier")
    check("sheets found", len(req) == 6, f"got {sorted(req)}")
    check("Supplier Import needs the name", req["Supplier Import"] == ["Supplier Name"])
    check("Address needs Address Name",
          "Address Name" in req["Supplier Address Import"])
    check("Site needs Remit-to Supplier",
          "Remit-to Supplier" in req["Supplier Site Import"])
    check("Contact needs File/Text/URL",
          "File/Text/URL" in req["Supplier Contact Import"])


def test_repeated_rows_are_collapsed():
    """The uploaded list repeats fields because one sheet covers several record
    blocks. The sheet still requires each field, but only once."""
    bank = load_required("Supplier")["Supplier Bank Import"]
    check("no duplicates", len(bank) == len(set(bank)), f"got {bank}")
    check("all three identifiers present",
          {"Payee Identifier", "Payee Bank Account Identifier",
           "Payee Bank Account Assignment Identifier"} <= set(bank), f"got {bank}")


def test_unknown_object_returns_nothing():
    check("no list for Item yet", load_required("Item") == {})


# ── Field-level status ───────────────────────────────────────────────────────
def test_all_populated_is_ok():
    df = pd.DataFrame([{"Supplier Name": "Acme"}, {"Supplier Name": "Beta"}])
    r = check_frame(df, ["Supplier Name"])[0]
    check("status ok", r["status"] == STATUS_OK, f"got {r}")
    check("counts right", r["present"] == 2 and r["blank"] == 0)


def test_absent_column_is_missing():
    df = pd.DataFrame([{"Supplier Name": "Acme"}])
    r = check_frame(df, ["Address Name"])[0]
    check("status missing", r["status"] == STATUS_MISSING, f"got {r}")


def test_present_but_wholly_empty_is_empty():
    """The common real case: the field is mapped, the source column exists, and
    every value is blank. A mapping-level check would call this satisfied."""
    df = pd.DataFrame([{"Address Name": ""}, {"Address Name": "  "}])
    r = check_frame(df, ["Address Name"])[0]
    check("status empty", r["status"] == STATUS_EMPTY, f"got {r}")
    check("nothing present", r["present"] == 0)


def test_some_blanks_is_partial():
    df = pd.DataFrame([{"Supplier Site": "US-Austin"}, {"Supplier Site": ""}])
    r = check_frame(df, ["Supplier Site"])[0]
    check("status partial", r["status"] == STATUS_PARTIAL, f"got {r}")
    check("counts split", r["present"] == 1 and r["blank"] == 1)


def test_nan_and_none_count_as_blank():
    df = pd.DataFrame([{"Supplier Name": None}, {"Supplier Name": float("nan")},
                       {"Supplier Name": "NULL"}])
    r = check_frame(df, ["Supplier Name"])[0]
    check("all three are blank", r["status"] == STATUS_EMPTY, f"got {r}")


def test_column_matching_ignores_case_and_punctuation():
    df = pd.DataFrame([{"SUPPLIER  NAME": "Acme"}])
    r = check_frame(df, ["Supplier Name"])[0]
    check("matched anyway", r["status"] == STATUS_OK, f"got {r}")
    check("real column reported", r["column"] == "SUPPLIER  NAME")


def test_slashed_field_name_matches():
    """File/Text/URL must not be mangled by the normaliser."""
    df = pd.DataFrame([{"File/Text/URL": "x"}])
    r = check_frame(df, ["File/Text/URL"])[0]
    check("matched", r["status"] == STATUS_OK, f"got {r}")


# ── Sheet-level gate ─────────────────────────────────────────────────────────
def test_missing_field_blocks():
    frames = {"Supplier Import": pd.DataFrame([{"Supplier Number": "1"}])}
    res = check_sheets(frames, {"Supplier Import": ["Supplier Name"]})
    check("blocked", res["blocked"] is True)
    check("one failure", res["failed_count"] == 1, f"got {res['failed_count']}")
    check("names sheet and field",
          res["failures"][0] == {"sheet": "Supplier Import", "field": "Supplier Name"})


def test_partial_does_not_block():
    """Some Oracle sheets legitimately carry optional child rows. Blocking on a
    partial gap would make the gate unusable, and an unusable gate gets switched
    off — which is worse than a gate that only stops guaranteed failures."""
    frames = {"Supplier Site Import": pd.DataFrame(
        [{"Supplier Site": "US-Austin"}, {"Supplier Site": ""}])}
    res = check_sheets(frames, {"Supplier Site Import": ["Supplier Site"]})
    check("not blocked", res["blocked"] is False)
    check("still reported", res["partial_count"] == 1)


def test_a_sheet_that_was_never_generated_fails():
    """Skipping it would report a clean pass on a bundle missing a whole sheet."""
    res = check_sheets({}, {"Supplier Bank Import": ["Account Number"]})
    check("blocked", res["blocked"] is True)
    check("flagged as not generated",
          res["sheets"][0]["sheet_generated"] is False)


def test_everything_populated_passes():
    frames = {"Supplier Import": pd.DataFrame([{"Supplier Name": "Acme"}])}
    res = check_sheets(frames, {"Supplier Import": ["Supplier Name"]})
    check("not blocked", res["blocked"] is False)
    check("no failures", res["failed_count"] == 0)


def test_counts_cover_every_sheet():
    frames = {"Supplier Import": pd.DataFrame([{"Supplier Name": "Acme"}]),
              "Supplier Address Import": pd.DataFrame([{"Supplier Name": "Acme"}])}
    spec = {"Supplier Import": ["Supplier Name"],
            "Supplier Address Import": ["Supplier Name", "Address Name"]}
    res = check_sheets(frames, spec)
    check("three required in total", res["required_total"] == 3)
    check("one failure — the missing Address Name", res["failed_count"] == 1,
          f"got {res['failures']}")


# ── The popup line ───────────────────────────────────────────────────────────
def test_explain_names_the_first_failure():
    res = check_sheets({}, {"Supplier Import": ["Supplier Name", "Supplier Number"]})
    msg = explain(res)
    check("names sheet and field", "Supplier Import" in msg and "Supplier Name" in msg,
          f"got {msg}")
    check("counts the rest", "1 more" in msg, f"got {msg}")
    check("says why it matters", "rejects" in msg.lower(), f"got {msg}")


def test_explain_when_clean():
    frames = {"Supplier Import": pd.DataFrame([{"Supplier Name": "Acme"}])}
    res = check_sheets(frames, {"Supplier Import": ["Supplier Name"]})
    check("positive message", "All required fields are populated" in explain(res),
          f"got {explain(res)}")


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
