"""Post-mapping report: layer attribution and pass/fail rollups.

Pure: stdlib only, plain dicts in. The layer classifier mirrors the canvas one,
so these tests are also the guard against the two copies drifting apart.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.mapping_report_service import (  # noqa: E402
    AI, DEFAULT, DETERMINISTIC, GOLD, LEARNED, MANUAL, SUPPRESSED, UNMAPPED,
    WORKBOOK, build_report, classify_layer, summarize_layers,
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


def fld(i, name, required=False):
    return {"id": i, "field_name": name, "required": required}


def mp(fid, source="src", reason="", status="suggested", conf=0.9, **kw):
    return {"target_field_id": fid, "source_column": source, "reason": reason,
            "status": status, "confidence": conf, **kw}


# ── Layer attribution ────────────────────────────────────────────────────────
def test_each_layer_is_recognised():
    f = fld(1, "Supplier Name")
    cases = [
        (mp(1, reason='captured from "gold example"'), GOLD),
        (mp(1, reason="from the learning library"), LEARNED),
        (mp(1, reason="mapping workbook: NXT Supplier.xlsx"), WORKBOOK),
        (mp(1, reason="mapping document: NXT Supplier.xlsx"), WORKBOOK),
        (mp(1, status="overridden"), MANUAL),
        (mp(1, conf=0.9), DETERMINISTIC),
        (mp(1, conf=0.2), AI),
    ]
    for m, want in cases:
        got = classify_layer(m, f)
        check(f"{want} recognised", got == want, f"got {got} for {m.get('reason')!r}")


def test_precedence_beats_alphabetical_order():
    """A gold-sourced mapping with low confidence is still gold, not AI — the
    ordering is the point of the classifier."""
    f = fld(1, "Supplier Name")
    m = mp(1, reason='captured from "gold example"', conf=0.1)
    check("gold outranks low confidence", classify_layer(m, f) == GOLD)


def test_an_override_outranks_its_origin():
    f = fld(1, "Supplier Name")
    m = mp(1, reason="from the learning library", status="overridden")
    check("analyst wins", classify_layer(m, f) == MANUAL)


def test_unmapped_versus_default():
    f = fld(1, "Tax Organization Type")
    check("no source, no default -> unmapped",
          classify_layer({"source_column": ""}, f) == UNMAPPED)
    check("no source but a default -> default",
          classify_layer({"source_column": "", "default_value": "CORPORATION"}, f)
          == DEFAULT)


def test_effective_defaults_count_as_a_default():
    """A default living only in the effective-defaults layer still populates the
    file — QA issue #2 was exactly this gap."""
    f = fld(1, "Payment Method")
    check("matched by normalised name",
          classify_layer({"source_column": ""}, f,
                         {"paymentmethod": "G-Treasury"}) == DEFAULT)


def test_suppressed_is_not_unmapped():
    f = fld(1, "Inactive Date")
    check("deliberately blank",
          classify_layer({"status": "not_applicable"}, f) == SUPPRESSED)


def test_a_custom_rule_counts_as_analyst_work():
    f = fld(1, "Supplier Site")
    check("rule id makes it manual", classify_layer(mp(1), f, None, True) == MANUAL)
    check("an inline transformation too",
          classify_layer(mp(1, suggested_transformation={"rule_type": "CONCAT"}), f)
          == MANUAL)


# ── Rollup ───────────────────────────────────────────────────────────────────
def test_summary_counts_and_unattested():
    fields = [fld(1, "A"), fld(2, "B"), fld(3, "C"), fld(4, "D", required=True)]
    maps = [mp(1, reason='from "gold example"'), mp(2, conf=0.9), mp(3, conf=0.2)]
    s = summarize_layers(fields, maps)
    check("4 fields", s["total_fields"] == 4)
    check("3 mapped", s["mapped"] == 3, f"got {s['mapped']}")
    check("2 unattested (deterministic + ai)", s["unattested"] == 2,
          f"got {s['unattested']}")
    check("the unmapped required field is named",
          s["required_unmapped"] == ["D"], f"got {s['required_unmapped']}")


