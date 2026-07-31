"""Plain English in, a real rule out — everywhere, and applied everywhere.

Analyst, 31-Jul, about the "Learn from example & steer" box:

    "If I add a rule here, it should be converted from English to a rule and applied
     in the mappings, stored in learning, applied to all previous (existing)
     conversions and future conversions too." … "all should be done using AI, or a
     Python function whichever is available."

There are TWO English boxes and both were broken, differently.

THE STEER BOX — and this one was destructive, not merely useless. The regexes were
tried before the model, and their order put DEFAULT ahead of MAP. So

    "supplier name should be mapped to legal name for all conversions"

matched `(?P<f>.+?) should be (?P<v>.+?)` and was read as: default the field
"supplier name" to the CONSTANT "mapped to legal name for all conversions" — that
whole sentence, written into the column on every row. It also never propagated to
conversions that already existed, which is the half the analyst asked about by name,
and it wrote its mappings with no approver and no date, so under "the last decision
by date is final" they could never be ranked against anything.

THE TRANSLATE BOX in the rule dialog returned rule_type and config and NOTHING about
the source column, so "Supplier Name should be mapped from Legal Name" set the target
field correctly and left the source picker on `Name` — the screenshot. It also read
only the FIRST bound dataset, so on a multi-sheet workbook a column on any other
sheet could not be resolved at all.

The model goes first now; the deterministic parser is the fallback for when there is
no API key, and it is fixed too — a fallback that is wrong in a destructive direction
is worse than no fallback.

Pure: stdlib. No API calls, no database.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.rule_translation_service import (                    # noqa: E402
    _match_column, _source_from_description,
)
from app.services.steering_service import (                            # noqa: E402
    _DEFAULT_RES, _MAP_RES, _SUPPRESS_RES, _strip_scope,
)

_ROOT = Path(__file__).resolve().parent.parent
COLS = ["Name", "Legal Name", "City", "Address 1", "Entity Id", "Cost Center"]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def classify(line):
    """What the deterministic steer parser makes of one line, in its real order."""
    for rx in _SUPPRESS_RES:
        m = rx.match(line)
        if m:
            return ("suppress", m.group("f"), None)
    for rx in _MAP_RES:
        m = rx.match(line)
        if m:
            return ("map", m.group("f"), _strip_scope(m.group("c")))
    for rx in _DEFAULT_RES:
        m = rx.match(line)
        if m:
            return ("default", m.group("f"), m.group("v"))
    return (None, None, None)


# ── the steer box ───────────────────────────────────────────────────────────
def test_the_sentence_that_was_written_into_the_column_as_a_constant():
    """The exact line from the screenshot. Read as a DEFAULT, it put
    "mapped to legal name for all conversions" into Supplier Name on every row."""
    act, fld, val = classify("supplier name should be mapped to legal name for all conversions")
    check("it is a MAPPING", act == "map", f"got {act} -> {val!r}")
    check("of the right field", fld.strip().lower() == "supplier name", f"got {fld!r}")
    check("to the right column", val == "legal name", f"got {val!r}")


def test_map_is_tried_before_default():
    """Order, not vocabulary, was the bug — both patterns can match the same line."""
    for line in ("Supplier Name should be mapped from Legal Name",
                 "Address Name should be mapped to City",
                 "map Address Name from City",
                 "Supplier Name is mapped using Legal Name"):
        act, _f, _v = classify(line)
        check(f"{line!r} is a map", act == "map", f"got {act}")


def test_a_real_default_is_still_a_default():
    """The fix must not swallow the instruction it was tried before."""
    for line, want in (("set Import Action to CREATE", "CREATE"),
                       ("default Business Relationship to PROSPECTIVE", "PROSPECTIVE"),
                       ("Import Action should be CREATE", "CREATE")):
        act, _f, val = classify(line)
        check(f"{line!r} is a default", act == "default", f"got {act}")
        check("with the right value", val.strip() == want, f"got {val!r}")


def test_leave_blank_still_wins_over_everything():
    for line in ("leave Batch ID blank", "don't map Supplier Name New",
                 "clear Procurement BU"):
        act, _f, _v = classify(line)
        check(f"{line!r} suppresses", act == "suppress", f"got {act}")


def test_scope_phrases_are_not_part_of_the_column_name():
    """"legal name for all conversions" is the column "legal name". Leaving the tail
    on produced a source column that matches nothing in any file."""
    for raw, want in (("legal name for all conversions", "legal name"),
                      ("City in all sheets", "City"),
                      ("Legal Name, for every conversion", "Legal Name"),
                      ("Legal Name", "Legal Name")):
        check(f"{raw!r} -> {want!r}", _strip_scope(raw) == want, f"got {_strip_scope(raw)!r}")


def test_the_model_parses_first_and_the_regexes_are_the_fallback():
    """"all should be done using AI, or a Python function whichever is available."
    The regexes are a small fixed set of shapes that fail silently outside them, so
    they cannot be the primary reader."""
    body = (_ROOT / "app" / "services" / "steering_service.py").read_text(encoding="utf-8")
    ai_at = body.index("directives = await _ai_parse_directives(")
    rx_at = body.index("for rx in _SUPPRESS_RES:")
    check("the model runs before the regexes", ai_at < rx_at)
    check("and the regexes only see what it left",
          "if i in handled_idx:" in body)
    check("the model is given the REAL source columns",
          "_ai_parse_directives(lines, [f.field_name for f in fields], src_names)" in body)


def test_a_steer_instruction_reaches_conversions_that_already_exist():
    """Capturing the learning only ever covered FUTURE conversions. This step was
    missing entirely, and it is the one the analyst named."""
    body = (_ROOT / "app" / "services" / "steering_service.py").read_text(encoding="utf-8")
    check("it propagates", "propagate_learning_to_open_conversions" in body)
    check("and reports how far it got", '"propagated": fanout' in body)


def test_a_steer_decision_is_dated_and_attributed():
    """status=approved with no approver and no date cannot be ranked under "the last
    decision by date is final" — it can never be shown to be recent."""
    body = (_ROOT / "app" / "services" / "steering_service.py").read_text(encoding="utf-8")
    check("an actor is recorded", '"approved_by": actor or "steering-prompt"' in body)
    check("and a timestamp", body.count('"approved_at": datetime.utcnow()') >= 2)
    api = (_ROOT / "app" / "routers" / "conversions.py").read_text(encoding="utf-8")
    check("the endpoint passes the real user",
          'actor=getattr(user, "email", None)' in api)


