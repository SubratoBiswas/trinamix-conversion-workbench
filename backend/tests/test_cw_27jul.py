"""The 27-Jul issue list. Four defects, three of them invisible by construction.

    row 1  "Input file has two sheets - Customer and Address. If we upload a
            workbook with two sheets, the conversion includes only the columns from
            the first sheet as source columns. Current implementation generates two
            conversions when two sheets are uploaded."
    row 5  "when I delete items from the Learning Center while working on supplier
            files, they reappear instead of being removed permanently."
    row 7  "Default values in the learning centre is no getting populated."
    row 8  "Address name is shown mapped correctly to city in the UI, but the
            generated file contains (static value PRIMARY)."

ROW 1 was two layers, and the output layer — the one that looks responsible — was
already correct. The generator has routed per-source frames for a long time. What
collapsed was (a) the create-all loop, which called generate-set once PER SHEET and
so produced two conversion sets for one workbook, while the per-row button on the
same page had always collected the siblings and sent dataset_ids; and (b) the
source-columns endpoint, which read the singular conv.dataset_id. That second one is
worse than an omission: the auto-mapper unions source_dataset_ids, so the AI could
propose a mapping to an Address column the analyst could then neither see nor
re-pick. Two layers disagreeing about what the SOURCES are.

ROW 5 is the tombstone family, fourth appearance. LearnedMapping.find injects
{'is_deleted': {'$ne': True}}, so a retired row is invisible to a plain find_one —
the code sees nothing, inserts, and the deleted learning is back beside its own
tombstone. SIX write paths had no idea tombstones existed. The audit that was
supposed to catch this could not: it flagged functions that MENTION is_deleted while
querying without include_deleted — a DEAD guard — and these six never mentioned it.
The audit is now anchored on the INSERT instead, which is the thing that does the
damage. Beanie's Document.get() delegates to find_one, so get() is tombstone-blind
too and takes no include_deleted at all — including in the delete endpoint itself,
where it meant an already-retired row could never be purged.

ROW 7 was a scope filter, not a capture failure. client_id_for_conversion can return
None, the capture then writes client_id=None with is_global=False, and the Learning
Center's hand-rolled filter matched NEITHER is_global nor client_id — so the row
existed, applied to the file, and could not be seen on screen. scope_query already
rescues exactly these rows for the defaults layer; this endpoint did not use it.

ROW 8: "address name": "PRIMARY" was still in _CONTROL_DEFAULTS. Taking it out of
_AUTHORITATIVE was not enough, because the plain control-default branch fills any
column that reaches finalize entirely blank and has NO explicitly_mapped guard at
all. The signed strategy says the opposite in so many words — "Address Name is the
City Name (e.g. Austin). NOT the constant 'PRIMARY'."

Pure: stdlib + pandas. No database.
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd                                                    # noqa: E402
from app.services.output_service import (                              # noqa: E402
    _CONTROL_DEFAULTS, _apply_control_defaults,
)

_ROOT = Path(__file__).resolve().parent.parent
_FE = _ROOT.parent / "frontend" / "src"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# ── row 8 ───────────────────────────────────────────────────────────────────
def test_address_name_is_no_longer_a_control_default():
    """The strategy says it is the CITY. A control default is for a column nobody
    has an opinion about; this one has a signed opinion."""
    check("address name is out of the table", "address name" not in _CONTROL_DEFAULTS,
          f"still {_CONTROL_DEFAULTS.get('address name')!r}")


def test_a_blank_address_name_ships_blank_rather_than_primary():
    """The failure was silent and total: the column reaches finalize empty (renamed
    source, sheet-routing miss, a mapping row that lost the dedup) and every row gets
    PRIMARY. Blank is the honest output — a visible gap, not a value the strategy
    forbids."""
    df = pd.DataFrame({"Address Name": ["", "", ""]})
    out = _apply_control_defaults(df.copy())
    check("stays blank", list(out["Address Name"]) == ["", "", ""],
          f"got {list(out['Address Name'])}")


def test_a_mapped_address_name_is_untouched():
    df = pd.DataFrame({"Address Name": ["Hayward", "Dallas", ""]})
    out = _apply_control_defaults(df.copy())
    check("real cities survive", list(out["Address Name"]) == ["Hayward", "Dallas", ""],
          f"got {list(out['Address Name'])}")


def test_the_strategy_still_says_what_this_is_based_on():
    """If the signed strategy ever changes its mind, this test should be the thing
    that notices — not a regenerated file six weeks later."""
    import json
    doc = json.loads((_ROOT / "app" / "data" / "supplier_strategy_defaults.json")
                     .read_text(encoding="utf-8"))
    txt = json.dumps(doc)
    check("the strategy names the city", "Address Name is the City Name" in txt)
    check("and rules PRIMARY out explicitly", "NOT the constant 'PRIMARY'" in txt)


# ── row 5 ───────────────────────────────────────────────────────────────────
def _inserting_functions():
    """(file, function, sees_tombstones) for every function that builds a
    LearnedMapping."""
    out = []
    app = _ROOT / "app"
    for p in sorted(app.rglob("*.py")):
        src = p.read_text(encoding="utf-8")
        if "LearnedMapping(" not in src:
            continue
        tree = ast.parse(src)
        for f in [n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                       and n.func.id == "LearnedMapping" for n in ast.walk(f)):
                continue
            out.append((str(p.relative_to(app)).replace(os.sep, "/"), f.name,
                        "include_deleted=True" in ast.unparse(f)))
    return out


def test_the_six_paths_that_resurrected_deleted_learnings_are_guarded():
    """Named individually, because "the audit passes" is what was true while all six
    were broken."""
    seen = {(f, fn): ok for f, fn, ok in _inserting_functions()}
    for f, fn in (("routers/manual_map.py", "save"),
                  ("services/mapping_ingest_service.py", "apply_proposal"),
                  ("services/value_mapping_service.py", "accept_value_map"),
                  ("services/mapping_import_service.py", "import_mapping_file"),
                  ("services/steering_service.py", "_learn"),
                  ("routers/datasets.py", "classify_learn")):
        check(f"{f}:{fn}() still exists", (f, fn) in seen, f"not found — renamed?")
        check(f"{f}:{fn}() can see a tombstone", seen[(f, fn)])


def test_an_automatic_path_respects_a_tombstone_and_a_human_one_revives():
    """The distinction the original rule drew. classify-learn runs by itself on
    upload, so it must never undo a deletion. Typing a mapping, accepting a value
    map, importing a workbook or writing a prompt are the analyst acting, so they
    revive — IN PLACE, so there is one row rather than a ghost pair."""
    ds = (_ROOT / "app" / "routers" / "datasets.py").read_text(encoding="utf-8")
    check("classify-learn refuses to re-learn a retired signature",
          "not re-learned" in ds and "this file signature was retired" in ds)
    for rel, needle in (
        ("routers/manual_map.py", '"is_deleted": False, "deleted_at": None'),
        ("services/value_mapping_service.py", '"is_deleted": False'),
        ("services/mapping_import_service.py", '"is_deleted": False'),
        ("services/steering_service.py", '"is_deleted": False'),
        ("services/mapping_ingest_service.py", '"is_deleted": False'),
    ):
        body = (_ROOT / "app" / rel).read_text(encoding="utf-8")
        check(f"{rel} revives in place", needle in body)


def test_clearing_a_manual_mapping_tombstones_instead_of_hard_deleting():
    """A hard delete leaves nothing for the next startup seed to respect, so the row
    returns on the next boot — the same bug by a different route. The seeds run on
    EVERY boot, unconditionally."""
    body = (_ROOT / "app" / "routers" / "manual_map.py").read_text(encoding="utf-8")
    check("no hard delete on clear", "await existing.delete()" not in body)
    check("tombstoned instead", '"is_deleted": True' in body)


def test_a_retired_learning_can_actually_be_purged():
    """Document.get() delegates to find_one, which this model filters — so the delete
    endpoint 404'd on anything already retired. The only rows you could purge were
    the ones you had not deleted yet."""
    body = (_ROOT / "app" / "routers" / "learned.py").read_text(encoding="utf-8")
    check("delete no longer reads through get()",
          "LearnedMapping.get(PydanticObjectId(learned_id))" not in body)
    check("it reads with include_deleted", "include_deleted=True" in body)


# ── row 7 ───────────────────────────────────────────────────────────────────
def test_the_learning_center_can_see_untagged_rows():
    """client_id_for_conversion can return None; the capture then writes
    client_id=None with is_global=False, matching NEITHER branch of this filter. The
    learning existed and applied to the file — it just could not be seen."""
    body = (_ROOT / "app" / "routers" / "learned.py").read_text(encoding="utf-8")
    check("untagged rows are in scope", '{"client_id": None},' in body)


def test_a_learning_is_filed_under_the_key_its_readers_use():
    """Written under the template's business_object, read by target_object. Three
    bundled templates carry business_object='Supplier' while their conversions are
    'Supplier Import'/'Supplier Address' — so the row landed in a different accordion
    group and the tab the analyst was looking at showed nothing."""
    body = (_ROOT / "app" / "services" / "learning_service.py").read_text(encoding="utf-8")
    check("the mismatch is resolved toward the reader",
          "_normalize(_bo) != _normalize(_tgt)" in body)
    check("and the reason is on the record", "CW #7" in body)


# ── row 1 ───────────────────────────────────────────────────────────────────
def test_mapping_review_offers_every_bound_source():
    """It read conv.dataset_id — the FIRST sheet — while the auto-mapper unions
    source_dataset_ids. The AI could propose a mapping to a column the analyst could
    not see in the dropdown."""
    body = (_ROOT / "app" / "routers" / "mapping.py").read_text(encoding="utf-8")
    check("every bound dataset is queried", '{"dataset_id": {"$in": _ds_ids}}' in body)
    check("falling back to the singular one", "conv.source_dataset_ids" in body)
    check("and repeated column names are collapsed", "_seen_names" in body)


def test_create_all_makes_one_conversion_set_per_workbook():
    """Not one per sheet. The per-row button on the same page had always done this
    correctly, which is the tell: same page, two behaviours, and the primary button
    was the wrong one."""
    if not _FE.exists():
        print("  SKIP  frontend not present in this checkout")
        return
    body = (_FE / "pages" / "ConvertFilePage.tsx").read_text(encoding="utf-8")
    check("rows are grouped by workbook", "byWorkbook" in body)
    check("grouped on the underlying File", "g.lead.file === it.file" in body)
    check("and the group is sent as dataset_ids",
          "datasetIds.length > 1" in body and "dataset_ids: datasetIds" in body)


def test_the_fanout_will_not_duplicate_a_template_across_sheets():
    """Backstop. Idempotency was keyed on the PRIMARY dataset alone, so a second call
    naming a different sheet of the same workbook collided with nothing. "The UI
    stopped doing it" is not the same as "it cannot happen"."""
    body = (_ROOT / "app" / "services" / "object_fanout_service.py").read_text(encoding="utf-8")
    check("existing conversions are matched on overlapping sources",
          "_have & _want" in body)
    check("and not on template alone — a project may load two extracts",
          "may legitimately load two different Customer" in body)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall 27-Jul checks passed")


