""""Cannot apply transformation logic where the value of a target field depends on
the value of another target field."   — CW_Issues, 31-Jul, row 36

That sentence, plus two "Tried using custom transformation rule, but its not working"
status notes (rows 16 and 22) and "Party Type still shows blank rows instead of
default as ORGANIZATION" (row 23), are FOUR defects in the transform, all of which
made a correctly authored rule produce nothing:

  1. A rule was read AFTER the discard guard. A derived field has no source column
     by nature, so its mapping row is routinely ``not_applicable`` — and the loop
     `continue`d before ever looking at the analyst's own TransformationRule rows.
     The strategy overlay was rescued from this exact trap on 30-Jul; the rules the
     analyst types in the UI were left in it.

  2. The per-row context is built from SOURCE columns. A condition naming a TARGET
     field — ``variant.if_column = "Party Type"`` — looked it up and got None on
     every row, silently, so the branch never fired and every party got the
     ORGANIZATION form. Same shape as BLANK_IF_EQUALS, which had to be lifted out of
     the row-local engine entirely for the same reason.

  3. ``row_index`` was never passed during generation. Only the mapping PREVIEW
     endpoint set it, so SEQUENCE read ``ctx.get("row_index", 0)`` and returned
     start+0 for EVERY row: Party Number, a required UNIQUE key, would have shipped
     NXT000001 on all 5,489 rows — while the preview showed a perfect running
     sequence. The preview and the file disagreeing is the worst version of this,
     because the screen is the only thing anybody checks.

  4. And when it was passed, it had to be the GLOBAL row number. The transform runs
     on chunks, so a chunk-local index restarts the numbering every 20,000 rows and
     the eighteen sheets that reference Party Number stop agreeing with it.

Pure: pandas + the transform. No database.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd                                                    # noqa: E402
from app.services.output_service import _transform_frame, _RowWithTargets  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


class F:
    def __init__(self, i, name, seq):
        self.id, self.field_name, self.sequence = i, name, seq


class M:
    def __init__(self, tid, src=None, status="approved", dv=None, by="tejaswini@np.com"):
        self.target_field_id, self.source_column, self.status = tid, src, status
        self.default_value, self.approved_by, self.approved_at = dv, by, None
        self.suggested_transformation, self.confidence = None, None


# Party Type is column 4 of HZ_IMP_PARTIES_T and Party Number column 5 — the
# dependency runs forwards in Oracle's own ordering, which is what makes this work.
FIELDS = {1: F(1, "Party Type", 1), 2: F(2, "Party Number", 2)}
PARTY_TYPE_RULE = {"rule_type": "CASE_WHEN", "config": {
    "branches": [{"if_column": "firstname", "op": "notblank", "then": "PERSON"}],
    "default": "ORGANIZATION"}}
PARTY_NUMBER_RULE = {"rule_type": "SEQUENCE", "config": {
    "prefix": "NXT", "width": 6, "start": 1, "preserve_source": True,
    "variant": {"if_column": "Party Type", "op": "eq", "value": "PERSON",
                "width": 5, "suffix": "_C{n}", "counter": 1}}}
SRC = pd.DataFrame({"firstname": ["Ann", "", "Bob", ""],
                    "companyname": ["", "Acme", "", "Globex"]})


def _run(maps, pipes, src=SRC, offset=0):
    out, _ = _transform_frame(src, maps, FIELDS, pipes, {"firstname"},
                              "Customer", row_offset=offset)
    return out


def test_an_analyst_rule_runs_on_a_not_applicable_mapping():
    """Defect 1. A derived field's mapping IS not_applicable — that is what "no
    source column" looks like — so skipping it skipped every derivation."""
    out = _run([M(1, status="not_applicable")], {1: [PARTY_TYPE_RULE]})
    check("the column exists at all", "Party Type" in out.columns)
    check("and it derived", list(out["Party Type"]) ==
          ["PERSON", "ORGANIZATION", "PERSON", "ORGANIZATION"], f"got {list(out['Party Type'])}")


def test_a_field_with_nothing_at_all_is_still_skipped():
    """The guard has to keep working: no source, no rule, no default, no overlay
    means the field genuinely has nothing to write."""
    out = _run([M(1, status="not_applicable")], {})
    check("no column", "Party Type" not in out.columns, f"got {list(out.columns)}")


