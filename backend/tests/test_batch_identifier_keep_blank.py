"""Batch Identifier must stay blank once the analyst keeps it blank — a generated
per-conversion value must never beat that suppression.

THE BUG (06-Aug, NextPower Customer)
------------------------------------
Batch Identifier was approved "keep blank" in the UI, yet the generated file shipped
`CONV-E3F9D5`. `CONV-<id>` is not an analyst value — it is the Customer linkage batch
id the generator invents per conversion (`f"CONV-{str(conversion.id)[-6:].upper()}"`).
It reached the field, and beat the suppression, through two compounding faults:

1. Auto-capture learned that generated value as a REUSABLE `example_default` and
   re-stamped it `now` on every generate, so it was perpetually newer than the
   seeded keep-blank (dated 2026-08-03) — and a strictly-newer entry wins.
2. Even at an equal date, `mapping_store._order` broke the tie in favour of a
   `default_value` over a `suppress` (via the trailing string tie-breaks).

THE FIX
-------
* `mapping_store._order` — a `suppress` outranks a value at the SAME instant, so a
  keep-blank the analyst tied is honoured; a strictly-newer value still wins.
* `learning_service` capture — a field the linkage glue generates
  (`customer_structure_service.generated_role`: Batch Identifier and the ORIG_SYSTEM
  keys) is never captured as a reusable `example_default`, so a per-conversion
  `CONV-<id>` cannot become a client standard that re-dates itself.

Pure: the resolver is table-driven; the capture guard is asserted at the source.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.mapping_store import (                       # noqa: E402
    DEFAULT_VALUE, SUPPRESS, resolve,
)
from app.services.customer_structure_service import generated_role  # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


class Row:
    def __init__(self, **kw):
        self.id = kw.pop("id", None)
        self.kind = kw.pop("kind", "column_mapping")
        self.target_field = kw.pop("target_field", "Batch Identifier")
        self.target_object = kw.pop("target_object", None)
        self.original_value = kw.pop("original_value", None)
        self.resolved_value = kw.pop("resolved_value", None)
        self.rule_type = kw.pop("rule_type", None)
        self.rule_config = kw.pop("rule_config", None)
        self.client_id = kw.pop("client_id", None)
        self.source_erp = kw.pop("source_erp", None)
        self.effective_date = kw.pop("effective_date", None)
        self.captured_at = kw.pop("captured_at", None)
        self.captured_from = kw.pop("captured_from", None)
        self.captured_by = kw.pop("captured_by", None)
        self.sheets = kw.pop("sheets", [])
        self.exclude_sheets = kw.pop("exclude_sheets", [])
        self.is_deleted = kw.pop("is_deleted", False)
        assert not kw, f"unexpected {kw}"


def d(day, month=8, year=2026):
    return datetime(year, month, day)


def _suppress(*, on, **kw):
    return Row(kind="suppress_field", original_value="(blank)", resolved_value="",
               rule_type="suppress", effective_date=on, **kw)


def _default(value, *, on, **kw):
    return Row(kind="example_default", original_value="(default)",
               resolved_value=value, rule_config={"default_value": value},
               effective_date=on, **kw)


# ── The resolver: a tied suppression wins; a strictly-newer value still wins ──

def test_a_same_dated_keep_blank_beats_a_generated_default():
    """The exact Batch Identifier tie: same instant, suppress vs CONV-<id>."""
    won = resolve([_default("CONV-E3F9D5", on=d(3)), _suppress(on=d(3))],
                  target_field="Batch Identifier")
    check("the keep-blank wins the tie", won.decision == SUPPRESS,
          f"got {won.decision}={won.value!r}")
    # order-independent
    won2 = resolve([_suppress(on=d(3)), _default("CONV-E3F9D5", on=d(3))],
                   target_field="Batch Identifier")
    check("and wins whichever order they arrive in", won2.decision == SUPPRESS)


def test_a_strictly_newer_value_still_beats_a_keep_blank():
    """The fix only decides a TIE. A genuinely later constant must still win, or a
    real analyst default set after a blank would be ignored."""
    won = resolve([_suppress(on=d(3)), _default("KEEP-ME", on=d(4))],
                  target_field="Batch Identifier")
    check("a newer value overrides the blank",
          (won.decision, won.value) == (DEFAULT_VALUE, "KEEP-ME"),
          f"got {won.decision}={won.value!r}")


def test_a_strictly_newer_keep_blank_beats_a_value():
    won = resolve([_default("OLD", on=d(3)), _suppress(on=d(4))],
                  target_field="Batch Identifier")
    check("a newer blank overrides the value", won.decision == SUPPRESS)


# ── The capture guard: generated fields are never learned as reusable defaults ──

def test_the_generated_linkage_fields_are_recognised():
    for name in ("Batch Identifier", "Party Original System Reference",
                 "Customer Account Source System Reference",
                 "Account Site Source System Reference"):
        check(f"{name} is a generated role", generated_role(name) is not None,
              f"generated_role({name!r})=None")
    # A real, analyst-owned field is NOT generated, so its default is still learned.
    for name in ("Credit Limit", "Insert Update Indicator", "Currency"):
        check(f"{name} is not a generated role", generated_role(name) is None)


def test_capture_skips_a_generated_default():
    """Source guard: both capture paths must consult generated_role before writing
    an example_default, or CONV-<id> becomes a reusable client default again."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "services",
                            "learning_service.py"), encoding="utf-8").read()
    check("the guard is imported at the capture sites",
          src.count("from app.services.customer_structure_service import generated_role") >= 2)
    # The per-mapping default writer returns early for a generated field.
    check("record_learning_from_mapping guards the default write",
          "if generated_role(target_field):" in src and "return None" in src)
    # The end-of-generate capture skips a generated field.
    check("capture_learnings_from_conversion guards the default capture",
          "if generated_role(fname):" in src and "continue" in src)


def test_the_order_key_carries_the_suppress_priority():
    """Source guard on the resolver: the suppress-first term must sit right after the
    date, so it breaks ties without ever overriding a newer date."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "services",
                            "mapping_store.py"), encoding="utf-8").read()
    check("suppress-first is computed",
          "suppress_first = 0 if entry.decision == SUPPRESS else 1" in src)
    i = src.index("datetime.max - (entry.effective_date or datetime.min),")
    tail = src[i:i + 160]
    check("and it is the first tie-break after the date",
          tail.split("\n")[1].strip().startswith("suppress_first"), tail[:120])


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nBatch Identifier keep-blank holds against a generated default")
