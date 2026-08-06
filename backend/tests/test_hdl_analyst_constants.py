"""A fixed value set in Mapping Review must reach the .dat file.

THE DEFECT
----------
Reported with a screenshot pair: Mapping Review showed EffectiveStartDate filled
with the constant 1/1/1900, re-applied from the library, explained in the AI
panel — and the generated file carried 1990/01/01 on every row.

1990/01/01 is the value hard-coded in ``hdl_schema`` for the Location and Job
components, with a comment recording why: the client's own HDL template says
1990-01-01 where the field-mapping workbook says 1/1/1900, one digit apart, and a
previous session judged the template the artefact they actually load.

That judgement is defensible. What was not is that ``render_cell`` returned the
schema constant UNCONDITIONALLY. It honoured the analyst's source COLUMN — there
is an override map for exactly that — and never once asked what the analyst had
set as a VALUE. So the disagreement could not be resolved by the person looking
at it: they could type 1/1/1900, watch it save, watch it re-apply, and get
1990/01/01 in the file with nothing anywhere saying why.

And it was never about that one field. Every ``_const`` on the whole HDL path
behaved the same way — SetCode, EffectiveEndDate, all of them — so every fixed
value and every Keep blank an analyst set on an HDL component was stored, shown,
and ignored at write time. The inert-data pattern, one layer down from where it
has been found before.

The schema's constant is still there and still the default. It is no longer an
override of a person.

Pure: stdlib only. ``render_cell`` is module-level precisely so this needs no
database.
"""
import ast
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.hdl_output_service import NO_ANSWER, render_cell   # noqa: E402
from app.services import hdl_schema                                  # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _no_source(_field, _source=None):
    return None


def _analyst(answers: dict):
    return lambda name: answers.get(name, NO_ANSWER)


def test_the_reported_case_exactly():
    """Screen said 1/1/1900, file said 1990/01/01."""
    spec = {"kind": "const", "name": "EffectiveStartDate", "value": "1990/01/01"}
    check("without an analyst answer the schema constant stands",
          render_cell(spec, _no_source) == "1990/01/01")
    got = render_cell(spec, _no_source, _analyst({"EffectiveStartDate": "1/1/1900"}))
    check("with one, the analyst's value is written", got == "1/1/1900", f"got {got}")


def test_the_schema_still_supplies_the_default():
    """The point is precedence, not deletion. A conversion where nobody has said
    anything still gets the client's template value."""
    for comp, _obj, fields in hdl_schema.all_components():
        for spec in fields:
            if spec.get("kind") == "const":
                check(f"{comp}.{spec['name']} still has its constant",
                      render_cell(spec, _no_source) == str(spec["value"]).strip())


def test_every_constant_on_the_path_is_overridable_not_just_the_date():
    """It was never about EffectiveStartDate. Walk the real schema and prove the
    analyst can override each constant it declares."""
    seen = 0
    for _comp, _obj, fields in hdl_schema.all_components():
        for spec in fields:
            if spec.get("kind") != "const":
                continue
            seen += 1
            got = render_cell(spec, _no_source, _analyst({spec["name"]: "ANALYST"}))
            check(f"{spec['name']} is overridable", got == "ANALYST", f"got {got}")
    check("the schema really does declare constants", seen >= 5, f"found {seen}")


def test_keep_blank_survives_as_an_instruction():
    """"" and "nothing to say" are different answers. Ship-this-column-empty is a
    decision, and a sentinel is what keeps it from being read as absence."""
    spec = {"kind": "const", "name": "SetCode", "value": "COMMON"}
    check("keep blank wins", render_cell(spec, _no_source, _analyst({"SetCode": ""})) == "")
    check("silence does not", render_cell(spec, _no_source, _analyst({})) == "COMMON")


def test_the_analyst_beats_a_source_column_too():
    """The panel that sets a fixed value says it overrides AI and clears the
    source column. So it does — for a src spec as much as a const one."""
    spec = {"kind": "src", "name": "LocationName", "source": "Location"}
    resolve = lambda _f, _s: "FROM THE FILE"          # noqa: E731
    check("without an answer the source is used",
          render_cell(spec, resolve) == "FROM THE FILE")
    check("with one, the analyst wins",
          render_cell(spec, resolve, _analyst({"LocationName": "TYPED"})) == "TYPED")


def test_const_if_blank_still_prefers_the_extract():
    """Strategy 9.1 is untouched: the extract wins over the open-ended fallback.
    Only the analyst outranks it."""
    spec = {"kind": "const_if_blank", "name": "EffectiveEndDate",
            "source": "Location End Date", "value": "4712/12/31"}
    check("extract wins over the fallback",
          render_cell(spec, lambda _f, _s: "2026/01/01") == "2026/01/01")
    check("fallback when the extract is empty",
          render_cell(spec, _no_source) == "4712/12/31")
    check("analyst outranks both",
          render_cell(spec, lambda _f, _s: "2026/01/01",
                      _analyst({"EffectiveEndDate": "2030/06/30"})) == "2030/06/30")


