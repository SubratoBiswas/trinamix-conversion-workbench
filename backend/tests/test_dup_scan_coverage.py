"""Duplicate scan must not report "0 duplicates" for work it never did.

Found while verifying the duplicate review live on 29-Jul. A Customer conversion
built from two datasets — "Customer Dump Latest available" and "… - Copy" — returned
``cluster_count: 0`` with ``truncated: false`` and no note. The zero turned out to be
defensible (the merge de-duplicates before the fuzzy pass), but reading the scanner
showed it could not have told me otherwise: blocking dropped any name group larger
than ``max_block`` **silently**, so on a large extract "no duplicates found" and
"thousands of rows were never examined" produced identical output.

Blocking is unavoidable — comparison is O(block²), and a 2,000-row group is four
million pair scores. So the fix is not to compare everything pairwise; it is to fall
back to the sorted-neighbourhood method for oversized groups (sort by name, compare
each row against its nearest neighbours) and to SAY when that happened. §10.7's rule
about silent caps, applied one layer down from ``hidden_count``.

Pure: pandas + stdlib.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.entity_resolution import find_duplicate_clusters  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def _frame(names, city="Fremont"):
    return pd.DataFrame({
        "Supplier Name": names,
        "Taxpayer ID": [""] * len(names),
        "City": [city] * len(names),
    })


# ── A duplicate hiding inside an oversized block ─────────────────────────────
def test_a_duplicate_in_an_oversized_name_group_is_still_found():
    """The regression that matters. 500 companies all starting "ACME" put every one
    of them in one 4-char block; at max_block=400 the whole block used to be dropped,
    including the genuine pair planted in it.
    """
    names = [f"ACME Holdings {i:04d}" for i in range(500)]
    names += ["Zenith Fabrication Inc", "Zenith Fabrication, Inc."]
    res = find_duplicate_clusters(_frame(names), "Supplier", max_block=400)
    check("the planted pair is found", res["cluster_count"] >= 1,
          f"got {res['cluster_count']} clusters, note={res.get('coverage_note')!r}")
    found = {v["Supplier Name"] for c in res["clusters"] for v in
             [m["values"] for m in c["members"]]}
    check("it is the Zenith pair",
          {"Zenith Fabrication Inc", "Zenith Fabrication, Inc."} <= found,
          f"got {sorted(found)[:6]}")


def test_no_row_in_an_oversized_group_is_dropped():
    """Every row in a large group is still examined — by the windowed method rather
    than pairwise, but examined. The old code compared none of them."""
    names = [f"ACME Holdings {i:04d}" for i in range(500)]
    res = find_duplicate_clusters(_frame(names), "Supplier", max_block=400)
    check("all rows compared", res["rows_compared"] == 500,
          f"compared {res['rows_compared']} of {len(names)}")
    check("recorded as windowed", res["rows_windowed"] == 500,
          f"got {res['rows_windowed']}")
    check("500 distinct names produce no false clusters",
          res["cluster_count"] == 0, f"got {res['cluster_count']}")


# ── The reduced method must be declared, not hidden ──────────────────────────
def test_a_windowed_group_says_so():
    """Windowing trades some recall for cost. The trade is acceptable; making it
    without telling the analyst is not."""
    names = ["Same Name Corp"] * 500
    res = find_duplicate_clusters(_frame(names), "Supplier", max_block=100)
    check("counted", res["rows_windowed"] == 500, f"got {res['rows_windowed']}")
    check("blocks counted", res["windowed_blocks"] >= 1)
    check("and named in the note",
          "nearest neighbours" in (res["coverage_note"] or ""),
          f"note={res.get('coverage_note')!r}")
    check("the identical names still cluster", res["cluster_count"] >= 1,
          f"got {res['cluster_count']}")


def test_rows_with_no_anchor_value_are_reported():
    """An unmapped conversion has a blank name column. Those rows cannot be matched
    by name, and saying "0 duplicates" about them is misleading."""
    res = find_duplicate_clusters(
        _frame(["Alpha Corp", "Alpha Corporation", "", "", ""]), "Supplier")
    check("counted", res["rows_without_anchor"] == 3,
          f"got {res['rows_without_anchor']}")
    check("named in the note", "no value in" in (res["coverage_note"] or ""),
          f"note={res.get('coverage_note')!r}")


def test_a_clean_scan_says_nothing_extra():
    """The note must stay empty when coverage was complete, or it becomes noise the
    analyst learns to ignore."""
    res = find_duplicate_clusters(_frame(["Alpha Corp", "Beta Ltd", "Gamma Plc"]),
                                  "Supplier")
    check("no note", not res.get("coverage_note"), f"got {res.get('coverage_note')!r}")
    check("nothing windowed", res["rows_windowed"] == 0)
    check("all rows had a name", res["rows_without_anchor"] == 0)


# ── Unchanged behaviour ──────────────────────────────────────────────────────
def test_the_ordinary_duplicate_pair_still_clusters():
    res = find_duplicate_clusters(
        _frame(["CRRC Sifang America Inc", "CRRC Sifang America Inc.",
                "Unrelated Trading Co"]), "Supplier")
    check("one cluster", res["cluster_count"] == 1, f"got {res['cluster_count']}")
    check("of two rows", res["clusters"][0]["size"] == 2)


def test_counts_are_present_even_on_an_empty_frame():
    """Callers read these keys unconditionally; a missing key is a 500 in the UI."""
    res = find_duplicate_clusters(pd.DataFrame(), "Supplier")
    for k in ("clusters", "rows_scanned", "identity_fields"):
        check(f"{k} present", k in res)


def test_a_child_interface_is_still_skipped_with_its_reason():
    res = find_duplicate_clusters(_frame(["A Corp", "A Corp"]), "Supplier Site")
    check("no clusters", res["clusters"] == [])
    check("and says why", "child interface" in (res.get("note") or ""))


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
