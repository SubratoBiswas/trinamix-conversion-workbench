"""The screen and the generated file must choose the SAME mapping row.

THE DEFECT
----------
A target field can hold more than one MappingSuggestion. ``mapping_service``
documents why: a re-run race on the suggest-mapping endpoint inserts a second row
for the same (conversion, target field), and no unique index prevents it.

There were two rules for choosing between them. Mapping Review used
``collapse_mapping_dupes`` — status, then a row that actually carries a source
column, then freshest — reachable from exactly one caller. Generation used its
own inline copy in six places, comparing status ALONE with a strict ``>`` over
the order Mongo returned rows in, so a tie went to whichever row came back first.

The path the analyst actually walks makes that decisive. Editing a source column
and saving does NOT move a row out of "suggested" — the update endpoint says so
in its own comment. So the analyst's edited row and an empty auto-map twin tie on
status, the screen breaks the tie on "has a source column" and shows the edit,
and the file breaks it on insertion order and ships the twin. Every track at
once, because it has nothing to do with any business object.

These tests are pure: no Beanie, no Mongo. A mapping here is anything with a
status, a source column, timestamps and a target field id — which is the point of
the shared module having no dependencies.
"""
import ast
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.mapping_dedupe import (                       # noqa: E402
    MAP_STATUS_PRIORITY, best_mapping_by_target, collapse_mapping_dupes,
    dedup_key, stamp_edit,
)

_BACKEND = Path(__file__).resolve().parent.parent
_OLD = datetime(2026, 8, 1, 9, 0, 0)
_NEW = _OLD + timedelta(hours=6)

# Every module that used to carry its own copy of the rule.
CALLERS = [
    "app/services/output_service.py",
    "app/services/learning_service.py",
    "app/services/copilot_grounding.py",
    "app/services/readiness_service.py",
]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def row(tid="F1", status="suggested", source=None, when=_OLD, tag=""):
    return SimpleNamespace(target_field_id=tid, status=status, source_column=source,
                           updated_at=when, created_at=when, tag=tag)


def _old_generator_pick(items):
    """The rule generation used to apply: status only, strict >, first wins a tie.

    Kept here so the regression is demonstrated rather than asserted. A test that
    only checks the new behaviour cannot show that the old one was wrong.
    """
    prio = {"overridden": 4, "approved": 3, "not_applicable": 2, "rejected": 1,
            "suggested": 0}
    best = {}
    for m in items:
        c = best.get(m.target_field_id)
        if c is None or prio.get(m.status or "suggested", 0) > prio.get(
                c.status or "suggested", 0):
            best[m.target_field_id] = m
    return best


def test_the_edited_row_beats_an_empty_twin():
    """The reported bug, in one assertion. Both rows sit at "suggested" because
    saving an edit does not change the status; only one of them carries what the
    analyst typed."""
    edited = row(source="city", tag="edited")
    twin = row(source=None, tag="auto-map twin")
    for order in ([edited, twin], [twin, edited]):
        got = best_mapping_by_target(order)["F1"]
        check(f"edit wins ({[m.tag for m in order]})", got.tag == "edited",
              f"got {got.tag}")


def test_the_old_rule_shipped_the_twin_and_the_new_one_does_not():
    """Proof the fix addresses the reported symptom rather than something near
    it: on the SAME input, the old generator rule picks the empty twin whenever
    Mongo happened to return it first."""
    edited = row(source="city", tag="edited")
    twin = row(source=None, tag="twin")
    old = _old_generator_pick([twin, edited])["F1"]
    check("the old rule shipped the twin", old.tag == "twin", f"got {old.tag}")
    new = best_mapping_by_target([twin, edited])["F1"]
    check("the shared rule ships the edit", new.tag == "edited", f"got {new.tag}")


def test_insertion_order_no_longer_decides_anything():
    """The old comparison was a strict >, so equal-status rows were resolved by
    whatever order the database returned. Nothing about a deliverable should
    depend on that."""
    a = row(source="city", when=_NEW, tag="a")
    b = row(source="postcode", when=_OLD, tag="b")
    check("same answer either way",
          best_mapping_by_target([a, b])["F1"].tag
          == best_mapping_by_target([b, a])["F1"].tag)


def test_the_screen_and_the_file_agree_on_every_shape():
    """The guarantee this module exists for. Both entry points, same input, same
    row — across the combinations that actually occur."""
    cases = {
        "edit vs empty twin": [row(source="city", tag="A"), row(tag="B")],
        "approved vs suggested": [row(status="approved", source="x", tag="A"),
                                  row(source="y", tag="B")],
        "overridden wins": [row(status="approved", source="x", tag="A"),
                            row(status="overridden", source="y", tag="B")],
        "rejected vs not_applicable": [row(status="not_applicable", tag="A"),
                                       row(status="rejected", tag="B")],
        "fresher edit": [row(source="x", when=_OLD, tag="A"),
                         row(source="y", when=_NEW, tag="B")],
        "blank status": [row(status="", tag="A"), row(source="z", tag="B")],
    }
    for name, items in cases.items():
        for order in (items, list(reversed(items))):
            screen = collapse_mapping_dupes(order)[0]
            file_ = best_mapping_by_target(order)["F1"]
            check(f"{name}: screen and file agree", screen.tag == file_.tag,
                  f"screen {screen.tag}, file {file_.tag}")


