"""CONCAT with a literal trailing segment (REC-03).

An analyst wrote "concatenate sourceCol1 + '_' + sourceCol2 + '_' + 'RS'" and the
trailing literal 'RS' vanished: dropped into `columns` it was read as a COLUMN name,
row.get("RS") is blank, so the tag disappeared — and under require_all the blank part
blanked the whole key. Literal segments are now first-class via `parts` and
`prefix`/`suffix`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.transformations import engine as E            # noqa: E402
from app.services.output_service import _rule_referenced_columns   # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


_ROW = {"entityid": "NT-2437", "internalid": "1001"}


def test_parts_form_keeps_the_trailing_literal():
    cfg = {"parts": [{"col": "entityid"}, {"literal": "_"},
                     {"col": "internalid"}, {"literal": "_RS"}]}
    out = E.apply_rule("CONCAT", cfg, "", row=_ROW)
    check("entityid_internalid_RS", out == "NT-2437_1001_RS", out)


def test_suffix_form_keeps_the_tag():
    cfg = {"columns": ["entityid", "internalid"], "separator": "_", "suffix": "_RS"}
    out = E.apply_rule("CONCAT", cfg, "", row=_ROW)
    check("suffix appended", out == "NT-2437_1001_RS", out)


def test_prefix_and_suffix_together():
    cfg = {"columns": ["entityid"], "prefix": "NX-", "suffix": "_RS"}
    out = E.apply_rule("CONCAT", cfg, "", row=_ROW)
    check("prefix+suffix", out == "NX-NT-2437_RS", out)


def test_require_all_gates_on_columns_not_literals():
    # A missing COLUMN still blanks a required key; the literal must never count as a
    # present-or-missing column.
    cfg = {"parts": [{"col": "entityid"}, {"literal": "_"},
                     {"col": "missing_col"}, {"literal": "_RS"}], "require_all": True}
    out = E.apply_rule("CONCAT", cfg, "", row=_ROW)
    check("half key blanked", out == "", out)
    # ...and with the missing column filled, the literal rides along.
    cfg2 = {"parts": [{"col": "entityid"}, {"literal": "_RS"}], "require_all": True}
    check("full key keeps literal",
          E.apply_rule("CONCAT", cfg2, "", row=_ROW) == "NT-2437_RS")


def test_all_blank_columns_do_not_emit_a_bare_literal():
    cfg = {"parts": [{"col": "missing_a"}, {"literal": "-"}, {"col": "missing_b"}]}
    # No real column data → fall back to the incoming value, never a bare "-".
    check("no bare literal", E.apply_rule("CONCAT", cfg, "KEEP", row={"x": "1"}) == "KEEP")
    cfg2 = {"columns": ["missing_a"], "prefix": "P", "suffix": "S"}
    check("no bare prefix/suffix", E.apply_rule("CONCAT", cfg2, "KEEP", row={"x": "1"}) == "KEEP")


def test_plain_columns_form_is_unchanged():
    cfg = {"columns": ["entityid", "internalid"], "separator": "_"}
    check("plain concat still joins", E.apply_rule("CONCAT", cfg, "", row=_ROW) == "NT-2437_1001")


def test_parts_columns_survive_pruning_declaration():
    cfg = {"parts": [{"col": "entityid"}, {"literal": "_"}, {"col": "internalid"}]}
    cols = _rule_referenced_columns([{"rule_type": "CONCAT", "config": cfg}])
    check("entityid declared", "entityid" in cols, cols)
    check("internalid declared", "internalid" in cols, cols)
    check("literal not declared as a column", "_" not in cols and "RS" not in cols, cols)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nCONCAT literal segments: trailing tags survive.")
