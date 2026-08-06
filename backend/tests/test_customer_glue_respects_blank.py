"""The Customer linkage glue must not regenerate a field the strategy/analyst
marked blank — Batch Identifier is the case.

THE BUG (06-Aug, NextPower Customer, live)
------------------------------------------
Batch Identifier is approved "keep blank", and blank_fields("Customer") lists it,
yet every one of the 15 loaded interface sheets shipped CONV-E3F9D8 — the linkage
batch id the glue invents per conversion (f"CONV-{id[-6:].upper()}").

Cause: the glue skips a column only when it is in `protected`, and `_cust_apply`
built `protected` from the per-sheet `decided` set ALONE. Batch Identifier's mapping
sits "approved" (empty) on the loaded sheets — not "not_applicable" — so it was not
`decided`, so it was not protected, so the glue (a declared FALLBACK) overrode the
blank and regenerated CONV-<id>.

THE FIX
-------
`_cust_apply` now seeds the glue's `protected` set with the suppression set AND the
strategy blank set (`suppressed_keys | _strategy_blanks`) as well as the per-sheet
decisions. A field the client said to leave blank is off limits to the glue.

Customer's strategy blank set is Batch Identifier alone, so this cannot starve a
linkage-reference column the glue is responsible for.
"""
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.customer_structure_service import apply_to_frame   # noqa: E402
from app.services.strategy_overlay import blank_fields               # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# ── The glue honours `protected` for the batch column ────────────────────────

def test_the_glue_fills_batch_when_nothing_protects_it():
    """The baseline: with no protection the glue does regenerate the batch id — this
    is the behaviour that must stay for a conversion that has NOT blanked it."""
    fr = pd.DataFrame({"Batch Identifier": ["", ""], "Party Original System Reference": ["", ""]})
    apply_to_frame(fr, source_system="NETSUITE", batch_id="CONV-ABCDEF",
                   ref=["r1", "r2"], level="party", sheet_name="HZ_IMP_PARTIES_T",
                   protected=set())
    check("batch filled when unprotected", list(fr["Batch Identifier"]) == ["CONV-ABCDEF"] * 2,
          f"got {list(fr['Batch Identifier'])}")


def test_a_protected_batch_column_is_left_blank():
    """The fix's core: name Batch Identifier protected and the glue leaves it alone,
    while it still fills the linkage reference it is responsible for."""
    fr = pd.DataFrame({"Batch Identifier": ["", ""], "Party Original System Reference": ["", ""]})
    apply_to_frame(fr, source_system="NETSUITE", batch_id="CONV-ABCDEF",
                   ref=["r1", "r2"], level="party", sheet_name="HZ_IMP_PARTIES_T",
                   protected={"Batch Identifier"})
    check("batch stays blank when protected", list(fr["Batch Identifier"]) == ["", ""],
          f"got {list(fr['Batch Identifier'])}")
    check("the linkage reference is still generated",
          list(fr["Party Original System Reference"]) == ["r1", "r2"],
          f"got {list(fr['Party Original System Reference'])}")


def test_strategy_blank_set_is_batch_identifier_only():
    """The safety property the fix relies on: feeding the whole Customer blank set
    into the glue's protected set cannot starve a linkage column, because the set is
    Batch Identifier and nothing else."""
    check("Customer blanks are exactly {batch identifier}",
          set(blank_fields("Customer")) == {"batch identifier"},
          f"got {sorted(blank_fields('Customer'))}")


# ── The seam: _cust_apply feeds those sets into the glue ─────────────────────

def test_cust_apply_protects_the_blank_and_suppressed_sets():
    src = (_BACKEND / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
    i = src.index("def _cust_apply(")
    block = src[i:i + 2400]
    check("the glue's protected set includes the suppression + strategy-blank sets",
          "set(suppressed_keys) | set(_strategy_blanks)" in block, block[:600])
    check("and still includes the per-sheet decisions",
          "_sheet_decisions(sfields)" in block and "_header_label(f) for f in sfields" in block)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nthe Customer glue leaves a blanked Batch Identifier alone")
