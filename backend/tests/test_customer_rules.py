"""The nine authored Customer rules (CW_Issues 15-24) and the two new rule types.

These were the rows the report called "Authorable": the engine could express each one
and nobody had typed it. On 30-Jul the analyst said to author all of them, so each is
exercised here against the stated case AND against the ways it can go wrong, because a
transformation rule that is subtly wrong ships in a file that looks correct.

Two rule types are new. SEQUENCE (CW #23) and SUFFIX_WHEN (CW #19) could not be built
from what existed: CASE_WHEN can only REPLACE a value and SUFFIX can only append a
FIXED one, so neither can append a suffix chosen per row.

CW #23 carries a warning in its own right. Auto-generated key numbers were removed once
before (section 10.6) because a manufactured unique value makes genuine duplicates look
distinct and they then load twice. The guards are tested here: a real source key always
wins, and the number is derived from the row index so a re-run does not renumber every
party and break the links the other 18 Customer sheets carry.

Pure: stdlib only.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.transformation import RULE_TYPES  # noqa: E402
from app.services.customer_rules_service import load_rules, open_questions  # noqa: E402
from app.transformations.engine import apply_rule  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def _authored():
    """Every rule the 30-Jul instruction authored, superseded or not.

    Read from the shipped JSON rather than from ``load_rules()``, which now FILTERS
    — on 03-Aug the analyst rewrote seven of these nine, and ``load_rules`` stopped
    handing those out so the "apply the authored rules" button cannot re-install a
    superseded version over the newer one.

    The tests below still belong here and still assert the file, unweakened: what
    the 30-Jul instruction said, and that the engine can run it, are facts that did
    not stop being true. What CHANGED is which of them a conversion gets, and that
    is asserted directly in ``test_only_the_rules_that_survived_are_handed_out``
    below and in test_customer_mapping_03aug.py, which covers the 03-Aug
    replacement for each one.
    """
    import json as _json
    from pathlib import Path as _Path
    doc = _json.loads((_Path(__file__).resolve().parent.parent / "app" / "data"
                       / "customer_rules_nextpower.json").read_text(encoding="utf-8"))
    return [r for r in (doc.get("rules") or [])
            if r.get("target_field") and r.get("rule_type")]


def rule(cw, target=None):
    """The authored rule for a CW row, straight from the shipped JSON — so these
    assertions test what was actually written, not a copy of it."""
    for r in _authored():
        if r.get("cw") == cw and (target is None or r["target_field"] == target):
            return r
    raise AssertionError(f"no authored rule for CW #{cw} {target or ''}")


def run(r, value, row=None, ctx=None):
    return apply_rule(r["rule_type"], r["rule_config"], value, row or {}, ctx or {})


# ── The JSON itself ─────────────────────────────────────────────────────────
def test_all_nine_rows_are_authored():
    got = sorted({r["cw"] for r in _authored()})
    check("CW 15-24 covered", got == [15, 16, 17, 18, 19, 20, 22, 23, 24], f"got {got}")
    check("ten bindings (CW #19 covers two fields)", len(_authored()) == 10,
          f"got {len(_authored())}")


def test_only_the_rules_that_survived_03aug_are_handed_out():
    """What a conversion actually gets. On 03-Aug the analyst rewrote seven of the
    nine — Party Type gained the organization-name test it never had, Party Number
    is sequenced on entityid, the site-use suffix and BILL_TO/SHIP_TO read which
    SHEET the row came from rather than the Default Billing / Default Shipping
    flags. Handing the July version out now would write it onto the conversion
    stamped with today, and a conversion rule newer than the document wins — so the
    button labelled "apply the analyst's rules" would quietly undo them.

    CW #18 survives: the new document does not mention Primary Indicator, and
    superseding is per field, not wholesale."""
    got = sorted({r["cw"] for r in load_rules()})
    check("only what has not been rewritten", got == [18], f"got {got}")
    check("one binding left", len(load_rules()) == 1, f"got {len(load_rules())}")


def test_every_rule_type_is_registered():
    """An unregistered rule_type is accepted at save and then silently does nothing."""
    for r in _authored():
        check(f"{r['rule_type']} registered", r["rule_type"] in RULE_TYPES)


def test_party_number_runs_after_party_type():
    """CW #23 reads Party Type, which CW #22 derives. Wrong order and every row takes
    the ORGANIZATION branch — a plausible-looking key that is wrong for every person."""
    from app.services.customer_rules_service import _ORDER
    order = [r["cw"] for r in sorted(
        _authored(), key=lambda r: (_ORDER.index(r["rule_type"])
                                    if r["rule_type"] in _ORDER else len(_ORDER)))]
    check("22 before 23", order.index(22) < order.index(23), f"got {order}")


def test_the_open_questions_are_carried_not_buried():
    qs = " ".join(open_questions()).lower()
    check("the two key widths are flagged", "width" in qs, "the NXT000001 vs "
          "NXT00001_C1 length difference must be surfaced")
    check("section 10.6 is flagged", "10.6" in qs)


def test_source_columns_are_candidate_lists_not_single_guesses():
    """The spellings came from prose. One guess binds to nothing and fails silently;
    a list binds to whichever the extract uses and costs nothing when wrong."""
    r = rule(15)
    check("COALESCE lists several spellings",
          len(r["rule_config"]["columns"]) >= 4,
          f"got {r['rule_config']['columns']}")
    r16 = rule(16)
    check("CASE_WHEN lists several spellings",
          len(r16["rule_config"]["branches"]) >= 3)


# ── CW #15 — COALESCE ───────────────────────────────────────────────────────
def test_account_established_date_falls_back():
    r = rule(15)
    check("StartDate wins",
          run(r, "", {"StartDate": "2020/01/15", "datecreated": "2019/05/01"})
          == "2020/01/15")
    check("blank StartDate falls back to datecreated",
          run(r, "", {"StartDate": "", "datecreated": "2019/05/01"}) == "2019/05/01")
    check("both blank -> blank, not a made-up date",
          run(r, "", {"StartDate": "", "datecreated": ""}) == "")
    check("a differently-spelled column still binds",
          run(r, "", {"Start Date": "2021/03/02"}) == "2021/03/02")


# ── CW #16 — Contact Point Type ─────────────────────────────────────────────
def test_contact_point_type():
    r = rule(16)
    check("email present -> EMAIL",
          run(r, "", {"Email Address": "a@b.com"}) == "EMAIL")
    check("email blank -> PHONE", run(r, "", {"Email Address": ""}) == "PHONE")
    check("no email column at all -> PHONE", run(r, "", {"Phone": "555"}) == "PHONE")
    check("lower-case spelling binds too",
          run(r, "", {"email": "a@b.com"}) == "EMAIL")


# ── CW #17 — Phone Line Type ────────────────────────────────────────────────
def test_phone_line_type_reads_which_column_carries_the_number():
    r = rule(17)
    check("mobile column -> MOBILE",
          run(r, "", {"Mobile Phone": "555-0100", "Fax": ""}) == "MOBILE")
    check("fax column -> FAX",
          run(r, "", {"Mobile Phone": "", "Fax": "555-0199"}) == "FAX")


def test_a_row_with_both_a_mobile_and_a_fax_is_a_mobile():
    """Deliberate: a record carrying both is a mobile number with a fax on file."""
    r = rule(17)
    check("MOBILE wins",
          run(r, "", {"Mobile Phone": "555-0100", "Fax": "555-0199"}) == "MOBILE")


def test_phone_line_type_defaults_to_blank_not_to_a_guess():
    """It is a coded column. Inventing a code fails the value-set check with no clue
    where the value came from; blank is honest and visible."""
    r = rule(17)
    check("blank", run(r, "", {"Mobile Phone": "", "Fax": ""}) == "")


# ── CW #18 / #24 — bill-to vs ship-to ───────────────────────────────────────
def test_primary_indicator():
    r = rule(18)
    check("billing -> BILL_TO", run(r, "", {"Default Billing": "T"}) == "BILL_TO")
    check("shipping -> SHIP_TO", run(r, "", {"DefaultShipping": "T"}) == "SHIP_TO")
    check("neither -> blank", run(r, "", {"Default Billing": "",
                                          "DefaultShipping": ""}) == "")


def test_party_site_use_type():
    r = rule(24)
    check("BILL_TO", run(r, "", {"Default Billing": "T"}) == "BILL_TO")
    check("SHIP_TO", run(r, "", {"DefaultShipping": "T"}) == "SHIP_TO")


# ── CW #20 — CONCAT ─────────────────────────────────────────────────────────
def test_party_site_name_concat():
    r = rule(20)
    check("joined with an underscore",
          run(r, "", {"entityid": "C123", "address_label": "HQ"}) == "C123_HQ")


def test_concat_does_not_manufacture_a_bare_separator():
    """The guard that exists because Supplier Site once shipped 8,561 rows of a bare
    '-' into a required unique key when neither input column was present."""
    r = rule(20)
    check("falls back rather than emitting '_'",
          run(r, "keep-me", {"other": "x"}) == "keep-me")


def test_concat_with_one_side_present_still_joins():
    r = rule(20)
    out = run(r, "", {"entityid": "C123", "address_label": ""})
    check("no crash, keeps what there is", out == "C123_", f"got {out!r}")


# ── CW #22 — Party Type ─────────────────────────────────────────────────────
def test_party_type():
    r = rule(22)
    check("first name -> PERSON", run(r, "", {"First Name": "Jane"}) == "PERSON")
    check("last name only -> PERSON", run(r, "", {"Last Name": "Smith"}) == "PERSON")
    check("neither -> ORGANIZATION",
          run(r, "", {"First Name": "", "Last Name": ""}) == "ORGANIZATION")
    check("no name columns -> ORGANIZATION",
          run(r, "", {"companyname": "Acme Ltd"}) == "ORGANIZATION")


# ── CW #19 — SUFFIX_WHEN (new) ──────────────────────────────────────────────
def test_suffix_by_bill_to_or_ship_to():
    r = rule(19, "Account Site Purpose SSR")
    check("_b on a billing row",
          run(r, "REF1", {"Default Billing": "T"}) == "REF1_b")
    check("_s on a shipping row",
          run(r, "REF1", {"DefaultShipping": "T"}) == "REF1_s")
    check("neither -> untouched",
          run(r, "REF1", {"Default Billing": "", "DefaultShipping": ""}) == "REF1")


def test_the_suffix_is_not_applied_twice_on_a_re_run():
    """Generation can run more than once over the same frame. A key that gains a
    second '_b' each pass is a new, silent data defect."""
    r = rule(19, "Account Site Purpose SSR")
    once = run(r, "REF1", {"Default Billing": "T"})
    check("idempotent", run(r, once, {"Default Billing": "T"}) == "REF1_b",
          f"got {run(r, once, {'Default Billing': 'T'})!r}")


def test_a_blank_value_gets_no_suffix():
    """A bare '_b' is not a reference; it is a guaranteed-duplicate invalid key."""
    r = rule(19, "Account Site Purpose SSR")
    check("blank stays blank", run(r, "", {"Default Billing": "T"}) == "")


def test_both_suffix_fields_are_authored():
    fields = {r["target_field"] for r in _authored() if r["cw"] == 19}
    check("both named in the row",
          fields == {"Account Site Purpose SSR",
                     "Original System Party Site Use Reference"}, f"got {fields}")


# ── CW #23 — SEQUENCE (new) ─────────────────────────────────────────────────
def test_party_number_sequence():
    r = rule(23)
    check("first row", run(r, "", {}, {"row_index": 0}) == "NXT000001")
    check("second row", run(r, "", {}, {"row_index": 1}) == "NXT000002")
    check("row 999", run(r, "", {}, {"row_index": 998}) == "NXT000999")


def test_a_person_takes_the_variant_form():
    r = rule(23)
    out = run(r, "", {"Party Type": "PERSON"}, {"row_index": 0})
    check("NXT00001_C1", out == "NXT00001_C1", f"got {out!r}")
    check("an organization keeps the plain form",
          run(r, "", {"Party Type": "ORGANIZATION"}, {"row_index": 0}) == "NXT000001")


def test_a_real_source_key_always_beats_the_generated_one():
    """The first half of the section 10.6 guard: if the extract carries the number of
    record, that is the number — a manufactured one would orphan every reference."""
    r = rule(23)
    check("source wins", run(r, "EXISTING-42", {}, {"row_index": 5}) == "EXISTING-42")


def test_the_number_is_stable_for_a_given_row():
    """The second half. Derived from the row index, not a counter, so regenerating the
    file does not renumber every party and break the links the other 18 sheets hold."""
    r = rule(23)
    first = run(r, "", {}, {"row_index": 7})
    check("same row, same number twice", run(r, "", {}, {"row_index": 7}) == first)
    check("and it is not a counter that advanced", first == "NXT000008", f"got {first}")


def test_the_sequence_never_collides_across_rows():
    r = rule(23)
    seen = {run(r, "", {"Party Type": "PERSON" if i % 3 else "ORGANIZATION"},
                {"row_index": i}) for i in range(500)}
    check("500 distinct keys", len(seen) == 500, f"got {len(seen)}")


def test_a_missing_row_index_does_not_crash():
    """ctx is optional on many call paths; a KeyError here would fail generation."""
    r = rule(23)
    check("falls back to the first number", run(r, "", {}, {}) == "NXT000001")


# ── Engine-level behaviour of the two new types ─────────────────────────────
def test_sequence_width_and_prefix_are_configurable():
    out = apply_rule("SEQUENCE", {"prefix": "ACC", "width": 4, "start": 100},
                     "", {}, {"row_index": 0})
    check("ACC0100", out == "ACC0100", f"got {out!r}")


def test_suffix_when_falls_through_to_a_default_suffix():
    out = apply_rule("SUFFIX_WHEN",
                     {"branches": [{"if_column": "x", "op": "eq", "value": "1",
                                    "suffix": "_a"}], "default_suffix": "_z"},
                     "K", {"x": "9"})
    check("_z", out == "K_z", f"got {out!r}")


def test_an_unknown_op_is_skipped_rather_than_crashing():
    out = apply_rule("SUFFIX_WHEN",
                     {"branches": [{"if_column": "x", "op": "nonsense",
                                    "suffix": "_a"}]}, "K", {"x": "1"})
    check("untouched", out == "K", f"got {out!r}")


def test_the_shipped_json_parses_and_is_documented():
    p = (Path(__file__).resolve().parent.parent / "app" / "data"
         / "customer_rules_nextpower.json")
    doc = json.loads(p.read_text(encoding="utf-8"))
    for key in ("_source", "_scope", "_column_spellings", "_open_questions", "rules"):
        check(f"{key} present", key in doc)
    for r in doc["rules"]:
        check(f"CW #{r.get('cw')} has a description", bool(r.get("description")))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except AssertionError:
            pass
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("all checks passed")
