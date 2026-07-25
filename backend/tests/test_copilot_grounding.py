"""Unit tests for the grounded copilot's pure intent answerer."""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.copilot_grounding import answer_from_facts  # noqa: E402

FACTS = {
    "name": "eBOS Supplier", "target_object": "Supplier Import",
    "required_total": 10, "required_covered": 8,
    "unmapped_required": ["Business Relationship", "Tax Organization Type"],
    "mapped": [
        {"target_field": "Supplier Name", "required": True, "source_column": "vendor_name",
         "default_value": None, "status": "approved", "provenance": "mapped (approved)"},
        {"target_field": "Invoice Match Option", "required": False, "source_column": None,
         "default_value": None, "status": None, "provenance": "unmapped (gap)"},
        {"target_field": "Business Relationship", "required": True, "source_column": None,
         "default_value": "PROSPECTIVE", "status": None, "provenance": "constant/default"},
    ],
    "dq": {"hard_error_count": 2, "warning_count": 5,
           "top_issues": [{"field_name": "Supplier Number", "issue_type": "Missing Required Field"}],
           "generated": True},
    "readiness": {"score": 68, "band": "Needs minor work", "effort": "Low",
                  "coverage_pct": 80, "est_hours": 2.5},
}


def test_field_blank_provenance():
    r = answer_from_facts(FACTS, "why is Invoice Match Option blank?")
    assert r["intent"] == "field_provenance"
    assert "no source column" in r["answer"].lower()
    assert any("Invoice Match Option" in c for c in r["citations"])


def test_field_mapped_source():
    r = answer_from_facts(FACTS, "how is Supplier Name mapped?")
    assert r["intent"] == "field_provenance" and "vendor_name" in r["answer"]


def test_field_defaulted():
    r = answer_from_facts(FACTS, "why is Business Relationship blank?")
    assert "PROSPECTIVE" in r["answer"]


def test_unmapped_required():
    r = answer_from_facts(FACTS, "which required fields are unmapped?")
    assert r["intent"] == "unmapped_required"
    assert "Business Relationship" in r["answer"] and "Tax Organization Type" in r["answer"]


def test_dq_reject():
    r = answer_from_facts(FACTS, "what will Oracle reject?")
    assert r["intent"] == "dq" and "2 hard error" in r["answer"]


def test_dq_not_generated():
    f = {**FACTS, "dq": {"hard_error_count": 0, "warning_count": 0, "top_issues": [], "generated": False}}
    r = answer_from_facts(f, "any data quality problems?")
    assert "no output has been generated" in r["answer"].lower()


def test_readiness():
    r = answer_from_facts(FACTS, "how ready is this for cutover?")
    assert r["intent"] == "readiness" and "68/100" in r["answer"]


def test_summary_default():
    r = answer_from_facts(FACTS, "tell me about this conversion")
    assert r["intent"] == "summary" and "8/10" in r["answer"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for t in tests:
        t(); print("PASS ", t.__name__); p += 1
    print(f"\n{p}/{len(tests)} passed")
