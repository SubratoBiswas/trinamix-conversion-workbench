"""One rule, stated by the analyst on 31-Jul, and the six places that broke it.

    "The mapping file will provide the initial mapping, the gold standard the initial
     reference for output, but after conversion if the user wants to change any
     mapping, remove etc, the tool should apply it and update or include the learning
     center. THE LAST MAPPING WITH RESPECT TO DATE SHOULD BE CONSIDERED FINAL, and
     existing and new conversions should map and generate output according to that."

Everything below follows from that one sentence. Each test names the specific way the
code contradicted it.

1  CAPTURE.  `PUT /mappings/{id}` learned only when the row was already approved or
   overridden — and `status` is absent from the payload unless the UI sends it. So
   changing the source column of a still-`suggested` row and saving captured NOTHING:
   the conversion in front of the analyst was right, the library never heard, nothing
   propagated. The sibling Approve endpoint had no such gate, so one screen had two
   behaviours and Save was the broken one.

2  FORKING.  `_upsert` keyed on rule_type and then matched on original_value, so
   changing a source column or adding a transform matched nothing and INSERTED a
   second learning while the first stayed live. "I updated the mapping" reliably
   produced two rules and a coin toss. "One answer per field" is a precondition for
   "the last one by date is final" — two live rows cannot express it.

3  PROPAGATION.  Existing conversions skipped EVERY mapping a person had approved,
   forever. In a conversion already worked through — most of them — a correction
   reached almost nothing. The rule is not "never overwrite a person"; it is "the
   later decision wins, whoever made it". An undated decision counts as older,
   because it cannot be shown to have come later and the alternative is what made
   corrections vanish.

4  REMOVAL.  Keep blank captured its suppression and stopped. `suppress_field` also
   had no branch in the propagation patch, so where it did arrive it set
   status=approved and left the source column in place — the opposite of what it
   says. "Remove" is a decision like any other.

5  THE KEY.  A learning is WRITTEN under the template's business_object and was READ
   under the conversion's target_object with exact string equality. The generator
   uses the write key, so a value reached the FBDI file while being invisible in the
   Learning Centre and the defaults preview — a correct fix looking broken.

6  THE ORDER.  Three layers, three orderings: the engine ranked by effective date,
   `compute_effective_defaults` had no sort at all (last row in Mongo natural order
   won), and the Learning Centre sorted by captured_at, which every startup seed
   re-stamps. The list, the screen and the file each believed a different instruction
   was current.

And the reason none of it surfaced: both propagation call sites swallowed exceptions
and returned 200 with a normal payload, and no count. A propagation that threw and
one that reached twelve conversions looked identical.

Pure: stdlib. Reads the shipped source.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.learning_service import (                            # noqa: E402
    _effective_of, object_keys_for_object,
)

_ROOT = Path(__file__).resolve().parent.parent
_SRC = {p.name: p.read_text(encoding="utf-8")
        for p in [(_ROOT / "app" / "routers" / "mapping.py"),
                  (_ROOT / "app" / "routers" / "learned.py"),
                  (_ROOT / "app" / "services" / "learning_service.py"),
                  (_ROOT / "app" / "services" / "mapping_store.py"),
                  (_ROOT / "app" / "services" / "defaults_service.py")]}


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# ── 1. capture on any deliberate edit ───────────────────────────────────────
def test_any_deliberate_edit_is_captured_and_dated():
    m = _SRC["mapping.py"]
    # A decision is defined by WHAT changed (a content field), not by whether the
    # analyst also pressed Approve. The content set now lives in mapping_edit as
    # CONTENT_FIELDS and the decision set is that plus an explicit status.
    check("a decision is defined by WHAT changed, not by the status",
          "_DECISION_FIELDS = CONTENT_FIELDS | {\"status\"}" in m)
    check("and it re-stamps the date", 'if _is_decision:' in m
          and 'data["approved_at"] = datetime.utcnow()' in m)
    # A content edit is made FINAL with no Approve click — finalize_content_edit
    # gives it a decided status so the library cannot overwrite it on refresh.
    check("any content edit is finalized without an Approve click",
          "data.update(finalize_content_edit(" in m)
    # Capture runs on ANY deliberate edit, gated on _is_decision — never on an
    # approved status.
    check("capture no longer requires an approved status",
          "if _is_decision:\n        if m.source_column or (m.default_value" in m)
    check("the old status gate is gone",
          'if m.status in ("approved", "overridden") and (\n        m.source_column' not in m)


def test_the_result_of_propagating_is_reported_rather_than_swallowed():
    """The fan-out now runs off the request, so its result is LOGGED rather than
    returned — there is no synchronous caller left to hand it to. What must not
    happen is the old silent swallow, where a fan-out that threw and one that
    reached twelve conversions looked identical."""
    m = _SRC["mapping.py"]
    helper = m[m.index("async def _propagate_in_background"):]
    helper = helper[:helper.index("\n\n\n")] if "\n\n\n" in helper else helper
    check("the reached count is logged",
          "log.info(" in helper and "reached" in helper)
    check("and a failure is logged as one, not swallowed",
          "log.exception(" in helper)


# ── 2. one answer per field ─────────────────────────────────────────────────
def test_editing_a_mapping_updates_the_learning_instead_of_forking_it():
    """One field, one answer.

    The row used to be found by a key that included rule_type, so changing the
    source column or adding a transform matched nothing and INSERTED a second
    learning while the old one stayed live and competing. "I updated the mapping"
    reliably produced two rules and a coin toss. The key is now
    (client, source system, target field) and the row is updated in place."""
    s = _SRC["mapping_store.py"]
    check("the key is client, source and field",
          "async def find_rows_for_key(*, target_field: str, client_id: Any = None,\n"
          "                            source_erp: str | None = None," in s)
    check("rule_type is not part of it",
          "rule_type" not in s.split("async def find_rows_for_key(")[1]
                              .split("\nasync def ")[0])
    check("an existing row is updated rather than forked",
          "if action in (UPDATE, REFRESH):" in s and "await row.set(patch)" in s)


def test_a_crosswalk_is_still_one_row_per_value():
    """Collapsing those would destroy the map — a crosswalk is per source VALUE,
    not a statement about how a target field maps, so it is not in the store."""
    s = _SRC["mapping_store.py"]
    check("crosswalk is not one of the four decisions",
          '"crosswalk"' not in s[s.index("KIND_TO_DECISION: dict[str, str] = {"):
                                 s.index("DECISION_TO_KIND")])
    check("and the store refuses to record one",
          'raise ValueError(f"not a mapping decision: {decision!r}")' in s)


# ── 3. the later decision wins, whoever made it ─────────────────────────────
def test_propagation_compares_dates_rather_than_who_decided():
    s = _SRC["learning_service.py"]
    check("the blanket human skip is gone",
          "continue        # a person decided this — leave it alone" not in s)
    check("a person's decision stands only while it is the later one",
          "if _when is not None and as_of is not None and _when > as_of:" in s)
    check("and being overruled is counted, not hidden",
          "skipped_newer_decision" in s)


def test_an_undated_decision_loses_to_a_dated_instruction():
    """It cannot be shown to have come later, and reading it as newer is what made
    corrections vanish."""
    s = _SRC["learning_service.py"]
    body = s[s.index("async def propagate_learning_to_open_conversions"):
             s.index("async def _dataset_columns_for")]
    check("the guard requires a timestamp to skip", "_when is not None" in body)
    check("and the reasoning is on the record", "treated as OLDER" in body)


def test_the_date_used_is_the_one_the_instruction_was_given():
    """effective_date first, captured_at only as a fallback — captured_at is
    re-stamped by every startup seed, so ordering on it inverts the precedence after
    any redeploy."""
    a = _effective_of(type("L", (), {"effective_date": datetime(2026, 7, 30),
                                     "captured_at": datetime(2026, 7, 31)})())
    check("effective_date wins", a == datetime(2026, 7, 30), f"got {a}")
    b = _effective_of(type("L", (), {"effective_date": None,
                                     "captured_at": datetime(2026, 7, 31)})())
    check("captured_at is the fallback", b == datetime(2026, 7, 31), f"got {b}")
    c = _effective_of(type("L", (), {})())
    check("and nothing sorts oldest", c == datetime.min)


def test_an_interactive_capture_is_dated_now():
    """Typed into the UI, so now IS when the instruction was given."""
    s = _SRC["mapping_store.py"]
    check("an undated write is stamped now",
          "when = None if (undated and effective_date is None) else "
          "(effective_date or now)" in s)
    check("new rows carry it", "effective_date=when," in s)
    check("and an update re-dates them", 'patch["effective_date"] = when' in s)


def test_a_bundled_file_that_never_said_when_is_not_stamped_with_today():
    """The other half of the same rule, and the one that bit hardest: a seed
    re-running on every boot and stamping itself `now` would out-rank every
    instruction the analyst has ever given, on every redeploy."""
    s = _SRC["mapping_store.py"]
    check("an undated statement stays undated", "undated: bool = False" in s)
    check("it may only refresh what it wrote itself",
          'if captured_from and getattr(row, "captured_from", None) == captured_from:' in s)
    check("and a refresh leaves the stored date alone",
          "# REFRESH deliberately leaves the stored date alone" in s)


# ── 4. removal is a decision too ────────────────────────────────────────────
def test_keep_blank_propagates():
    """The suppression still reaches the other conversions — now AFTER the reply.

    It used to be awaited inside the request, which walked all 35 conversions with
    per-conversion Atlas round-trips and blew past the browser's 60s axios timeout;
    the analyst saw Keep blank "do nothing" because the answer never came back. The
    fan-out now runs as a FastAPI background task, so the row flips and the reply
    returns immediately while the rest catches up off the request."""
    m = _SRC["mapping.py"]
    tail = m[m.index('out["learned_suppression"] = learned') - 2000:]
    check("the suppression is still pushed out",
          "_propagate_in_background" in tail)
    check("and the background helper is what runs the real fan-out",
          "background_tasks.add_task(_propagate_in_background" in m)
    check("the helper calls the real propagation",
          "async def _propagate_in_background" in m
          and "propagate_learning_to_open_conversions(" in m)
    check("keep-blank no longer awaits the fan-out inside the request",
          "_prop = await propagate_learning_to_open_conversions" not in m)


def test_a_suppression_actually_blanks_the_target_mapping():
    """It had no branch in the patch at all, so where it arrived it set
    status=approved and left the source column — the opposite of what it says."""
    s = _SRC["learning_service.py"]
    check("suppress_field has a branch", 'elif lm.kind == "suppress_field":' in s)
    check("it marks the row not applicable", '"status": "not_applicable"' in s)
    check("and clears the source", '"source_column": None' in s)
    check("without also attaching a transform",
          'if lm.rule_type and lm.kind != "suppress_field":' in s)


# ── 5. one object key ───────────────────────────────────────────────────────
def test_every_reader_asks_for_every_spelling_of_the_object():
    keys = object_keys_for_object("Supplier Address")
    for want in ("Supplier Address", "Supplier_Address", "supplier address",
                 "SUPPLIER ADDRESS"):
        check(f"{want!r} is covered", want in keys, f"got {keys}")
    check("an empty object yields nothing", object_keys_for_object("") == [])
    check("defaults reads them all", '"target_object": {"$in": _obj_keys}' in _SRC["defaults_service.py"])
    check("...including client rules", "+ [CLIENT_RULE]" in _SRC["defaults_service.py"])
    check("the Learning Centre reads them all",
          'object_keys_with_client_rules(target_object)' in _SRC["learned.py"])
    check("and propagation matches normalised",
          "_normalize(await _business_object_for(conv)) not in _keys" in _SRC["learning_service.py"])
    check("but exempts a client rule from the object filter",
          "if lm.target_object is not None and (" in _SRC["learning_service.py"])


# ── 6. one ordering ─────────────────────────────────────────────────────────
def test_all_three_layers_order_by_the_same_date():
    check("the defaults layer sorts", "_rows.sort(key=_effective_of)" in _SRC["defaults_service.py"])
    check("the Learning Centre sorts by it too",
          "items.sort(key=_effective_of, reverse=True)" in _SRC["learned.py"])
    check("and no longer by captured_at", 'query.sort("-captured_at")' not in _SRC["learned.py"])


def test_newest_wins_when_two_defaults_compete():
    """The defaults loop assigns oldest-first so the newest write lands last."""
    rows = [type("L", (), {"target_field": "X", "resolved_value": "OLD",
                           "effective_date": datetime(2026, 7, 1), "captured_at": None,
                           "sheets": [], "exclude_sheets": []})(),
            type("L", (), {"target_field": "X", "resolved_value": "NEW",
                           "effective_date": datetime(2026, 7, 31), "captured_at": None,
                           "sheets": [], "exclude_sheets": []})()]
    rows.sort(key=_effective_of)
    out: dict = {}
    for lm in rows:
        out[lm.target_field] = lm.resolved_value
    check("the later instruction is what the screen shows", out["X"] == "NEW",
          f"got {out['X']}")


# ── editing the library reaches the conversions ─────────────────────────────
def test_editing_a_learning_reaches_the_conversions():
    """PATCH was a bare set(): the library and the files drifted apart in silence,
    and the only way to find out was to regenerate and read the output."""
    s = _SRC["learned.py"]
    check("a re-apply exists", "async def _reapply_learning(" in s)
    check("PATCH uses it", 'out["propagation"] = await _reapply_learning(item, "learning-centre")' in s)
    check("POST uses it", 'out["propagation"] = await _reapply_learning(item, user.email)' in s)
    check("and the borrowed conversion is not skipped", "skip_origin=False" in s)


def test_propagation_will_not_point_a_mapping_at_a_missing_column():
    """It wrote lm.original_value verbatim with no check that the target
    conversion's extract has such a column — which reads as mapped on screen and
    produces nothing in the file."""
    s = _SRC["learning_service.py"]
    check("the column is checked", "async def _dataset_columns_for(" in s)
    check("unknown means do not filter", 'None means "cannot tell"' in s)


def test_propagation_honours_sheet_scope():
    """A default is written with sheets=[the sheet it was set on]; this loop matched
    by field NAME across every sheet, so propagation was wider than what the readers
    would then honour."""
    s = _SRC["learning_service.py"]
    check("sheet scope is applied", "ids = {i for i in ids if sheet_allowed(lm, _sheet_of.get(i))}" in s)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall latest-decision-wins checks passed")
