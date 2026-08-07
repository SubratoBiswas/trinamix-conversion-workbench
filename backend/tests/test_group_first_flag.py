"""Identifying Address — Y on the first row per entityid, blank on the rest.

The workbook rule (HZ_IMP_PARTYSITES_T): "group by entityid, mark as Y for the first
row (the identifying/primary address), blank the others." A live test on Customer
03082026 showed the field shipping blank because no such rule existed. GROUP_FIRST_FLAG
implements it: which row is "first" is decided once over the whole extract
(first appearance) and handed to the row-local transform via ctx.group_first_index,
exactly like the sequence index — so the same row wins on every regenerate.
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.output_service import (_transform_frame,               # noqa: E402
                                         _build_group_first_index)


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


class _F:
    def __init__(self, id, field_name, sheet_id):
        self.id, self.field_name, self.sheet_id = id, field_name, sheet_id


class _M:
    def __init__(self, target_field_id, status="not_applicable", source_column=None,
                 default_value=None, suggested_transformation=None, approved_at=None,
                 confidence=0.0):
        self.target_field_id = target_field_id
        self.status, self.source_column = status, source_column
        self.default_value = default_value
        self.suggested_transformation = suggested_transformation
        self.approved_at, self.confidence = approved_at, confidence


_RULE = {"rule_type": "GROUP_FIRST_FLAG",
         "config": {"key_column": ["entityid", "Entity ID"], "flag": "Y", "default": ""}}


def test_index_records_first_appearance_per_key():
    src = pd.DataFrame({"entityid": ["10", "10", "20", "20", "20"]})
    idx = _build_group_first_index(src, [_RULE["config"]])
    check("keyed by normalised column", "entityid" in idx, list(idx))
    check("10 first appears at row 0", idx["entityid"].get("10") == 0)
    check("20 first appears at row 2", idx["entityid"].get("20") == 2)


def test_first_row_of_each_group_is_flagged_Y():
    src = pd.DataFrame({"entityid": ["10", "10", "20", "20", "20"],
                        "city": ["A", "B", "C", "D", "E"]})
    gf = _build_group_first_index(src, [_RULE["config"]])
    fields = {1: _F(1, "Identifying Address", sheet_id=3)}
    mappings = [_M(1)]
    pipelines = {1: [_RULE]}
    frame, _lin = _transform_frame(
        src, mappings, fields, pipelines, context_cols={"entityid"},
        target_object=None, group_first_index=gf)
    col = list(frame["Identifying Address"])
    check("one Y per customer, on the first row, blank otherwise",
          col == ["Y", "", "Y", "", ""], col)


def test_blank_key_and_unknown_key_yield_the_default():
    src = pd.DataFrame({"entityid": ["", "10"]})
    gf = _build_group_first_index(src, [_RULE["config"]])
    fields = {1: _F(1, "Identifying Address", sheet_id=3)}
    frame, _ = _transform_frame(src, [_M(1)], fields, {1: [_RULE]},
                                context_cols={"entityid"}, target_object=None,
                                group_first_index=gf)
    col = list(frame["Identifying Address"])
    check("blank entityid -> blank (no false Y)", col[0] == "", col)
    check("the sole real customer's first row -> Y", col[1] == "Y", col)


def test_seed_doc_is_well_formed():
    doc = json.loads((Path(__file__).resolve().parent.parent / "app" / "data" /
                      "customer_mapping_07aug.json").read_text(encoding="utf-8"))
    by = {r["target_field"]: r for r in doc["rules"]}
    check("effective date is 07-Aug with a time",
          doc["_effective_date"] == "2026-08-07T23:59:00", doc["_effective_date"])
    ad = by["Account Description"]
    check("Account Description derives from companyname",
          ad["action"] == "derive" and ad["source_column"] == "companyname")
    check("Account Description scoped to the Accounts sheet",
          ad["sheets"] == ["HZ_IMP_ACCOUNTS_T"], ad.get("sheets"))
    ia = by["Identifying Address"]
    check("Identifying Address is a GROUP_FIRST_FLAG rule",
          ia["action"] == "rule" and ia["rule_type"] == "GROUP_FIRST_FLAG")
    check("Identifying Address keyed on entityid, flag Y",
          "entityid" in ia["rule_config"]["key_column"] and ia["rule_config"]["flag"] == "Y")
    check("Identifying Address scoped to PartySites",
          ia["sheets"] == ["HZ_IMP_PARTYSITES_T"], ia.get("sheets"))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nIdentifying Address flags exactly one row per entityid")