def test_a_column_the_file_does_not_have_is_reported_not_written():
    """A source column that resolves to nothing reads as MAPPED on screen and
    produces an empty column in the file — the worst of both."""
    body = (_ROOT / "app" / "services" / "steering_service.py").read_text(encoding="utf-8")
    check("names are resolved against the real profile", "def find_source(" in body)
    check("and a miss is reported", '"reason": "no column by that name in this file"' in body)


# ── the translate box ───────────────────────────────────────────────────────
def test_the_translator_returns_the_source_column_the_sentence_names():
    """It returned rule_type and config and nothing else, so the picker kept the
    pre-filled `Name` while the analyst had asked for `Legal Name`."""
    for desc, want in (
        ("Supplier Name should be mapped from Legal Name", "Legal Name"),
        ("supplier name should be mapped to legal name for all conversions", "Legal Name"),
        ("map Address Name from City", "City"),
        ("Address Name should be pulled from City", "City"),
        ("Supplier Name comes from Legal Name.", "Legal Name"),
    ):
        got = _source_from_description(desc, COLS)
        check(f"{desc!r} -> {want!r}", got == want, f"got {got!r}")


def test_the_specific_column_wins_over_the_one_contained_in_it():
    """"Legal Name" and "Name" both appear in the sentence. The longer one is meant,
    and picking the shorter is exactly the bug in the screenshot."""
    check("Legal Name beats Name",
          _source_from_description("Supplier Name should be mapped from Legal Name",
                                   COLS) == "Legal Name")
    check("_match_column agrees",
          _match_column(COLS, "legal name") == "Legal Name")


def test_a_transform_instruction_does_not_retarget_the_column():
    """"trim the supplier name" is a transform on the column already chosen. Reading
    a column name out of it would silently re-bind the field — no suggestion is much
    better than a wrong one."""
    for desc in ("trim the supplier name", "uppercase the name",
                 "pad Entity Id to 8 characters"):
        got = _source_from_description(desc, COLS)
        check(f"{desc!r} suggests nothing", got is None, f"got {got!r}")


