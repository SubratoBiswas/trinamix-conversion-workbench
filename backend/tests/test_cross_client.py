"""Unit tests for cross-client mapping/crosswalk suggestion aggregation (pure)."""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.cross_client_service import aggregate_cross_client, _confidence  # noqa: E402


def _rows():
    return [
        {"client_id": "A", "target_field": "Supplier Number", "kind": "mapping", "rule_type": None,
         "original_value": "vendor_no", "resolved_value": "Supplier Number", "times_reused": 5},
        {"client_id": "B", "target_field": "Supplier Number", "kind": "mapping", "rule_type": None,
         "original_value": "vendor_no", "resolved_value": "Supplier Number", "times_reused": 2},
        {"client_id": "C", "target_field": "Supplier Number", "kind": "mapping", "rule_type": None,
         "original_value": "vendor_no", "resolved_value": "Supplier Number", "times_reused": 0},
        {"client_id": "D", "target_field": "Payment Terms", "kind": "crosswalk", "rule_type": None,
         "original_value": "Net 30", "resolved_value": "PMT_NET30", "times_reused": 1},
        {"client_id": "B", "target_field": "Payment Terms", "kind": "crosswalk", "rule_type": None,
         "original_value": "Net 30", "resolved_value": "PMT_NET30", "times_reused": 3},
        {"client_id": "D", "target_field": "Alias", "kind": "mapping", "rule_type": None,
         "original_value": "nickname", "resolved_value": "Alias", "times_reused": 1},
    ]


def test_multi_client_support_ranks_first():
    s = aggregate_cross_client(_rows(), exclude_client_id="D")
    assert s[0]["target_field"] == "Supplier Number"
    assert s[0]["support_clients"] == 3
    assert s[0]["confidence"] >= 0.85


def test_excludes_current_client_only_rows():
    s = aggregate_cross_client(_rows(), exclude_client_id="D")
    fields = {x["target_field"] for x in s}
    assert "Alias" not in fields          # only the current client D had it
    assert "Payment Terms" in fields      # supported by another client (B)


def test_already_used_here_flag():
    s = aggregate_cross_client(_rows(), exclude_client_id="D")
    pt = next(x for x in s if x["target_field"] == "Payment Terms")
    assert pt["already_used_here"] is True and pt["support_clients"] == 1


def test_confidence_monotonic_in_support():
    assert _confidence(1, 1) < _confidence(2, 2) < _confidence(3, 3) <= _confidence(4, 4)
    assert _confidence(0, 5) == 0.0


def test_empty_rows():
    assert aggregate_cross_client([], exclude_client_id="X") == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for t in tests:
        t(); print("PASS ", t.__name__); p += 1
    print(f"\n{p}/{len(tests)} passed")