def test_a_person_outranks_a_rule():
    """rejected over not_applicable. Both mean "no source column", but rejected
    is a person turning a suggestion down while not_applicable is usually the
    engine or a gold example. The generator had these the other way round, so a
    field a person rejected could be suppressed as not-applicable in the file
    while the screen showed it rejected."""
    check("rejected outranks not_applicable",
          MAP_STATUS_PRIORITY["rejected"] > MAP_STATUS_PRIORITY["not_applicable"])
    got = best_mapping_by_target([row(status="not_applicable", tag="na"),
                                  row(status="rejected", tag="rej")])["F1"]
    check("and wins", got.tag == "rej", f"got {got.tag}")


def test_status_still_beats_everything_else():
    """The tie-breaks are tie-breaks. An approved row with no source column must
    still beat a suggested row that has one — a human decision is not overturned
    by a machine's guess having more in it."""
    got = best_mapping_by_target([row(status="suggested", source="guess", tag="guess"),
                                  row(status="approved", source=None, tag="approved")])["F1"]
    check("approved wins", got.tag == "approved", f"got {got.tag}")


def test_freshest_only_decides_a_genuine_tie():
    a = row(source="x", when=_OLD, tag="old")
    b = row(source="y", when=_NEW, tag="new")
    check("newest wins", best_mapping_by_target([a, b])["F1"].tag == "new")


def test_a_missing_timestamp_does_not_raise():
    """Rows written before a field existed, or a corrupt date, must not take
    generation down — this runs inside the file writer."""
    bad = SimpleNamespace(target_field_id="F1", status="suggested",
                          source_column="x", updated_at=None, created_at=None, tag="bad")
    check("no timestamp is survivable", dedup_key(bad)[2] == 0.0)
    check("and it still ranks", best_mapping_by_target([bad])["F1"].tag == "bad")


def test_collapse_keeps_one_row_per_field_and_the_original_order():
    items = [row("F1", source="a", tag="1"), row("F2", source="b", tag="2"),
             row("F1", tag="dupe"), row("F3", source="c", tag="3")]
    out = collapse_mapping_dupes(items)
    check("one per field", len(out) == 3, f"got {len(out)}")
    check("first-occurrence order", [m.target_field_id for m in out] == ["F1", "F2", "F3"])
    check("and the strongest F1", out[0].tag == "1")


def test_stamp_edit_dates_the_write_and_leaves_the_caller_alone():
    """updated_at existed on the model from the start and only the auto-mapper
    ever set it, so a human edit kept its creation time while a re-run of
    auto-map stamped itself with now. Recency then pointed at the machine's guess
    — the reverse of the stated precedence."""
    original = {"source_column": "city"}
    out = stamp_edit(original)
    check("it stamps", isinstance(out.get("updated_at"), datetime))
    check("it keeps the payload", out["source_column"] == "city")
    check("the caller's dict is untouched", "updated_at" not in original)
    check("None is survivable", "updated_at" in stamp_edit(None))


# ---------------------------------------------------------------------------
# Sweeps. The rule was reimplemented six times; these fail if a seventh appears.
# AST, so a comment quoting a priority table cannot satisfy or break them.
# ---------------------------------------------------------------------------
def _dict_literals(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            yield node, set(keys)


def test_no_module_declares_a_second_status_priority_table():
    """The specific way this went wrong: the table was copy-pasted, then the
    copies drifted. One of them can be fixed and the others left, and nothing
    says so."""
    marker = {"overridden", "approved", "suggested"}
    offenders = []
    for path in sorted((_BACKEND / "app").rglob("*.py")):
        if path.name == "mapping_dedupe.py":
            continue
        for node, keys in _dict_literals(path):
            if marker.issubset(keys):
                offenders.append(f"{path.relative_to(_BACKEND)}:{node.lineno}")
    check("only mapping_dedupe declares it", not offenders, f"also at {offenders}")


def test_every_former_caller_uses_the_shared_rule():
    """Seam. A shared rule nobody calls is the inert-feature failure again — and
    this one is the reason the file disagreed with the screen."""
    for rel in CALLERS:
        src = (_BACKEND / rel).read_text(encoding="utf-8")
        check(f"{rel} imports it", "best_mapping_by_target" in src)


def test_no_caller_still_scans_status_by_hand():
    """The copies did not only differ in the table — they compared status alone.
    Fail if any module still walks rows comparing a status lookup."""
    for rel in CALLERS + ["app/services/mapping_service.py"]:
        src = (_BACKEND / rel).read_text(encoding="utf-8")
        for pat in (".get(m.status or", ".get(_m.status or", "_PRIO.get("):
            check(f"{rel} has no hand-rolled scan ({pat})", pat not in src)


def test_the_screen_endpoint_and_the_generator_share_one_implementation():
    """collapse_mapping_dupes must delegate rather than hold a second body — that
    is precisely how the two came apart."""
    src = (_BACKEND / "app" / "services" / "mapping_service.py").read_text(encoding="utf-8")
    i = src.index("def collapse_mapping_dupes")
    body = src[i:i + 700]
    check("it delegates", "mapping_dedupe" in body)
    check("and holds no loop of its own", "for m in items" not in body)


def test_every_mapping_write_in_the_router_stamps_its_date():
    """Recency cannot mean anything if a write does not date itself. AST over the
    mapping router: every ``.set(...)`` on a mapping row goes through stamp_edit.
    """
    src = (_BACKEND / "app" / "routers" / "mapping.py").read_text(encoding="utf-8")
    bare = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set"):
            continue
        recv = node.func.value
        if not (isinstance(recv, ast.Name) and recv.id in ("m", "mp", "row", "sib")):
            continue
        arg = node.args[0] if node.args else None
        ok = (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
              and arg.func.id == "stamp_edit")
        if not ok:
            bare.append(node.lineno)
    check("every mapping write is dated", not bare, f"unstamped at lines {bare}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall mapping dedupe agreement checks passed")