# ── the same issue, reported again from the screen (31-Jul) ─────────────────
def test_a_rules_own_source_column_actually_feeds_the_rule():
    """Reported with two screenshots: a Value-mapping rule saved as
    "Address Name <- City", and the field still shipping PRIMARY.

    The rule dialog asks for a Source column — "the legacy column this rule
    transforms" — stores it on the TransformationRule, and shows it back in the
    saved-rules banner. Generation then fed the rule the MAPPING's source value and
    ignored the rule's own column entirely. With no mapped source that is an empty
    string, so the value map evaluated "" on every row and returned its default.
    Shipped, visible in the UI, and inert — and the control default then wrote
    PRIMARY over the empty result.
    """
    from app.services.output_service import _transform_frame

    class F:
        def __init__(self, i, n, q):
            self.id, self.field_name, self.sequence = i, n, q

    class M:
        def __init__(self, tid, src=None, status="approved"):
            self.target_field_id, self.source_column, self.status = tid, src, status
            self.default_value = None
            self.approved_by, self.approved_at = "admin@trinamix.com", None
            self.suggested_transformation, self.confidence = None, None

    fields = {1: F(1, "Address Name", 1)}
    # The screenshot verbatim: VALUE_MAP whose pairs are the dialog's placeholder
    # examples and whose default is blank — i.e. "pass the city through unchanged".
    pipes = {1: [{"rule_type": "VALUE_MAP", "source_column": "City",
                  "config": {"active": "production", "inactive": "discontinued",
                             "case_insensitive": True}}]}
    src = pd.DataFrame({"City": ["Hayward", "Dallas", "SP", "New York", ""],
                        "Address 1": ["a", "b", "c", "d", "e"]})

    out, _ = _transform_frame(src, [M(1, None)], fields, pipes, set(), "Supplier Address")
    check("the cities reach the column",
          list(out["Address Name"]) == ["Hayward", "Dallas", "SP", "New York", ""],
          f"got {list(out['Address Name'])}")

    # ...and a mapping the analyst bound to something else still wins. Silently
    # retargeting a deliberate mapping is the eBOS bug pointed the other way.
    out2, _ = _transform_frame(src, [M(1, "Address 1")], fields, pipes, set(),
                               "Supplier Address")
    check("an explicit mapping outranks the rule's column",
          list(out2["Address Name"]) == ["a", "b", "c", "d", "e"],
          f"got {list(out2['Address Name'])}")


