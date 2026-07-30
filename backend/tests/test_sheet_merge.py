"""CW #1 — several sources in one workbook, or one input split across sheets?

The report carried this as "Partial — needs a product decision" for good reason: the two
readings need opposite handling and the file cannot tell you which it is.

  * SEVERAL SOURCES (eBOS suppliers on one tab, NetSuite on another): a dataset and a
    conversion per sheet, converged at generation by the survivorship merge. The rows
    are peers, so the grains match.
  * ONE INPUT SPLIT UP (Customer on one tab, Address on another): one conversion over
    the union of the columns. The grains differ — 5,489 parties against 22,505
    addresses — so the sheets must be JOINED, not stacked.

On 30-Jul the analyst settled it: ask, and merge into a single conversion when it is one
input. This suite is mostly about the thing that makes merging safe. A row-order merge
would attach customer 1's address to customer 2 and the output would look immaculate, so
the join key is detected, scored on whether the values actually overlap, and the merge is
REFUSED when nothing scores. A refusal costs a conversation; a bad join costs a cutover.

Pure: pandas + stdlib.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.sheet_merge_service import (  # noqa: E402
    MODE_ONE, MODE_PER_SHEET, describe_choice, detect_join_keys, merge_sheets,
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


def _customer_address():
    """The real shape from the report: 3 parties, 5 addresses, joined on entityid.
    Both sheets carry a City column, which must NOT be allowed to collide."""
    cust = pd.DataFrame({"entityid": ["C1", "C2", "C3"],
                         "companyname": ["Acme", "Beta", "Gamma"],
                         "City": ["SF", "LA", "NY"]})
    addr = pd.DataFrame({"entityid": ["C1", "C1", "C2", "C3", "C3"],
                         "address_label": ["HQ", "Ship", "HQ", "HQ", "Ship"],
                         "City": ["San Francisco", "Oakland", "Los Angeles",
                                  "New York", "Newark"]})
    return {"Customer": cust, "Address": addr}


# ── Detecting the key ───────────────────────────────────────────────────────
def test_the_shared_key_is_found():
    c = detect_join_keys(_customer_address())
    top = c[0]
    check("entityid wins", top["column"] == "entityid", f"got {top['column']}")
    check("all its values overlap", top["value_overlap"] == 1.0)
    check("usable", top["usable"] is True)


def test_a_column_shared_by_name_but_not_by_value_is_rejected():
    """Both sheets have City and none of the values match. Joining on it produces a
    frame of nulls that is structurally perfect and completely wrong."""
    c = {x["column"]: x for x in detect_join_keys(_customer_address())}
    check("City found as a candidate", "City" in c)
    check("but not usable", c["City"]["usable"] is False,
          f"overlap {c['City']['value_overlap']}")


def test_a_one_to_many_key_is_not_penalised_out_of_contention():
    """entityid repeats on the address sheet — that is what one-to-many means. If
    uniqueness dominated the score, the only correct key would be rejected."""
    top = detect_join_keys(_customer_address())[0]
    check("still chosen despite repeating", top["column"] == "entityid")
    check("scored on the sheet where it IS unique", top["uniqueness"] == 1.0)
    check("and the repetition is still visible", top["one_to_many"] is True,
          f"min_uniqueness={top['min_uniqueness']}")


def test_a_single_sheet_has_nothing_to_detect():
    check("empty", detect_join_keys({"only": pd.DataFrame({"a": [1]})}) == [])


def test_an_all_blank_shared_column_is_not_a_key():
    a = pd.DataFrame({"k": ["", ""], "x": ["1", "2"]})
    b = pd.DataFrame({"k": ["", ""], "y": ["3", "4"]})
    check("refused", not [c for c in detect_join_keys({"A": a, "B": b})
                          if c["usable"]])


# ── Merging ─────────────────────────────────────────────────────────────────
def test_the_finer_grain_leads_the_join():
    """The output has to carry 22,505 addresses, not 5,489 parties — so the address
    sheet is primary. Leading with the coarser sheet silently drops rows."""
    df, rep = merge_sheets(_customer_address())
    check("Address is primary", rep["primary"] == "Address", f"got {rep['primary']}")
    check("all 5 address rows survive", rep["rows"] == 5, f"got {rep['rows']}")


def test_the_party_columns_reach_every_address_row():
    df, _rep = merge_sheets(_customer_address())
    check("companyname present", "companyname" in df.columns, f"got {list(df.columns)}")
    check("C1's two addresses both get Acme",
          list(df[df["entityid"] == "C1"]["companyname"]) == ["Acme", "Acme"],
          f"got {list(df[df['entityid'] == 'C1']['companyname'])}")


def test_a_colliding_column_name_is_kept_under_both_names():
    """Both sheets have City. Silently overwriting one is how a mapping ends up
    pointed at the wrong column with nothing on screen to show it."""
    df, _rep = merge_sheets(_customer_address())
    check("address City kept", "City" in df.columns)
    check("customer City kept separately", "Customer.City" in df.columns,
          f"got {list(df.columns)}")
    check("and they differ", list(df["City"])[0] != list(df["Customer.City"])[0])


def test_an_explicit_join_key_is_honoured():
    df, rep = merge_sheets(_customer_address(), join_key="entityid")
    check("used", rep["join_key"] == "entityid")
    check("joined", rep["rows"] == 5)


def test_a_wrong_but_present_key_is_reported_not_hidden():
    """A key that matched almost nothing produces a well-formed frame full of blanks.
    The frame cannot say it is wrong, so the report has to."""
    a = pd.DataFrame({"k": ["1", "2", "3", "4"], "x": ["a", "b", "c", "d"]})
    b = pd.DataFrame({"k": ["9", "8", "7", "1"], "y": ["p", "q", "r", "s"]})
    _df, rep = merge_sheets({"A": a, "B": b}, join_key="k")
    check("a warning is raised", bool(rep["warning"]), f"got {rep}")
    check("it names the column", "'k'" in rep["warning"], f"got {rep['warning']!r}")


def test_a_merge_with_no_usable_key_is_refused_with_the_reason():
    """The whole safety property. Two peer sheets with nothing in common must not be
    stitched together in row order."""
    a = pd.DataFrame({"vendor": ["A", "B"], "tax": ["1", "2"]})
    b = pd.DataFrame({"supplier_nm": ["C", "D"], "ein": ["3", "4"]})
    df, rep = merge_sheets({"eBOS": a, "NetSuite": b})
    check("nothing returned", df.empty)
    check("refused", bool(rep.get("error")))
    check("and says why in plain terms",
          "row order" in rep["error"], f"got {rep['error']!r}")
    check("with the candidates it considered", "candidates" in rep)


def test_duplicate_keys_on_the_joined_side_are_collapsed_and_counted():
    """Left-joining a right side with repeating keys multiplies the left. Keeping the
    first row is a real limitation, so it is reported rather than assumed harmless."""
    a = pd.DataFrame({"k": ["1", "2"], "x": ["a", "b"]})
    b = pd.DataFrame({"k": ["1", "1", "2"], "y": ["p", "q", "r"]})
    # `primary` is forced: left to itself the service leads with B, which has more rows
    # and is therefore the finer grain. This test is about the OTHER case — a right side
    # whose keys repeat, which would otherwise multiply the left.
    df, rep = merge_sheets({"A": a, "B": b}, join_key="k", primary="A")
    check("no row multiplication", len(df) == 2, f"got {len(df)}")
    joined = next(s for s in rep["sheets"] if s["role"] == "joined")
    check("the collapse is counted", joined["collapsed_duplicate_keys"] == 1,
          f"got {joined}")


def test_a_sheet_without_the_join_column_is_skipped_and_said_so():
    a = pd.DataFrame({"k": ["1"], "x": ["a"]})
    b = pd.DataFrame({"k": ["1"], "y": ["p"]})
    c = pd.DataFrame({"unrelated": ["z"]})
    _df, rep = merge_sheets({"A": a, "B": b, "C": c}, join_key="k")
    roles = {s["sheet"]: s["role"] for s in rep["sheets"]}
    check("C skipped", "no join column" in roles["C"], f"got {roles}")


def test_a_single_sheet_passes_through_unchanged():
    df, rep = merge_sheets({"only": pd.DataFrame({"a": [1, 2]})})
    check("2 rows", len(df) == 2)
    check("and says there was nothing to join", "nothing to join" in rep["note"])


def test_no_readable_sheets_reports_rather_than_raising():
    _df, rep = merge_sheets({"a": None, "b": pd.DataFrame()})
    check("error", bool(rep.get("error")), f"got {rep}")


# ── The question put to the analyst ─────────────────────────────────────────
def test_the_prompt_leans_towards_one_conversion_for_customer_plus_address():
    ch = describe_choice([{"sheet": "Customer", "rows": 3},
                          {"sheet": "Address", "rows": 5}],
                         detect_join_keys(_customer_address()))
    check("leans one_conversion", ch["suggested"] == MODE_ONE, f"got {ch['suggested']}")
    check("and gives the evidence", "entityid" in ch["why"], f"got {ch['why']!r}")
    check("both answers are offered",
          {o["mode"] for o in ch["options"]} == {MODE_ONE, MODE_PER_SHEET})


def test_the_prompt_leans_towards_per_sheet_for_two_peer_sources():
    a = pd.DataFrame({"vendor": ["A", "B"], "tax": ["1", "2"]})
    b = pd.DataFrame({"supplier_nm": ["C", "D"], "ein": ["3", "4"]})
    ch = describe_choice([{"sheet": "eBOS", "rows": 2}, {"sheet": "NetSuite", "rows": 2}],
                         detect_join_keys({"eBOS": a, "NetSuite": b}))
    check("leans per_sheet", ch["suggested"] == MODE_PER_SHEET, f"got {ch['suggested']}")


def test_an_ambiguous_workbook_suggests_nothing_rather_than_bluffing():
    """Same row count AND a shared key is genuinely undecidable from the file. A
    confident suggestion there would be a guess wearing evidence's clothes."""
    a = pd.DataFrame({"k": ["1", "2"], "x": ["a", "b"]})
    b = pd.DataFrame({"k": ["1", "2"], "y": ["p", "q"]})
    ch = describe_choice([{"sheet": "A", "rows": 2}, {"sheet": "B", "rows": 2}],
                         detect_join_keys({"A": a, "B": b}))
    check("no lean", ch["suggested"] is None, f"got {ch['suggested']}")
    check("and says it is ambiguous", "ambiguous" in ch["why"], f"got {ch['why']!r}")


def test_the_question_is_asked_in_the_analysts_terms():
    ch = describe_choice([{"sheet": "A", "rows": 1}], [])
    q = ch["question"].lower()
    check("names both readings", "sources" in q and "split across sheets" in q,
          f"got {ch['question']!r}")


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
