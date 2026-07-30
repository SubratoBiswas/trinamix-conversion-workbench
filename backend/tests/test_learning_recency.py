"""Conflicts resolve by DATE, latest first — across the whole library.

Analyst, 30-Jul: "why is the tool still following gold learnings, as the new rules
will prioritize mappings files and analyst mapping with date of the rule; for
conflicts always the latest one should be taken for mapping".

Until now the library had no notion of when an instruction was given. Competing
learnings for one field were ordered by TRANSFORM STRENGTH only, so a gold example
captured weeks earlier beat the mapping workbook the analyst had just handed over —
and no date the analyst wrote anywhere could change that.

The date has to be the date of the INSTRUCTION, not of the row. Every startup seed
stamps captured_at with utcnow, so ordering on captured_at would make the 13-Jul
strategy look newer than the 30-Jul corrections after any redeploy, and the whole
precedence would invert on a restart.
"""
import os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.learning_service import _candidate_order, _effective_of

STRONG = {"CASE_WHEN", "CONCAT"}


class LM:
    def __init__(self, tag, rule_type=None, effective_date=None, captured_at=None):
        self.tag, self.rule_type = tag, rule_type
        self.effective_date = effective_date
        self.captured_at = captured_at or datetime(2020, 1, 1)


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def order(items):
    return [x.tag for x in sorted(items, key=lambda m: _candidate_order(m, STRONG))]


def test_the_newer_instruction_wins_even_without_a_transform():
    """The reported case: a plain alias from the 30-Jul workbook beats a gold
    CASE_WHEN from 13-Jul. Strength used to decide this outright."""
    gold = LM("gold-13jul", "CASE_WHEN", datetime(2026, 7, 13))
    book = LM("workbook-30jul", None, datetime(2026, 7, 30))
    check("latest first", order([gold, book])[0] == "workbook-30jul",
          f"got {order([gold, book])}")


def test_strength_still_breaks_a_same_day_tie():
    """Among instructions given on the same day, a real transform still beats a
    plain alias — that rule was protecting Taxpayer ID and must survive."""
    alias = LM("alias", None, datetime(2026, 7, 30))
    xform = LM("transform", "CASE_WHEN", datetime(2026, 7, 30))
    check("transform first", order([alias, xform])[0] == "transform",
          f"got {order([alias, xform])}")


def test_a_ui_capture_falls_back_to_when_the_analyst_acted():
    """Rows captured from an analyst's own action carry no effective_date, and for
    them captured_at IS the moment the instruction was given."""
    seeded = LM("file-30jul", None, datetime(2026, 7, 30))
    ui = LM("ui-31jul", None, None, captured_at=datetime(2026, 7, 31))
    check("the UI action is newer", order([seeded, ui])[0] == "ui-31jul",
          f"got {order([seeded, ui])}")
    check("_effective_of prefers the explicit date",
          _effective_of(seeded) == datetime(2026, 7, 30))
    check("and falls back to captured_at",
          _effective_of(ui) == datetime(2026, 7, 31))


def test_a_reseed_cannot_invert_the_order():
    """The trap this design avoids: both rows re-written today, so captured_at is
    identical and only the file's declared date can still tell them apart."""
    today = datetime(2026, 7, 30, 12, 0)
    strategy = LM("strategy-13jul", "CASE_WHEN", datetime(2026, 7, 13), captured_at=today)
    correction = LM("correction-30jul", None, datetime(2026, 7, 30), captured_at=today)
    check("the correction still leads",
          order([strategy, correction])[0] == "correction-30jul",
          f"got {order([strategy, correction])}")


def test_the_rule_files_declare_their_dates():
    import json
    from pathlib import Path
    data = Path(__file__).resolve().parent.parent / "app" / "data"
    for name, want in (("supplier_corrections_30jul.json", "2026-07-30"),
                       ("supplier_strategy_defaults.json", "2026-07-13"),
                       ("supplier_source_mapping_30jul.json", "2026-07-30")):
        doc = json.loads((data / name).read_text(encoding="utf-8"))
        check(f"{name} is dated {want}", doc.get("_effective_date") == want,
              f"got {doc.get('_effective_date')!r}")


def test_the_model_and_seeders_carry_the_date():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    model = (root / "app" / "models" / "learned.py").read_text(encoding="utf-8")
    check("the model has the field", "effective_date" in model)
    seed = (root / "app" / "services" / "catalog_seed_service.py").read_text(encoding="utf-8")
    check("the seeder reads the file date", "_effective_date_of(doc)" in seed)
    svc = (root / "app" / "services" / "learning_service.py").read_text(encoding="utf-8")
    check("candidates are ordered by it", "_candidate_order(lm, _STRONG_TRANSFORMS)" in svc)
    check("and the defaults pass too", "sorted(defaults, key=_effective_of, reverse=True)" in svc)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall ordering checks passed")
