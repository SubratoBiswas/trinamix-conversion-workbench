"""The 06-Aug Customer yellow-column changes from 01_Customer_Import.xlsx.

Five rules the workbook flagged yellow that were new or changed vs the 03-Aug document:
Party Original System Reference -> internalid (Parties only, per the analyst's
"only where it maps directly"), Site Language and Primary Indicator kept blank,
From Date coalescing startdate->datecreated, and Relationship Source System Reference
as entityid_internalid_RS. Dated 06-Aug so they beat the 03-Aug statements.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.transformations.engine import apply_pipeline          # noqa: E402

_DOC = Path(__file__).resolve().parent.parent / "app" / "data" / "customer_mapping_06aug.json"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _rules():
    return {r["target_field"]: r for r in json.loads(_DOC.read_text(encoding="utf-8"))["rules"]}


def test_effective_date_supersedes_03aug():
    doc = json.loads(_DOC.read_text(encoding="utf-8"))
    check("dated 06-Aug (beats 03-Aug)", doc["_effective_date"] == "2026-08-06")


def test_party_original_system_reference_is_internalid_on_parties_only():
    r = _rules()["Party Original System Reference"]
    check("maps to internalid", r["source_column"] == "internalid")
    check("scoped to the Parties sheet only", r["sheets"] == ["HZ_IMP_PARTIES_T"],
          f"got {r['sheets']}")


def test_site_language_and_primary_indicator_are_kept_blank():
    r = _rules()
    check("Site Language blanked on PartySites",
          r["Site Language"]["action"] == "blank"
          and r["Site Language"]["sheets"] == ["HZ_IMP_PARTYSITES_T"])
    check("Primary Indicator blanked on PartySiteUses",
          r["Primary Indicator"]["action"] == "blank"
          and r["Primary Indicator"]["sheets"] == ["HZ_IMP_PARTYSITEUSES_T"])


def test_from_date_coalesces_startdate_then_datecreated():
    cfg = _rules()["From Date"]["rule_config"]
    rule = {"rule_type": "COALESCE", "config": cfg}
    check("uses startdate when present",
          apply_pipeline([rule], "", row={"startdate": "2021-01-01", "datecreated": "2019-05-05"}) == "2021-01-01")
    check("falls back to datecreated when startdate blank",
          apply_pipeline([rule], "", row={"startdate": "", "datecreated": "2019-05-05"}) == "2019-05-05")


def test_relationship_source_system_reference_is_entityid_internalid_rs():
    cfg = _rules()["Relationship Source System Reference"]["rule_config"]
    rule = {"rule_type": "CONCAT", "config": cfg}
    check("entityid + _ + internalid + _RS",
          apply_pipeline([rule], "", row={"entityid": "2437", "internalid": "595895"}) == "2437_595895_RS")
    check("a half key blanks rather than shipping a dangling reference",
          apply_pipeline([rule], "", row={"entityid": "2437", "internalid": ""}) == "")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\n06-Aug customer yellow-column changes are correct")
