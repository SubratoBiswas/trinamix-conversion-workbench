"""Unit tests for object-readiness / effort scoring (pure scorer)."""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.readiness_service import score_readiness  # noqa: E402


def test_fully_ready():
    r = score_readiness(dict(required_total=20, required_covered=20, has_source=True,
                             has_gold=True, output_generated=True, last_load_status="passed",
                             dq_hard_errors=0))
    assert r["band"] == "Ready" and r["score"] >= 85 and r["effort"] == "None"


def test_hard_errors_block():
    r = score_readiness(dict(required_total=20, required_covered=18, has_source=True,
                             output_generated=True, dq_hard_errors=3))
    assert r["band"] == "Blocked" and r["open_items"] >= 3


def test_no_source_is_not_started():
    r = score_readiness(dict(required_total=20, required_covered=0, has_source=False))
    assert r["band"] == "Not started"


def test_low_coverage_with_source_blocks():
    r = score_readiness(dict(required_total=20, required_covered=4, has_source=True))
    assert r["band"] == "Blocked" and r["coverage_pct"] == 20


def test_failed_load_blocks():
    r = score_readiness(dict(required_total=10, required_covered=10, has_source=True,
                             output_generated=True, last_load_status="failed", dq_hard_errors=0))
    assert r["band"] == "Blocked"


def test_effort_tiers():
    assert score_readiness(dict(required_total=5, required_covered=5, has_source=True))["effort"] == "None"
    assert score_readiness(dict(required_total=10, required_covered=7, has_source=True))["effort"] == "Low"
    assert score_readiness(dict(required_total=30, required_covered=10, has_source=True))["effort"] == "High"


def test_score_monotonic_in_coverage():
    lo = score_readiness(dict(required_total=20, required_covered=5, has_source=True))["score"]
    hi = score_readiness(dict(required_total=20, required_covered=18, has_source=True))["score"]
    assert hi > lo


def test_factors_present():
    r = score_readiness(dict(required_total=4, required_covered=2, has_source=True))
    labels = {f["label"] for f in r["factors"]}
    assert {"Source data", "Required fields", "Data quality", "Output", "Last load"} <= labels


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for t in tests:
        t(); print("PASS ", t.__name__); p += 1
    print(f"\n{p}/{len(tests)} passed")
