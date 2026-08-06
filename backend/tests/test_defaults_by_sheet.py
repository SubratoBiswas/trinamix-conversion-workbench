"""A default resolves PER SHEET, so the grid stops painting a per-interface
default across every sheet.

Reported 05-Aug: EffectiveStartDate set to 1900/1/1 on ONE HDL object showed on
all four. The store was per-sheet correct; the SCREEN read a field-flat effective
default. `compute_effective_defaults` now returns `defaults_by_sheet`, built by
`_place_default_by_sheet`: a field-wide default (no scope) reaches every sheet, a
scoped one only its own. Tested on the pure helper — no database, so it cannot go
flaky on the shared in-memory harness.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.defaults_service import (  # noqa: E402
    _place_default_by_sheet,
    _strip_suppressed_by_sheet,
)


class _LM:
    """What the helper reads: a scope and a value."""
    def __init__(self, value, sheets=None, exclude_sheets=None):
        self.resolved_value = value
        self.sheets = sheets or []
        self.exclude_sheets = exclude_sheets or []


_SHEETS = ["Location", "Job", "Position"]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_a_scoped_default_lands_only_on_its_sheet():
    by_sheet = {s: {} for s in _SHEETS}
    _place_default_by_sheet(by_sheet, _SHEETS,
                            _LM("1900/1/1", sheets=["Location"]), "effectivestartdate")
    check("Location has it", by_sheet["Location"].get("effectivestartdate") == "1900/1/1")
    check("Job does not", "effectivestartdate" not in by_sheet["Job"], f"got {by_sheet['Job']}")
    check("Position does not", "effectivestartdate" not in by_sheet["Position"])


def test_a_field_wide_default_reaches_every_sheet():
    by_sheet = {s: {} for s in _SHEETS}
    _place_default_by_sheet(by_sheet, _SHEETS, _LM("Y"), "activeflag")
    for s in _SHEETS:
        check(f"{s} has the field-wide default", by_sheet[s].get("activeflag") == "Y",
              f"got {by_sheet[s]}")


def test_exclude_sheets_holds_one_sheet_out():
    by_sheet = {s: {} for s in _SHEETS}
    _place_default_by_sheet(by_sheet, _SHEETS,
                            _LM("X", exclude_sheets=["Job"]), "somefield")
    check("Location has it", by_sheet["Location"].get("somefield") == "X")
    check("Position has it", by_sheet["Position"].get("somefield") == "X")
    check("Job is excluded", "somefield" not in by_sheet["Job"], f"got {by_sheet['Job']}")


def test_two_sheets_can_hold_different_values_for_one_field():
    """The whole point: I on one interface, blank/other on another."""
    by_sheet = {s: {} for s in _SHEETS}
    # Oldest-first: field-wide "I" first, then a Location-specific "X" overrides
    # Location only.
    _place_default_by_sheet(by_sheet, _SHEETS, _LM("I"), "flag")
    _place_default_by_sheet(by_sheet, _SHEETS, _LM("X", sheets=["Location"]), "flag")
    check("Location took the later scoped value", by_sheet["Location"]["flag"] == "X",
          f"got {by_sheet['Location']}")
    check("Job kept the field-wide value", by_sheet["Job"]["flag"] == "I")
    check("Position kept the field-wide value", by_sheet["Position"]["flag"] == "I")


def test_a_suppressed_field_is_stripped_from_every_sheet():
    """Live 05-Aug (Employee HDL): SourceSystemOwner=LEGACY, ActionCode=CREATE,
    EffectiveSequence=1, EffectiveLatestChange=Y are suppressed — blank in the .dat —
    yet they were seeded onto every sheet of `defaults_by_sheet` from the learned
    rows, ahead of the flat-defaults suppression gate. The per-sheet map then
    disagreed with both `defaults` and the file. This strips them back out."""
    by_sheet = {
        "Worker": {"sourcesystemowner": "LEGACY", "actioncode": "CREATE",
                   "effectivestartdate": "1900/1/1"},
        "Assignment": {"effectivesequence": "1", "effectivelatestchange": "Y",
                       "setcode": "DEFAULT"},
    }
    suppressed = {"sourcesystemowner", "actioncode", "effectivesequence",
                  "effectivelatestchange"}
    _strip_suppressed_by_sheet(by_sheet, suppressed)
    check("Worker.sourcesystemowner gone", "sourcesystemowner" not in by_sheet["Worker"])
    check("Worker.actioncode gone", "actioncode" not in by_sheet["Worker"])
    check("Assignment.effectivesequence gone", "effectivesequence" not in by_sheet["Assignment"])
    check("Assignment.effectivelatestchange gone", "effectivelatestchange" not in by_sheet["Assignment"])
    # non-suppressed defaults survive
    check("Worker keeps a real default", by_sheet["Worker"].get("effectivestartdate") == "1900/1/1")
    check("Assignment keeps a real default", by_sheet["Assignment"].get("setcode") == "DEFAULT")


def test_strip_suppressed_is_a_noop_when_nothing_suppressed():
    by_sheet = {"Job": {"setcode": "DEFAULT"}}
    _strip_suppressed_by_sheet(by_sheet, set())
    check("untouched", by_sheet["Job"].get("setcode") == "DEFAULT")


def test_compute_effective_defaults_strips_suppressed_from_by_sheet():
    """The endpoint must apply the strip before returning, or the payload ships the
    leak. Guards against a refactor dropping the post-pass call."""
    import inspect
    from app.services import defaults_service
    src = inspect.getsource(defaults_service.compute_effective_defaults)
    check("compute_effective_defaults calls _strip_suppressed_by_sheet",
          "_strip_suppressed_by_sheet(defaults_by_sheet, suppressed)" in src)


def test_the_return_shape_declares_defaults_by_sheet():
    """The endpoint hands the grid this key; a rename would silently break the fix."""
    import inspect
    from app.services import defaults_service
    src = inspect.getsource(defaults_service.compute_effective_defaults)
    check("compute_effective_defaults returns defaults_by_sheet",
          '"defaults_by_sheet": defaults_by_sheet' in src)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\ndefaults_by_sheet holds")
