""""I mapped these yesterday and the output still doesn't carry them."

Reported live on NextPower Supplier Test (Supplier Site). Three constants set in
Mapping Review, approved, each showing "currently 3" / "currently R" on screen:

    Receipt Routing       -> 3        file shipped DIRECT
    Invoice Match Option  -> R        file shipped Receipt
    Match Approval Level  -> 3        file shipped 3-Way

Those three are write-time strategy constants in supplier_strategy_defaults.json,
dated 13-Jul, applied inside _transform_frame after the mapping. The overlay is
supposed to stand aside for an analyst who has spoken more recently — that guard
existed and looked right:

    _explicit = bool(str(m.source_column or "").strip()
                     and m.status in ("approved", "overridden")
                     and _by_a_person and _person_is_newer)

It reads `source_column`. A CONSTANT has no source column — that is what makes it
a constant — so `_explicit` was False for every fixed value ever typed, and the
overlay replaced all 8,561 rows with its own. The only analyst the guard could
see was one who had bound a column.

The screen said one thing and the file said another, and the screen looked right.

Pure: hand-built mappings through the transform. No database.
"""
from pathlib import Path

import pandas as pd

from app.services.output_service import _transform_frame

_BACKEND = Path(__file__).resolve().parent.parent
SRC = pd.DataFrame({"Vendor Site Code": ["S1", "S2"]})
YESTERDAY = pd.Timestamp("2026-08-03").to_pydatetime()
LAST_YEAR = pd.Timestamp("2025-01-01").to_pydatetime()


class F:
    def __init__(self, i, name):
        self.id, self.field_name, self.sequence = i, name, i


class M:
    """A mapping with NO source column — which is what a constant looks like."""
    def __init__(self, tid, dv=None, src=None, status="approved",
                 by="subrato@nextpower.com", at=YESTERDAY):
        self.target_field_id, self.source_column, self.status = tid, src, status
        self.default_value, self.approved_by, self.approved_at = dv, by, at
        self.suggested_transformation, self.confidence = None, None


def run(maps, fields):
    out, _ = _transform_frame(SRC, maps, fields, {}, set(), "Supplier Site")
    return out


# ── The three reported fields, by name, against the shipped overlay ──────────

CASES = [(1, "Receipt Routing", "3", "DIRECT"),
         (2, "Invoice Match Option", "R", "Receipt"),
         (3, "Match Approval Level", "3", "3-Way")]


def test_the_analysts_constant_reaches_the_file():
    for tid, name, mine, strategy in CASES:
        out = run([M(tid, dv=mine)], {tid: F(tid, name)})
        got = list(out[name])
        assert got == [mine, mine], (
            f"{name}: analyst set {mine!r}, file carried {got!r} "
            f"(the 13-Jul strategy says {strategy!r})")


def test_the_strategy_constant_still_fills_a_field_nobody_has_set():
    """The overlay is not disabled — it is the default for a field the analyst
    has not spoken about, which is most of them."""
    out = run([M(1, dv=None, status="suggested", by="", at=None)],
              {1: F(1, "Receipt Routing")})
    assert list(out["Receipt Routing"]) == ["DIRECT", "DIRECT"]


def test_an_engine_approval_does_not_count_as_a_person():
    """The learning engine approves what it applies. If that counted, a seeded
    mapping would outrank the analyst's own later correction — the defect the
    _by_a_person test was added for."""
    out = run([M(1, dv="3", by="learning-engine")], {1: F(1, "Receipt Routing")})
    assert list(out["Receipt Routing"]) == ["DIRECT", "DIRECT"]


def test_a_constant_set_before_the_strategy_file_does_not_win():
    """Whichever is latest, in both directions. The strategy is dated 13-Jul; a
    value approved in 2025 is the older statement."""
    out = run([M(1, dv="3", at=LAST_YEAR)], {1: F(1, "Receipt Routing")})
    assert list(out["Receipt Routing"]) == ["DIRECT", "DIRECT"]


def test_a_constant_that_is_only_suggested_does_not_win():
    """Auto-map guessing is exactly what the strategy constants exist to correct,
    so only a deliberate approve/override counts."""
    out = run([M(1, dv="3", status="suggested")], {1: F(1, "Receipt Routing")})
    assert list(out["Receipt Routing"]) == ["DIRECT", "DIRECT"]


def test_a_bound_source_column_still_wins_as_it_always_did():
    """The behaviour that already worked must be untouched — this change is
    purely additive."""
    src = pd.DataFrame({"Routing": ["X", "Y"]})
    m = M(1, src="Routing")
    out, _ = _transform_frame(src, [m], {1: F(1, "Receipt Routing")}, {},
                              set(), "Supplier Site")
    assert list(out["Receipt Routing"]) == ["X", "Y"]


def test_a_blank_directive_also_yields_to_an_approved_constant():
    """Same guard drives the 'ship this column empty' branch. An analyst who
    types a value into a field the strategy blanks is overruling it deliberately,
    and silently emptying the column would be the same bug wearing a hat."""
    out = run([M(1, dv="900001")], {1: F(1, "Batch ID")})
    assert list(out["Batch ID"]) == ["900001", "900001"]


# ── The seam ─────────────────────────────────────────────────────────────────

def test_the_guard_reads_the_default_value_not_only_the_source_column():
    body = (_BACKEND / "app" / "services" / "output_service.py").read_text(
        encoding="utf-8").split("_person_set_a_value = ")[1][:400]
    assert "m.default_value" in body
    assert "m.source_column" in body


def test_the_three_reported_fields_really_are_strategy_constants():
    """A canary. If these are ever removed from the strategy file this test is
    the only thing that still remembers what the bug was about."""
    import json
    doc = json.loads((_BACKEND / "app" / "data" / "supplier_strategy_defaults.json")
                     .read_text(encoding="utf-8"))
    rules = (doc.get("rules") or []) + ((doc.get("analyst_rules") or {}).get("rules") or [])
    by_field = {r.get("target_field"): r.get("constant") for r in rules}
    assert by_field.get("Receipt Routing") == "DIRECT"
    assert by_field.get("Invoice Match Option") == "Receipt"
    assert by_field.get("Match Approval Level") == "3-Way"
