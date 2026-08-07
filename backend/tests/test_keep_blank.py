""""Keep blank" has to survive three layers, not one.

Analyst, 30-Jul: "give a button to keep as blank for each mapping of the column".
The button is the easy part. What makes it real is that "blank" is a decision with
three separate places to leak:

  1. the MAPPING — cleared source and cleared default, status not_applicable;
  2. the per-target DEDUP — the generator keeps ONE mapping per target field and
     picks it by status priority, where not_applicable (2) ranks BELOW approved (3)
     and overridden (4). A stale duplicate row would therefore win and the column
     would keep shipping its value while the UI showed it blank;
  3. the CONTROL-DEFAULT pass — which refills any column it recognises unless the
     field's name is in ``suppressed``.

Batch ID was the live proof that (3) leaks: marked blank in the UI, still shipping
900001 in the file. (2) is the same bug one layer down, which is why the endpoint
now blanks sibling rows as well as the one that was clicked.

These tests mirror the three decisions rather than driving the DB, so they run in
the plain suite. The seam test at the bottom is what stops the mirror drifting from
the code it mirrors.

Pure: stdlib only.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# From output_service — the priority the generator dedups on.
_SPRIO = {"overridden": 4, "approved": 3, "not_applicable": 2, "rejected": 1,
          "suggested": 0}


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    raise AssertionError(f"{name} {detail}".strip())


class M:
    """The three fields of a mapping the blank decision turns on."""

    def __init__(self, status="suggested", source_column=None, default_value=None,
                 field="Batch ID"):
        self.status, self.source_column = status, source_column
        self.default_value, self.field = default_value, field


def keep_blank(rows):
    """Mirror of routers.mapping.keep_blank — the clicked row AND its siblings."""
    return [M("not_applicable", None, None, r.field) for r in rows]


def best(rows):
    """Mirror of output_service's per-target dedup."""
    win = None
    for r in rows:
        if win is None or _SPRIO.get(r.status, 0) > _SPRIO.get(win.status, 0):
            win = r
    return win


def suppressed_keys(rows):
    """Mirror of output_service.suppressed_keys — built from the dedup WINNER."""
    w = best(rows)
    if w is None or w.status != "not_applicable":
        return set()
    # An explicit default is intent to POPULATE, so it is not a suppression.
    if w.default_value and str(w.default_value).strip():
        return set()
    return {w.field.strip().lower().rstrip("*").strip()}


def control_default(field, value, suppressed, default="900001"):
    """Mirror of _apply_control_defaults for one recognised control column."""
    if field.strip().lower().rstrip("*").strip() in suppressed:
        return value
    return default if not str(value).strip() else value


def test_keep_blank_clears_the_source_and_the_default():
    row = keep_blank([M("approved", "vendor_batch", "900001")])[0]
    check("status becomes not_applicable", row.status == "not_applicable")
    check("source column cleared", row.source_column is None)
    # Leaving the default behind is the trap: the generator reads a
    # not_applicable-WITH-default as "intent to populate" and emits it as a
    # constant, so the column would ship 900001 from the very row that was
    # supposed to blank it.
    check("default cleared", row.default_value is None)


def test_a_stale_approved_sibling_cannot_outrank_the_blank_decision():
    """The dedup bug: one target field, two rows."""
    clicked = M("approved", "vendor_batch", "900001")
    sibling = M("approved", "legacy_batch_no", None)

    # What the OLD endpoint did — blank only the row that was clicked.
    old = [M("not_applicable", None, None), sibling]
    check("old behaviour loses the dedup", best(old).status == "approved",
          "-> the approved sibling still feeds the column")
    check("old behaviour suppresses nothing", suppressed_keys(old) == set())

    # What it does now.
    new = keep_blank([clicked, sibling])
    check("every row for the field is blanked",
          all(r.status == "not_applicable" for r in new))
    check("the blank decision now wins the dedup",
          best(new).status == "not_applicable")
    check("and the field is suppressed", suppressed_keys(new) == {"batch id"})


def test_the_control_default_no_longer_refills_batch_id():
    """The reported symptom, end to end through the mirror."""
    rows = keep_blank([M("approved", "vendor_batch", "900001")])
    sup = suppressed_keys(rows)
    check("Batch ID ships empty", control_default("Batch ID", "", sup) == "")
    # The suppression is per field, not global — an unrelated control column must
    # still get its default, or "keep blank" would quietly break the others.
    check("an unsuppressed control column still defaults",
          control_default("Batch ID", "", set()) == "900001")


def test_a_field_with_an_explicit_default_is_not_treated_as_blank():
    """not_applicable + a default means 'populate with this constant'.

    e.g. Invoice Match Option = "Receipt". Suppressing it would drop a value the
    analyst deliberately set, so the blank set must exclude it.
    """
    rows = [M("not_applicable", None, "Receipt", "Invoice Match Option")]
    check("kept out of the blank set", suppressed_keys(rows) == set())


def test_the_endpoint_still_does_what_this_file_mirrors():
    """Seam: a mirror that has drifted from the code proves nothing.

    Reads the router source and checks the endpoint still performs each of the
    three actions the tests above assume.
    """
    here = os.path.dirname(__file__)
    src = open(os.path.join(here, "..", "app", "routers", "mapping.py"),
               encoding="utf-8").read()
    body = src.split("async def keep_blank(")[1].split("\n@router.")[0]
    # The suppression learning now lives in a shared helper so an ordinary grid edit
    # that clears a mapping records the SAME decision — read its body too.
    suppress = src.split("async def _record_suppression_learning(")[1].split(
        "\n@router.")[0].split("\nasync def ")[0]

    check("endpoint exists", "/keep-blank" in src)
    for needed, why in (
        ('"status": "not_applicable"', "sets the suppressing status"),
        ('"source_column": None', "clears the source"),
        ('"default_value": None', "clears the default"),
        ("_mark_outputs_stale", "marks built files stale"),
    ):
        check(f"keep_blank {why}", needed in body, f"missing {needed!r}")
    # The learning is recorded through the shared helper the endpoint calls.
    check("keep_blank records the suppression via the shared helper",
          "_record_suppression_learning(" in body)
    for needed, why in (
        ('kind="suppress_field"', "records the learning"),
        ("revive=True", "may revive a retired suppression"),
    ):
        check(f"the suppression helper {why}", needed in suppress,
              f"missing {needed!r}")

    # The sibling sweep — the fix this file's second test exists for.
    check("keep_blank blanks sibling rows for the same target field",
          re.search(r"target_field_id\s*==\s*m\.target_field_id", body) is not None,
          "no sibling query found")
    check("the sibling sweep reuses the same blank payload",
          body.count("await sib.set(") == 1 and "_blank" in body)

    # The frontend must actually call it, or the button is decoration.
    fe = os.path.join(here, "..", "..", "frontend", "src")
    api = open(os.path.join(fe, "api", "index.ts"), encoding="utf-8").read()
    page = open(os.path.join(fe, "pages", "MappingReviewPage.tsx"),
                encoding="utf-8").read()
    check("the API client exposes keepBlank", "keepBlank:" in api)
    check("the API client hits the right route", "/keep-blank" in api)
    check("Mapping Review calls it", "MappingApi.keepBlank(" in page)
    check("and renders a button for it", "Keep blank" in page and "onKeepBlank" in page)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__)
        fn()
    print("\nall keep-blank checks passed")
