"""Unit tests for fuzzy duplicate / entity resolution (dependency-light).

Exercises the same matching the endpoint uses (app.services.entity_resolution) —
no DB, network or model.
"""
import os
import sys

import pandas as pd

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.entity_resolution import (  # noqa: E402
    detect_identity_fields, find_duplicate_clusters, _str_sim,
)


def _supplier_df():
    return pd.DataFrame([
        {"Supplier Name*": "Acme Inc", "Supplier Number": "1001", "City": "Boston"},
        {"Supplier Name*": "ACME, Incorporated", "Supplier Number": "2002", "City": "Boston"},
        {"Supplier Name*": "Beta Industries", "Supplier Number": "3003", "City": "Denver"},
        {"Supplier Name*": "Beta Industries LLC", "Supplier Number": "3003", "City": "Denver"},
        {"Supplier Name*": "Zeta Corp", "Supplier Number": "9009", "City": "Austin"},
    ])


def test_identity_field_detection():
    fields = detect_identity_fields(_supplier_df(), "Supplier Import")
    cols = [f["column"] for f in fields]
    assert cols[0] == "Supplier Name*"          # name is the highest-weight anchor
    kinds = {f["column"]: f["kind"] for f in fields}
    assert kinds["Supplier Number"] == "number" and kinds["City"] == "city"


def test_fuzzy_name_match_across_different_keys():
    res = find_duplicate_clusters(_supplier_df(), "Supplier Import", threshold=0.80)
    assert res["cluster_count"] == 2
    joined = {tuple(sorted(m["values"]["Supplier Name*"] for m in c["members"]))
              for c in res["clusters"]}
    assert ("ACME, Incorporated", "Acme Inc") in joined        # differing numbers, still matched
    assert ("Beta Industries", "Beta Industries LLC") in joined
    # Zeta is unique — never clustered
    assert all("Zeta Corp" not in [m["values"]["Supplier Name*"] for m in c["members"]]
               for c in res["clusters"])


def test_confidence_and_evidence_present():
    res = find_duplicate_clusters(_supplier_df(), "Supplier Import", threshold=0.80)
    for c in res["clusters"]:
        assert 0.0 < c["confidence"] <= 1.0
        assert c["fields"] and c["size"] >= 2


def test_child_interface_is_skipped():
    res = find_duplicate_clusters(_supplier_df(), "Supplier Site", threshold=0.80)
    assert res["clusters"] == [] and "child interface" in (res.get("note") or "")


def test_no_name_column_returns_no_clusters():
    df = pd.DataFrame([{"Amount": "5"}, {"Amount": "6"}])
    res = find_duplicate_clusters(df, "Supplier Import", threshold=0.8)
    assert res["clusters"] == []


def test_similarity_bounds():
    assert _str_sim("Acme Inc", "ACME, Incorporated") >= 0.9
    assert _str_sim("Acme Inc", "Zeta Corp") < 0.3
    assert _str_sim("", "") == 0.0


def test_empty_frame():
    res = find_duplicate_clusters(pd.DataFrame(), "Supplier Import")
    assert res["clusters"] == [] and res["rows_scanned"] == 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    p = 0
    for t in tests:
        t(); print("PASS ", t.__name__); p += 1
    print(f"\n{p}/{len(tests)} passed")
