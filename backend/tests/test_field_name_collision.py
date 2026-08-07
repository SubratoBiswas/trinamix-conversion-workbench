"""A sheet-scoped rule's value must not be blanked by a same-named field on another
sheet — the "mapped in the UI, blank in the output" collision.

Oracle repeats a field name across interface sheets, and out_cols in _transform_frame
is keyed by bare field name. "Relationship Source System Reference" is on several
Customer tabs; only the HZ_IMP_PARTYSITES_T copy carries the CONCAT rule
(entityid_internalid_RS). The other copies are unmapped and produce a blank column,
and — processed after the one that computed the real value — `out_cols[name] = blank`
erased it, so the field shipped empty though the UI showed the rule.

These drive the real _transform_frame (a pure function) with two target fields of the
same name: one with the CONCAT pipeline, one unmapped, the unmapped one LAST so it
would overwrite. target_object=None keeps the strategy overlay out of it.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.output_service import _transform_frame           # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


class _F:
    """An FBDIField stand-in — the attributes _transform_frame reads."""
    def __init__(self, id, field_name, sheet_id):
        self.id, self.field_name, self.sheet_id = id, field_name, sheet_id


class _M:
    """A MappingSuggestion stand-in."""
    def __init__(self, target_field_id, status="suggested", source_column=None,
                 default_value=None, suggested_transformation=None, approved_at=None,
                 confidence=0.0):
        self.target_field_id = target_field_id
        self.status = status
        self.source_column = source_column
        self.default_value = default_value
        self.suggested_transformation = suggested_transformation
        self.approved_at = approved_at
        self.confidence = confidence


_FIELD = "Relationship Source System Reference"

# The 06-Aug rule: CONCAT entityid + _ + internalid, then append _RS; require_all so
# half a key blanks rather than shipping a dangling reference.
_CONCAT = {
    "rule_type": "CONCAT",
    "config": {
        "separator": "_",
        "columns": [["entityid", "Entity ID"], ["internalid", "Internal ID"]],
        "require_all": True,
        "then": [{"rule_type": "SUFFIX",
                  "config": {"suffix": "_RS", "skip_blank": True, "skip_if_present": True}}],
    },
}


def _run(mappings, pipelines):
    src = pd.DataFrame({"entityid": ["2437", "10", ""],
                        "internalid": ["595895", "77", "88"]})
    fields = {
        101: _F(101, _FIELD, sheet_id=7),   # HZ_IMP_PARTYSITES_T — carries the rule
        202: _F(202, _FIELD, sheet_id=9),   # another tab, same name, unmapped
    }
    frame, _lin = _transform_frame(
        src, mappings, fields, pipelines,
        context_cols={"entityid", "internalid"}, target_object=None)
    return frame


def test_unmapped_same_name_field_no_longer_blanks_the_rule():
    """PARTYSITES computes the CONCAT; the unmapped copy is processed LAST. The
    field must still carry the rule's value, not the blank."""
    mappings = [
        _M(101, status="not_applicable"),   # derived field: no source, rule supplies it
        _M(202, status="suggested"),        # unmapped copy on another sheet — LAST
    ]
    pipelines = {101: [_CONCAT], 202: []}
    frame = _run(mappings, pipelines)
    col = list(frame[_FIELD])
    check("row 0 keeps the concat, not a blank", col[0] == "2437_595895_RS", f"got {col[0]!r}")
    check("row 1 keeps the concat", col[1] == "10_77_RS", f"got {col[1]!r}")
    # require_all: entityid blank on row 2 -> the rule itself blanks that row.
    check("row 2 is blank by the rule's own require_all", col[2] == "", f"got {col[2]!r}")


def test_order_independent_the_rule_survives_either_way():
    """Same two fields, unmapped copy FIRST. Order must not decide the outcome."""
    mappings = [
        _M(202, status="suggested"),        # unmapped copy FIRST
        _M(101, status="not_applicable"),   # rule sheet second
    ]
    pipelines = {202: [], 101: [_CONCAT]}
    frame = _run(mappings, pipelines)
    check("the concat wins regardless of processing order",
          list(frame[_FIELD])[0] == "2437_595895_RS", list(frame[_FIELD]))


def test_two_real_values_still_last_wins():
    """The guard only protects against a BLANK overwrite. Two populated same-named
    fields must keep last-populated-wins, so a sheet with its own mapping is
    unaffected."""
    mappings = [
        _M(101, status="overridden", source_column="entityid"),
        _M(202, status="overridden", source_column="internalid"),  # populated, LAST
    ]
    frame = _run(mappings, {101: [], 202: []})
    check("a later real value still overwrites an earlier one",
          list(frame[_FIELD]) == ["595895", "77", "88"], list(frame[_FIELD]))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nsheet-scoped rule values survive the field-name collision")