def test_the_translator_sees_every_bound_sheet():
    """It read the singular conversion.dataset_id, so a column on any sheet but the
    first could not be resolved at all."""
    body = (_ROOT / "app" / "services" / "rule_translation_service.py").read_text(encoding="utf-8")
    check("every bound dataset", '{"dataset_id": {"$in": ids}}' in body)
    check("reading source_dataset_ids", '"source_dataset_ids"' in body)
    check("with the singular one as the fallback",
          'if not ids and getattr(conversion, "dataset_id", None):' in body)


def test_the_modal_moves_the_picker_and_says_so():
    fe = _ROOT.parent / "frontend" / "src"
    if not fe.exists():
        print("  SKIP  frontend not present in this checkout")
        return
    body = (fe / "components" / "transforms" / "RuleAuthorModal.tsx").read_text(encoding="utf-8")
    check("the picker moves", "if (_ns) setSourceColumn(_ns);" in body)
    check("and the change is explained", "source_column_changed_from" in body)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall English-to-rule checks passed")


# ── the single-column box: intent, not a fabricated rule ────────────────────
def test_a_remap_sentence_is_a_mapping_change_not_a_value_map():
    """The screenshot: "Supplier Name should be mapped from Legal Name" came back as
    a VALUE MAPPING pre-filled with the placeholder pairs active->production. That is
    neither what was asked for nor anything anyone would keep. Three of the four
    things typed about one column are mapping changes and need no rule at all."""
    from app.services.rule_translation_service import detect_intent
    got = detect_intent("Supplier Name should be mapped from Legal Name", COLS)
    check("it is a remap", (got or {}).get("intent") == "remap", f"got {got}")
    check("at the right column", got["source_column"] == "Legal Name")
    check("and it says no rule is needed", "mapping change" in got["explanation"])


def test_default_and_blank_are_also_mapping_changes():
    from app.services.rule_translation_service import detect_intent
    d = detect_intent("set it to CREATE", COLS)
    check("default detected", (d or {}).get("intent") == "default", f"got {d}")
    check("with the value", d["value"] == "CREATE", f"got {d.get('value')!r}")
    b = detect_intent("leave it blank", COLS)
    check("blank detected", (b or {}).get("intent") == "blank", f"got {b}")


def test_a_real_transformation_still_produces_a_rule():
    """The intent pass must not swallow the case the dialog exists for."""
    from app.services.rule_translation_service import detect_intent
    for desc in ("trim the supplier name", "uppercase the legal name",
                 "pad Entity Id to 8 characters",
                 "if Email Transaction is Yes then EMAIL else FAX",
                 "set it to uppercase"):
        got = detect_intent(desc, COLS)
        check(f"{desc!r} is left to the rule parser", got is None, f"got {got}")


def test_the_modal_applies_the_intent_instead_of_saving_a_junk_rule():
    fe = _ROOT.parent / "frontend" / "src"
    if not fe.exists():
        print("  SKIP  frontend not present in this checkout")
        return
    body = (fe / "components" / "transforms" / "RuleAuthorModal.tsx").read_text(encoding="utf-8")
    check("the intent is read", 'const _intent = (res as any).intent' in body)
    check("save applies a mapping change",
          'if (nlIntent === "remap" || nlIntent === "default" || nlIntent === "blank")' in body)
    check("blank goes through keep-blank", "MappingApi.keepBlank(mid)" in body)
    check("and no rule is created on that path", "return;\n      }\n      const body = {" in body)


# ── the steer box: the whole output, not one sheet ─────────────────────────
def test_steering_corrects_every_object_in_the_load_sequence():
    """"This should be prompts to correct mapping for all the output." A Supplier
    load is SIX conversions with six different business objects, so an object-scoped
    fan-out reaches one sixth of what the analyst calls the output."""
    st = (_ROOT / "app" / "services" / "steering_service.py").read_text(encoding="utf-8")
    check("the bundle is resolved", "async def _bundle_objects(" in st)
    check("and passed to the fan-out", "extra_object_keys=bundle" in st)
    ls = (_ROOT / "app" / "services" / "learning_service.py").read_text(encoding="utf-8")
    check("propagation accepts it", "extra_object_keys: list[str] | None = None" in ls)
    check("and widens the object match",
          "for _k in (extra_object_keys or []):" in ls)
    check("while every per-conversion guard still applies",
          "_src_ok" in ls and "sheet_allowed(lm, _sheet_of.get(i))" in ls)