def test_suppressed_does_not_count_as_mapped():
    fields = [fld(1, "A"), fld(2, "B")]
    maps = [mp(1), {"target_field_id": 2, "status": "not_applicable"}]
    s = summarize_layers(fields, maps)
    check("only one mapped", s["mapped"] == 1, f"got {s['mapped']}")


# ── The report ───────────────────────────────────────────────────────────────
def _report(**kw):
    base = dict(
        conversion={"id": "c1", "target_object": "Supplier"},
        fields=[fld(1, "Supplier Name", required=True), fld(2, "Supplier Site")],
        mappings=[mp(1, reason="mapping workbook: x.xlsx")],
    )
    base.update(kw)
    return build_report(**base)


def test_report_rolls_up_validation_and_cleansing():
    dq = {"issues": [{"severity": "error", "issue_type": "Value Not In Value Set",
                      "impacted_count": 12},
                     {"severity": "warning", "issue_type": "Exceeds Max Length",
                      "impacted_count": 3}],
          "cleansing_fixes": [{"field": "Supplier Name", "rule": "whitespace_punct",
                               "count": 4713},
                              {"field": "City", "rule": "special_chars", "count": 1941}],
          "hard_error_count": 1}
    r = _report(dq_report=dq)
    check("validation failures counted", r["validation"]["failed"] == 1,
          f"got {r['validation']}")
    check("warnings separated", r["validation"]["warnings"] == 1)
    check("cleansing rules counted", r["cleansing"]["rules_fired"] == 2)
    check("values changed summed", r["cleansing"]["values_changed"] == 6654,
          f"got {r['cleansing']['values_changed']}")
    check("fields touched", r["cleansing"]["fields_touched"] == 2)
    check("by_rule ranked", r["cleansing"]["by_rule"][0]["rule"] == "whitespace_punct")


def test_required_failure_blocks_and_leads_the_headline():
    req = {"required_total": 3, "failed_count": 2, "partial_count": 0,
           "failures": [{"sheet": "Supplier Import", "field": "Supplier Name"},
                        {"sheet": "Supplier Bank Import", "field": "Account Number"}],
           "blocked": True}
    r = _report(required_result=req)
    check("blocked", r["blocked"] is True)
    check("counted", r["required_fields"]["failed"] == 2)
    check("passed is the remainder", r["required_fields"]["passed"] == 1)
    check("headline leads with it", r["headline"].startswith("Not ready"),
          f"got {r['headline']}")
    check("headline says why", "rejects every row" in r["headline"])


def test_clean_report_headline_reports_coverage():
    req = {"required_total": 2, "failed_count": 0, "partial_count": 1,
           "failures": [], "partials": [{"sheet": "S", "field": "F"}],
           "blocked": False}
    r = _report(required_result=req)
    check("not blocked", r["blocked"] is False)
    check("headline counts mapped fields", "1 of 2 fields mapped" in r["headline"],
          f"got {r['headline']}")
    check("partial mentioned", "partially filled" in r["headline"],
          f"got {r['headline']}")


def test_report_survives_missing_inputs():
    """A conversion that has never been generated has no DQ report — the report
    must still render rather than explode."""
    r = _report()
    check("no dq is fine", r["validation"]["checked"] == 0)
    check("no cleansing is fine", r["cleansing"]["rules_fired"] == 0)
    check("not blocked without a required check", r["blocked"] is False)
    check("headline still produced", bool(r["headline"]))


def test_unattested_count_is_disclosed():
    fields = [fld(1, "A"), fld(2, "B")]
    maps = [mp(1, conf=0.2), mp(2, conf=0.2)]
    r = _report(fields=fields, mappings=maps)
    check("both AI-resolved", r["mapping"]["unattested"] == 2)
    check("surfaced in the headline", "matcher or AI alone" in r["headline"],
          f"got {r['headline']}")


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
