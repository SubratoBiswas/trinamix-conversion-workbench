"""The 06-Aug Supplier transforms: #1 Parent Supplier Name, #3 Supplier Site.

#2 (taxpayer id by country) is deliberately absent — no supplier source carries the
per-country tax columns it named, so it would only ship blanks. This test asserts #1
and #3 are recorded client + object scoped, and that the BU-lookup behaviour of the
Supplier Site key works (map the country code through country_map before joining,
falling through unchanged for a code the map doesn't cover).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.transformations.engine import apply_pipeline          # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_supplier_site_maps_country_code_through_a_bu_lookup():
    cfg = {"country_column": ["Billing Country Code", "country", "Country"],
           "city_column": ["city", "City"], "separator": "-",
           "country_map": {"US": "US-PROC", "CA": "CA-PROC"}}
    rule = {"rule_type": "CITY_COUNTRY_KEY", "config": cfg}
    check("US -> US-PROC-<city>",
          apply_pipeline([rule], "", row={"country": "US", "city": "Chicago"}) == "US-PROC-Chicago")
    check("lowercase code still maps",
          apply_pipeline([rule], "", row={"Billing Country Code": "ca", "city": "Toronto"}) == "CA-PROC-Toronto")
    check("a code not in the map ships unchanged (not blank)",
          apply_pipeline([rule], "", row={"country": "IN", "city": "Hyderabad"}) == "IN-Hyderabad")


def test_empty_map_is_the_old_country_city_key():
    rule = {"rule_type": "CITY_COUNTRY_KEY",
            "config": {"country_column": ["country"], "city_column": ["city"],
                       "separator": "-", "country_map": {}}}
    check("empty country_map -> raw code + city",
          apply_pipeline([rule], "", row={"country": "US", "city": "Chicago"}) == "US-Chicago")


def test_parent_supplier_name_is_a_self_lookup():
    """#1 resolves the parent's Legal Name from Parent Vendor Id via the self-index."""
    rule = {"rule_type": "SELF_LOOKUP",
            "config": {"key_column": "Parent Vendor Id", "match_column": "Internal ID",
                       "value_column": "Legal Name", "default": ""}}
    ctx = {"self_index": {"Internal ID->Legal Name": {"7788": "Acme Holdings LLC"}}}
    check("parent id resolves to the parent's legal name",
          apply_pipeline([rule], "", row={"Parent Vendor Id": "7788"}, ctx=ctx) == "Acme Holdings LLC")
    check("no parent id -> default (blank)",
          apply_pipeline([rule], "", row={"Parent Vendor Id": ""}, ctx=ctx) == "")


def test_the_seed_file_carries_1_and_3_and_not_2():
    import json
    from pathlib import Path
    doc = json.loads((Path(__file__).resolve().parent.parent / "app" / "data"
                      / "supplier_transforms_06aug.json").read_text(encoding="utf-8"))
    fields = {r["target_field"]: r for r in doc["rules"]}
    check("Parent Supplier is a SELF_LOOKUP on Supplier Import",
          fields["Parent Supplier"]["rule_type"] == "SELF_LOOKUP"
          and fields["Parent Supplier"]["target_object"] == "Supplier Import")
    check("Supplier Site is a CITY_COUNTRY_KEY on Supplier Site",
          fields["Supplier Site"]["rule_type"] == "CITY_COUNTRY_KEY"
          and fields["Supplier Site"]["target_object"] == "Supplier Site")
    check("Supplier Site carries a country_map slot for the BU lookup",
          "country_map" in fields["Supplier Site"]["rule_config"])
    check("#2 (taxpayer) is NOT in the file",
          not any("taxpayer" in (r.get("target_field") or "").lower() for r in doc["rules"]))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nsupplier transforms #1 and #3 are correct; #2 is absent by design")