def test_a_rule_can_read_another_target_field():
    """Defect 2, and row 36 verbatim. Party Number's variant tests Party Type, which
    is itself derived — so the value has to come from the computed TARGET column."""
    out = _run([M(1, status="not_applicable"), M(2, status="not_applicable")],
               {1: [PARTY_TYPE_RULE], 2: [PARTY_NUMBER_RULE]})
    check("people get the _C form", list(out["Party Number"])[0] == "NXT00001_C1",
          f"got {list(out['Party Number'])}")
    check("organizations do not", list(out["Party Number"])[1] == "NXT000002")
    check("and it tracks the derived type",
          [n.endswith("_C1") for n in out["Party Number"]] ==
          [t == "PERSON" for t in out["Party Type"]])


def test_a_source_column_still_wins_over_a_target_of_the_same_name():
    """The target fallback is ADDITIVE. Every rule that resolves today must resolve
    identically — only lookups that used to find nothing may change."""
    src = pd.DataFrame({"firstname": ["Ann", ""], "Party Type": ["FROM_SOURCE", "FROM_SOURCE"]})
    out, _ = _transform_frame(
        src, [M(1, status="not_applicable"), M(2, status="not_applicable")],
        FIELDS, {1: [PARTY_TYPE_RULE], 2: [PARTY_NUMBER_RULE]},
        {"firstname", "Party Type"}, "Customer")
    # The source column says FROM_SOURCE, so the PERSON branch must NOT fire even
    # though the computed target column says PERSON on row 0.
    check("target column still derived", out["Party Type"].iat[0] == "PERSON")
    check("but the rule read the SOURCE", out["Party Number"].iat[0] == "NXT000001",
          f"got {out['Party Number'].iat[0]}")


def test_the_row_view_prefers_source_then_target_then_default():
    v = _RowWithTargets({"a": 1}, {"b": [10, 20]}, 1)
    check("source", v.get("a") == 1)
    check("target at this row", v.get("b") == 20)
    check("default", v.get("zzz", "fallback") == "fallback")
    check("contains", "a" in v and "b" in v and "zzz" not in v)
    check("getitem", v["b"] == 20)


def test_the_sequence_actually_increments():
    """Defect 3. It returned start+0 for every row, so a REQUIRED UNIQUE key was the
    same value on the whole file — and the preview, which did pass row_index, looked
    right the entire time."""
    out = _run([M(1, status="not_applicable"), M(2, status="not_applicable")],
               {1: [PARTY_TYPE_RULE], 2: [PARTY_NUMBER_RULE]})
    nums = list(out["Party Number"])
    check("every value distinct", len(set(nums)) == len(nums), f"got {nums}")
    check("and it counts up", nums == ["NXT00001_C1", "NXT000002", "NXT00003_C1", "NXT000004"],
          f"got {nums}")


def test_the_sequence_is_global_across_chunks():
    """Defect 4. The transform runs on chunks of 20,000 rows; a chunk-local index
    restarts the numbering and the eighteen sheets referencing it stop agreeing."""
    out = _run([M(1, status="not_applicable"), M(2, status="not_applicable")],
               {1: [PARTY_TYPE_RULE], 2: [PARTY_NUMBER_RULE]}, offset=100)
    check("the second chunk continues the numbering",
          list(out["Party Number"]) ==
          ["NXT00101_C1", "NXT000102", "NXT00103_C1", "NXT000104"],
          f"got {list(out['Party Number'])}")


def test_a_real_source_key_still_beats_a_manufactured_one():
    """Section 10.6: manufactured unique values made genuine duplicates look
    distinct. preserve_source is the safeguard and it must survive all of this."""
    src = pd.DataFrame({"firstname": ["Ann"], "partynum": ["REAL-123"]})
    out, _ = _transform_frame(
        src, [M(1, status="not_applicable"), M(2, src="partynum")],
        FIELDS, {1: [PARTY_TYPE_RULE], 2: [PARTY_NUMBER_RULE]},
        {"firstname"}, "Customer")
    check("the source key is kept", out["Party Number"].iat[0] == "REAL-123",
          f"got {out['Party Number'].iat[0]}")


def test_the_chunk_loop_passes_the_offset():
    """Seam: the offset is worthless if the caller does not thread it through, and
    a chunked run only happens above 20,000 rows — well past any unit test."""
    out = (_ROOT / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
    check("chunked calls pass their start row",
          "_self_idx, _city_idx, _city_case, start)" in out)
    check("and the signature takes it", "city_case: dict | None = None, row_offset: int = 0," in out)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall target-field rule checks passed")
