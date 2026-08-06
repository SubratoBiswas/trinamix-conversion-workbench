"""Rules resolve by date AND time — the latest change on a day wins.

UI edits and auto-captures already carried a real timestamp (captured_at) and the
resolver already compares full datetimes, so latest-wins-by-time worked for them.
Seeded DOCUMENTS were the gap: their effective date was truncated to the day, so a
document lost to any same-day change made later. _effective_date_of now honours a time
in the date string, and these tests hold the whole store to date-time precedence.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import mapping_store as store                  # noqa: E402
from app.services.catalog_seed_service import _effective_date_of  # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


class Row:
    def __init__(self, **k):
        for a in ("kind", "target_field", "target_object", "original_value",
                  "resolved_value", "rule_type", "rule_config", "client_id",
                  "source_erp", "effective_date", "captured_at", "sheets",
                  "exclude_sheets", "is_deleted"):
            setattr(self, a, k.get(a))
        self.sheets = self.sheets or []
        self.exclude_sheets = self.exclude_sheets or []
        self.is_deleted = bool(self.is_deleted)


def test_document_date_can_carry_a_time():
    check("plain date -> midnight",
          _effective_date_of({"_effective_date": "2026-08-06"}) == datetime(2026, 8, 6, 0, 0))
    check("ISO T time honoured",
          _effective_date_of({"_effective_date": "2026-08-06T14:30:00"}) == datetime(2026, 8, 6, 14, 30))
    check("space time honoured",
          _effective_date_of({"_effective_date": "2026-08-06 21:05"}) == datetime(2026, 8, 6, 21, 5))
    check("empty -> None", _effective_date_of({"_effective_date": ""}) is None)


def test_effective_of_returns_full_datetime_not_a_date():
    r = Row(kind="example_default", target_field="X", resolved_value="v",
            effective_date=datetime(2026, 8, 6, 20, 52, 0))
    check("effective_of keeps the time",
          store.effective_of(r) == datetime(2026, 8, 6, 20, 52, 0))
    # falls back to captured_at (a UI edit leaves effective_date None)
    r2 = Row(kind="example_default", target_field="X", resolved_value="v",
             effective_date=None, captured_at=datetime(2026, 8, 6, 23, 15, 0))
    check("falls back to captured_at timestamp",
          store.effective_of(r2) == datetime(2026, 8, 6, 23, 15, 0))


def test_latest_same_day_change_wins():
    """Three changes to one field on one day; the resolver must pick the latest by time."""
    field = "Insert Update Indicator"
    morning = Row(kind="example_default", target_field=field, resolved_value="A",
                  rule_config={"default_value": "A"}, effective_date=datetime(2026, 8, 6, 9, 0))
    midday = Row(kind="example_default", target_field=field, resolved_value="B",
                 rule_config={"default_value": "B"}, effective_date=datetime(2026, 8, 6, 14, 0))
    evening = Row(kind="example_default", target_field=field, resolved_value="C",
                  rule_config={"default_value": "C"}, effective_date=datetime(2026, 8, 6, 20, 52))
    w = store.resolve([morning, evening, midday], target_field=field)
    check("the 20:52 change wins over the 09:00 and 14:00 ones",
          w.value == "C", f"got {w.value!r}")


def test_a_human_edit_later_in_the_day_beats_an_earlier_document():
    """A person's edit at 15:00 beats a document 'given' at 09:00 the same day."""
    field = "Organization Name"
    doc = Row(kind="example_default", target_field=field, resolved_value="FROM_DOC",
              rule_config={"default_value": "FROM_DOC"}, effective_date=datetime(2026, 8, 6, 9, 0))
    human = Row(kind="example_default", target_field=field, resolved_value="FROM_HUMAN",
                rule_config={"default_value": "FROM_HUMAN"},
                effective_date=None, captured_at=datetime(2026, 8, 6, 15, 0))
    w = store.resolve([doc, human], target_field=field)
    check("the later human edit wins", w.value == "FROM_HUMAN", f"got {w.value!r}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nrules resolve by date-time; the latest change of the day wins")