def test_the_rules_source_column_survives_frame_pruning():
    """A column nobody declares is dropped from the frame before the rule runs —
    the same fault that shipped Supplier Site empty on 8,561 rows."""
    from app.services.output_service import _rule_referenced_columns
    cols = _rule_referenced_columns([{"rule_type": "VALUE_MAP", "source_column": "City",
                                      "config": {}}])
    check("declared", "City" in cols, f"got {cols}")


def test_saving_a_rule_writes_the_binding_onto_the_mapping():
    """"I updated this in the custom rule, but it is not reflecting in the mapping
    section." Two artefacts describing one decision: the rule holds the source column,
    the screen and the required-field gate read the mapping. The rule now writes the
    decision down where they already look — filling an EMPTY binding only, and lifting
    a not_applicable row, because attaching a rule to a field says it should carry a
    value."""
    body = (_ROOT / "app" / "routers" / "mapping.py").read_text(encoding="utf-8")
    check("the sync exists", "async def _sync_mapping_to_rule(" in body)
    check("it runs on create", "await _sync_mapping_to_rule(r, user.email)" in body)
    check("it only fills an empty binding",
          'if not (m.source_column or "").strip():' in body)
    check("it lifts a discarded mapping",
          'if (m.status or "") in ("not_applicable", "rejected"):' in body)
    check("and it never fails the save", "never fail the rule save over this" in body)


def test_the_mapping_screen_reloads_after_a_rule_is_saved():
    """The other half of "not reflecting in the mapping section", and it is in the
    browser: onSaved closed the modal and flashed a message without refetching
    anything, so the grid and the inspector kept showing their in-memory copy —
    SOURCE "(none)", "Required field with no source and no default" — for a field the
    rule had just bound to a column.

    The server-side sync makes the binding TRUE; this makes the screen SHOW it. Both
    were needed, which is why fixing only the backend still looked broken.
    """
    if not _FE.exists():
        print("  SKIP  frontend not present in this checkout")
        return
    body = (_FE / "pages" / "MappingReviewPage.tsx").read_text(encoding="utf-8")
    i = body.index("<RuleAuthorModal")
    block = body[i:i + 1600]
    check("onSaved refetches", "await loadAll();" in block,
          "the modal closes and the grid keeps its stale copy")