def test_a_missing_analyst_argument_changes_nothing():
    """The signature stays backwards compatible — every existing caller and every
    existing test passes two arguments."""
    spec = {"kind": "const", "name": "SetCode", "value": "COMMON"}
    check("two-argument call still works", render_cell(spec, _no_source) == "COMMON")


def test_the_generator_actually_passes_the_analyst_answers():
    """Seam. An override map nothing consults is the inert-feature failure that
    caused this in the first place."""
    src = (_BACKEND / "app" / "services" / "hdl_output_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "render_cell"]
    check("the generator calls it", calls)
    check("with the analyst answers", all(len(c.args) >= 3 for c in calls),
          "render_cell is still being called without the analyst lookup")
    check("built from the mapping rows' fixed values",
          "const_override[(comp, fn)] = _dv" in src)
    check("and from keep-blank", 'const_override[(comp, fn)] = ""' in src)


def test_the_hdl_path_picks_one_row_per_field_like_everywhere_else():
    """This path walked every mapping row, so a duplicate left by the
    suggest-mapping race could bind a column to whichever came back last — the
    same defect the FBDI path had, on a path nobody had checked."""
    src = (_BACKEND / "app" / "services" / "hdl_output_service.py").read_text(encoding="utf-8")
    check("it uses the shared rule", "best_mapping_by_target(maps)" in src)
    check("and no longer walks the raw list", "for m in maps:" not in src)


def test_the_recorded_disagreement_is_still_recorded():
    """The 1990 vs 1900 evidence must not be lost just because the analyst can now
    override it. Whoever changes this next needs to know the client's own HDL
    template is where 1990-01-01 came from."""
    src = (_BACKEND / "app" / "services" / "hdl_schema.py").read_text(encoding="utf-8")
    check("the reason survives", "HDL template says 1990-01-01" in src)
    check("and names the other document", "1/1/1900" in src)


def test_custom_rules_now_run_on_the_hcm_path():
    """They never did. A transformation rule authored against a Worker/HCM field
    was saved, listed and explained, and no code on the way to the .dat file ever
    asked for it — while the FBDI generator had run these since it was written.
    Nothing about HCM makes a rule less of an instruction."""
    src = (_BACKEND / "app" / "services" / "hdl_output_service.py").read_text(encoding="utf-8")
    check("the rules are loaded", "TransformationRule.find(" in src)
    check("in sequence order", "sort(+TransformationRule.sequence)" in src)
    check("keyed by field", "rules_by_field.setdefault" in src)
    check("and executed", "apply_pipeline(_rules, value" in src)


def test_a_rule_runs_on_the_value_the_field_ended_up_with():
    """Order matters and matches the FBDI generator: the value is decided first —
    source, schema constant or the analyst's fixed value — and the rule
    transforms THAT. A rule is a transformation of a value, not a competitor
    to it."""
    src = (_BACKEND / "app" / "services" / "hdl_output_service.py").read_text(encoding="utf-8")
    i = src.index("value = render_cell(spec, _resolve,")
    j = src.index("apply_pipeline(_rules, value", i)
    check("render first, then the rule", j > i)


def test_a_failing_rule_does_not_take_the_file_down():
    src = (_BACKEND / "app" / "services" / "hdl_output_service.py").read_text(encoding="utf-8")
    i = src.index("apply_pipeline(_rules, value")
    body = src[i - 300:i + 500]
    check("it is guarded", "except Exception" in body)
    check("and the untransformed value still ships", "return value" in body)


def test_authoring_a_rule_counts_as_an_explicit_decision():
    """The other half of the same complaint, on the FBDI side. _explicit was read
    off the MAPPING's status, so a custom rule on a field whose mapping still sat
    at "suggested" was unprotected and a strategy constant replaced every row.
    A rule has no status to approve — it has a date, and date is how everything
    here is ranked."""
    src = (_BACKEND / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
    check("authored rules are captured", "_authored_rules = list(rules)" in src)
    check("ranked by date like everything else",
          "_conversion_rule_wins(\n            _authored_rules, _asof)" in src
          or "_conversion_rule_wins(_authored_rules, _asof)" in src)
    check("and they make the field explicit", "or _authored_rule_wins" in src)


def test_the_engines_own_suggestion_does_not_count_as_speaking():
    """suggested_transformation is a guess. Counting it would hand every
    auto-suggested field the protection meant for a person, and the strategy
    constants exist precisely to correct auto-suggestions."""
    src = (_BACKEND / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
    i = src.index("_authored_rules = list(rules)")
    j = src.index("m.suggested_transformation and not rules", i)
    check("authored is captured BEFORE the suggestion is appended", i < j)


def test_hdl_rule_dict_carries_config_the_pipeline_can_read():
    """THE 06-Aug DEFECT. The HDL path built each rule dict with the key
    ``rule_config`` (the model FIELD's name), but apply_pipeline reads
    ``r.get("config", {})``. So every rule reached the engine with an EMPTY config:
    a CASE_WHEN with no branches returns its input unchanged. Live: Country
    (Saudi Arabia->SA) and OnMilitaryServiceFlag (0->N,1->Y) shipped the raw source
    value. This runs a rule through the SAME dict shape the writer builds and proves
    it transforms — the check the presence-only wiring test could never make."""
    from app.transformations import apply_pipeline

    # exactly the live OnMilitaryServiceFlag rule config
    mil_cfg = {"branches": [
        {"if_column": "Military_Service", "op": "eq", "value": "0", "then": "N"},
        {"if_column": "Military_Service", "op": "eq", "value": "1", "then": "Y"}],
        "default": ""}
    good = [{"rule_type": "CASE_WHEN", "config": mil_cfg}]           # what the fix builds
    bad = [{"rule_type": "CASE_WHEN", "rule_config": mil_cfg}]       # what the bug built
    check("0 -> N with the config key",
          apply_pipeline(good, "0", row={"Military_Service": "0"}) == "N")
    check("1 -> Y with the config key",
          apply_pipeline(good, "1", row={"Military_Service": "1"}) == "Y")
    # the bug reproduced: wrong key -> empty config -> raw value passes through
    check("wrong key ships the raw value (the bug)",
          apply_pipeline(bad, "0", row={"Military_Service": "0"}) == "0")

    # Country, the other reported field
    ctry_cfg = {"branches": [
        {"if_column": "Country", "op": "eq", "value": "Saudi Arabia", "then": "SA"},
        {"if_column": "Country", "op": "eq", "value": "Israel", "then": "IL"}],
        "default": ""}
    country = [{"rule_type": "CASE_WHEN", "config": ctry_cfg}]
    check("Saudi Arabia -> SA",
          apply_pipeline(country, "Saudi Arabia", row={"Country": "Saudi Arabia"}) == "SA")


def test_the_hdl_writer_appends_the_rule_under_the_config_key():
    """Source guard so the key cannot silently revert to ``rule_config``. The dict
    the writer appends must carry ``"config":`` — the key the pipeline reads — not
    ``"rule_config":``, which would be an empty config to apply_pipeline."""
    src = _src()
    i = src.index("rules_by_field.setdefault")
    block = src[i:i + 500]
    check('the pipeline key "config" is used', '"config": _r.rule_config' in block,
          block[:300])
    check('the model-field key is not passed as the pipeline key',
          '"rule_config": _r.rule_config' not in block)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall HDL analyst-constant checks passed")


def _src() -> str:
    """The HDL writer's source. Read fresh each time so a test cannot pass on a
    stale copy."""
    return (_BACKEND / "app" / "services"
            / "hdl_output_service.py").read_text(encoding="utf-8")


# ── Latest date wins on the HDL path too (05-Aug) ────────────────────────────
#
# Employee is the only object that generates through this writer, and it had no
# date test at all: a fixed value was taken as const_override and every rule then
# transformed it, however old the value or however new the rule. The FBDI path
# ranks the two by date. One intent, two behaviours, decided by which loader the
# object happened to use — which is exactly the "two copies of a rule" shape this
# codebase keeps paying for.
#
# Analyst, 05-Aug: "just follow the date, latest date wins in mapping and
# constants in all modules."

def test_the_hdl_writer_carries_the_rules_own_date():
    """A rule cannot be ranked against anything without one."""
    src = _src()
    i = src.index("rules_by_field.setdefault")
    block = src[i:i + 400]
    check("the rule's date is carried", '"as_of"' in block, block[:200])
    check("from the rule row", "created_at" in block)


def test_the_hdl_writer_records_when_a_fixed_value_was_approved():
    src = _src()
    check("there is a date map for constants", "const_at: dict" in src)
    # A generous window: the reasoning beside this line is long, and a short
    # slice would fail on the comment rather than on the code.
    i = src.index("const_override[(comp, fn)] = _dv")
    block = src[i:i + 1200]
    check("it is stamped beside the value", "const_at[(comp, fn)]" in block, block[:220])
    check("only for an approved row",
          '("approved", "overridden")' in block or "'approved', 'overridden'" in block)
    check("and only when it carries a date", "_dv_at is not None" in block)


def test_a_fixed_value_newer_than_every_rule_is_not_transformed():
    """The alignment itself: the later statement wins, so the rule does not get to
    rewrite a value approved after it."""
    src = _src()
    i = src.index("_rules = rules_by_field.get((comp, _fn))")
    j = src.index("apply_pipeline(_rules, value", i)
    block = src[i:j]
    check("the constant's date is consulted", "const_at.get(" in block, block[:300])
    check("against the newest rule", "max(_rule_dates)" in block)
    check("and the value is returned untransformed", "return value" in block)


def test_an_undated_fixed_value_still_lets_the_rule_run():
    """Same rule as everywhere else: undated cannot be shown to be later, so it
    does not outrank anything."""
    src = _src()
    i = src.index("_at = const_at.get(")
    block = src[i:i + 400]
    check("a missing date falls through to the rule", "_at is not None" in block,
          block[:200])
