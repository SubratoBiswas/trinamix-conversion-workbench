"""A CASE_WHEN whose branches map each country to a DIFFERENT source column via
``{Column}`` interpolation must ship the column VALUE, not the literal token.

THE DEFECT (06-Aug, NextPower Supplier Test)
--------------------------------------------
Taxpayer ID was built with a SQL-expression CASE:

    CASE WHEN country = 'India'         THEN pan
         WHEN country = 'United States' THEN tax_id
         WHEN country = 'Canada'        THEN tax_id_canada
    END

which compiles to a CASE_WHEN whose branch results are ``{pan}``, ``{tax_id}``,
``{tax_id_canada}`` — column references the engine interpolates from the row.

``pan`` came out right; ``tax_id`` and ``tax_id_canada`` shipped the LITERAL text
``{tax_id}`` / ``{tax_id_canada}``. The engine was fine — the columns were never in
the row it received. The generator prunes the source frame to the columns rules
declare (``_rule_referenced_columns``), and that walk collected a CASE_WHEN's
``if_column`` but not the columns named only inside a branch's ``then``. ``pan``
survived solely because it was the rule's own ``source_column``; ``tax_id`` /
``tax_id_canada`` were referenced nowhere the walk looked, so they were dropped
before the rule ran and the engine had nothing to interpolate.

THE FIX
-------
``_rule_referenced_columns`` now also collects the columns named in a CASE_WHEN
branch's ``then`` (and the top-level ``default``), and in a CONDITIONAL's ``then`` /
``else`` — the same result strings the engine interpolates. Those columns then
survive pruning and reach the per-row context, so the values resolve.

This exercises the REAL generator path — ``_rule_referenced_columns`` for the
collection and ``_transform_frame`` for the prune → per-row context → engine
sequence — and carries a negative control proving the collection is load-bearing.
"""
import os
import sys
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.output_service import (            # noqa: E402
    _branch_columns, _rule_referenced_columns, _transform_frame,
)


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _taxpayer_rule():
    """Exactly the shape the SQL builder produces for the reported CASE."""
    return {
        "rule_type": "CASE_WHEN",
        "source_column": "pan",          # the rule's own declared source column
        "config": {
            "branches": [
                {"if_column": "country", "op": "eq", "value": "India",
                 "then": "{pan}"},
                {"if_column": "country", "op": "eq", "value": "United States",
                 "then": "{tax_id}"},
                {"if_column": "country", "op": "eq", "value": "Canada",
                 "then": "{tax_id_canada}"},
            ],
            "default": "",
        },
    }


def _frame():
    """Three suppliers — one per country — plus noise columns the rule never names,
    so the prune actually has something to drop."""
    return pd.DataFrame([
        {"country": "India", "pan": "AAAAE2161R", "tax_id": "",
         "tax_id_canada": "", "supplier_name": "Bharat Metals", "city": "Pune"},
        {"country": "United States", "pan": "", "tax_id": "83-1655504",
         "tax_id_canada": "", "supplier_name": "Acme US", "city": "Austin"},
        {"country": "Canada", "pan": "", "tax_id": "",
         "tax_id_canada": "123456789RT0001", "supplier_name": "Maple Co", "city": "Toronto"},
    ])


def _run(needed_src, ctx_cols):
    """Reproduce the generator's own steps: prune the frame to ``needed_src`` (the
    exact-match prune from _convert_source), then transform with ``ctx_cols`` as the
    per-row context — and return the Taxpayer ID column."""
    src = _frame()
    keep = [c for c in src.columns if c in needed_src]
    pruned = src[keep].copy()
    mapping = SimpleNamespace(
        target_field_id="f1", source_column="pan", status="approved",
        default_value=None, suggested_transformation=None,
        approved_at=None, approved_by="analyst@nextpower.com", confidence=1.0)
    field = SimpleNamespace(id="f1", field_name="Taxpayer ID", sequence=1)
    out, _lineage = _transform_frame(
        pruned, [mapping], {"f1": field}, {"f1": [_taxpayer_rule()]},
        context_cols=ctx_cols, target_object="__test_no_overlay__")
    return list(out["Taxpayer ID"])


# ── The collection: the fix itself ───────────────────────────────────────────

def test_the_then_columns_are_collected():
    refs = _rule_referenced_columns([_taxpayer_rule()])
    check("the if_column is collected", "country" in refs)
    check("the rule's own source column is collected", "pan" in refs)
    check("the US branch's {tax_id} is collected", "tax_id" in refs, f"got {sorted(refs)}")
    check("the Canada branch's {tax_id_canada} is collected",
          "tax_id_canada" in refs, f"got {sorted(refs)}")


def test_a_default_interpolation_is_collected_too():
    rule = {"rule_type": "CASE_WHEN", "config": {
        "branches": [{"if_column": "country", "op": "eq", "value": "X", "then": "Y"}],
        "default": "{fallback_col}"}}
    check("the default's token is collected",
          "fallback_col" in _rule_referenced_columns([rule]))


def test_conditional_then_else_columns_are_collected():
    rule = {"rule_type": "CONDITIONAL", "config": {
        "if_column": "flag", "equals": "Y",
        "then": "{col_a}", "else": "{col_b}"}}
    refs = _rule_referenced_columns([rule])
    for c in ("flag", "col_a", "col_b"):
        check(f"{c} is collected", c in refs, f"got {sorted(refs)}")


# ── End to end through the real transform: the reported case ──────────────────

def test_each_country_ships_its_own_taxpayer_column():
    """With the fix, needed_src includes the {then} columns, they survive the prune,
    and every country resolves to its own value."""
    refs = _rule_referenced_columns([_taxpayer_rule()])
    needed_src = {"pan"} | refs
    got = _run(needed_src, refs)
    check("India -> pan", got[0] == "AAAAE2161R", f"got {got[0]!r}")
    check("United States -> tax_id", got[1] == "83-1655504", f"got {got[1]!r}")
    check("Canada -> tax_id_canada", got[2] == "123456789RT0001", f"got {got[2]!r}")
    check("no row shipped a literal token",
          not any(str(v).startswith("{") for v in got), f"got {got}")


def test_negative_control_without_the_then_columns_the_token_ships():
    """Prove the collection is load-bearing: reproduce the OLD walk (source column +
    branch if_columns only, i.e. `_branch_columns`) and the US/Canada rows ship the
    literal token — exactly the bug. If this ever passes, the prune stopped mattering
    and the real test above would no longer be guarding anything."""
    old_refs = {"pan"} | _branch_columns(_taxpayer_rule()["config"]["branches"])
    check("the old walk misses tax_id", "tax_id" not in old_refs)
    got = _run(old_refs, old_refs)
    check("India still resolves (pan survived as source column)",
          got[0] == "AAAAE2161R", f"got {got[0]!r}")
    check("United States ships the literal token (the bug)",
          got[1] == "{tax_id}", f"got {got[1]!r}")
    check("Canada ships the literal token (the bug)",
          got[2] == "{tax_id_canada}", f"got {got[2]!r}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nCASE_WHEN {then} columns survive pruning and resolve per country")
