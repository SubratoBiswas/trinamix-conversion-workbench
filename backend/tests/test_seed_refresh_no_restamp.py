"""A re-running seed must NOT re-stamp captured_at (the revert bug).

Debayon, 07-Aug: "A few columns that I mapped today (e.g. D-U-N-S Number) are reverting
to their previous values." Cause: an undated seed carries no effective_date, so
effective_of() falls back to captured_at — and REFRESH (a seed restating itself on every
boot) was re-stamping captured_at to `now`. So after any deploy the seed looked newer
than an analyst edit made earlier the same day and overrode it. A REFRESH must leave the
date exactly where it is; only a genuinely newer statement (UPDATE) moves it.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import mapping_store as MS            # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


_T0 = datetime(2026, 8, 1, 9, 0, 0)   # when the seed first landed


class _Row:
    def __init__(self, captured_from, kind="suppress_field",
                 effective_date=None, captured_at=_T0):
        self.id = "rowid"
        self.kind = kind
        self.captured_from = captured_from
        self.effective_date = effective_date
        self.captured_at = captured_at
        self.is_deleted = False
        self.sheets = []
        self.exclude_sheets = []
        self.target_field = "D-U-N-S Number"
        self.patch = None

    async def set(self, patch):
        self.patch = patch
        for k, v in patch.items():
            setattr(self, k, v)


def _run(existing_row, *, undated, captured_from, effective_date=None):
    async def _find(**kw):
        return [existing_row]

    async def _noop(*a, **k):
        return None

    orig = (MS.find_rows_for_key, MS._delete_other_decisions, MS._archive)
    MS.find_rows_for_key = _find
    MS._delete_other_decisions = _noop
    MS._archive = _noop
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            MS.record_decision(
                decision=MS.SUPPRESS, target_field="D-U-N-S Number", value="",
                client_id=None, source_erp=None,
                effective_date=effective_date, undated=undated,
                captured_from=captured_from, captured_by=None,
                target_object="Supplier Import", sheets=None))
    finally:
        loop.close()
        (MS.find_rows_for_key, MS._delete_other_decisions, MS._archive) = orig
    return existing_row.patch


def test_refresh_does_not_restamp_captured_at():
    row = _Row(captured_from="NXT supplier corrections", captured_at=_T0)
    patch = _run(row, undated=True, captured_from="NXT supplier corrections")
    check("a REFRESH happened (patch written)", patch is not None)
    check("captured_at NOT re-stamped on refresh", "captured_at" not in patch, patch)
    check("effective_date left alone on refresh", "effective_date" not in patch, patch)
    check("the stored captured_at is still the original", row.captured_at == _T0)


def test_undated_seed_effective_date_is_stable_so_a_human_edit_wins():
    # The seed's effective date (via captured_at) stays at T0 after a refresh, so an
    # analyst edit dated AFTER T0 is the later statement and wins.
    row = _Row(captured_from="NXT supplier corrections", captured_at=_T0)
    _run(row, undated=True, captured_from="NXT supplier corrections")
    seed_effective = MS.effective_of(row)
    human_edit = _T0 + timedelta(hours=5)
    check("human edit is later than the (frozen) seed date", human_edit > seed_effective,
          f"seed={seed_effective} human={human_edit}")


def test_a_genuinely_newer_statement_still_moves_the_date():
    # An UPDATE (a real new, later statement) must still move both dates forward.
    row = _Row(captured_from="analyst", captured_at=_T0, effective_date=_T0)
    newer = _T0 + timedelta(days=3)
    patch = _run(row, undated=False, captured_from="analyst", effective_date=newer)
    check("update moves captured_at", patch.get("captured_at") is not None, patch)
    check("update moves effective_date to the new date",
          patch.get("effective_date") == newer, patch)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nSeed refresh no longer re-stamps the date — human edits survive redeploys.")
