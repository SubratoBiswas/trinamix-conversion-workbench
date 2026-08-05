""""Earlier a logic was implemented for delivery method. In the new output FBDI it's blank."

Reported 04-Aug against the generated supplier FBDI: the Delivery Method column
empty on every row, having previously worked.

The rule was not missing. It was authored on 28-Jul, restated on 30-Jul with
every plausible column spelling, marked AUTHORITATIVE, and reads correctly. It
shipped blank anyway.

THE LIKELIEST CAUSE IS A REGRESSION FROM EARLIER THE SAME DAY. `_conversion_rule_wins`
was added so a write-time overlay would stop overwriting a rule the analyst had
typed on the conversion itself. That guard is right in general and wrong here: a
rule SEEDED onto a conversion carries the date it was seeded, not the date it was
decided, so a stale seed outranks a dated instruction. supplier_transform_mappings.json
seeds a Delivery Method CASE_WHEN off the Email/Fax TRANSACTION FLAGS, and the
28-Jul note says in as many words that it must be retired or "the two CASE_WHENs
will both target this field". A stale rule reading columns the extract does not
have produces exactly what was reported.

The fix is the one the architecture already has: the analyst restated the rule
today, so it is recorded with today's date and the newest statement wins. That
beats any rule seeded before today WITHOUT weakening the guard protecting a rule
somebody typed five minutes ago.

Pure: the shipped JSON and the overlay reader. No database.
"""
from datetime import datetime
from pathlib import Path

from app.services import strategy_overlay as so
from app.transformations.engine import apply_rule

_DATA = Path(__file__).resolve().parent.parent / "app" / "data"
OBJECTS = ("Supplier", "Supplier Address", "Supplier Site")


def rule_for(obj):
    d = so.directive_for(obj, "Delivery Method")
    assert d and "rule" in d, f"{obj}: no Delivery Method rule"
    return d


def run(obj, row):
    r = rule_for(obj)["rule"]
    return apply_rule(r["rule_type"], r["config"], "", row=row, ctx={})


# ── The analyst's sentence, on every sheet that carries the column ───────────

def test_a_remittance_email_gives_email():
    for obj in OBJECTS:
        assert run(obj, {"Remittance E-Mail": "ap@acme.com"}) == "EMAIL", obj


def test_a_remittance_fax_with_no_email_gives_fax():
    """MOVED 05-Aug, from EMAIL to FAX. The behaviour changed on purpose.

    This asserted EMAIL because that is what the 04-Aug instruction said, twice,
    and it was implemented as written with the doubt filed as an _open_question
    rather than corrected on somebody's judgement. Subrato answered it on 05-Aug
    and Oracle's own template is why:

        Remittance E-mail (REMIT_ADVICE_EMAIL) — "Value must be provided when
        Delivery Method is EMAIL or EMAILPDF."

    This branch fires precisely when that column is BLANK, so EMAIL here produced
    the one combination Oracle documents as not allowed, on every fax-only row.
    FAX is among the four values it does accept.

    The assertion is not relaxed — it is the same strictness pointed at the value
    that is now correct. See supplier_corrections_05aug.json and
    test_delivery_method_05aug.py.
    """
    for obj in OBJECTS:
        assert run(obj, {"Remittance Fax": "+1 415 555 0100"}) == "FAX", obj


def test_neither_gives_blank():
    """"otherwise blank" — and blank rather than a guessed code, because Delivery
    Method is a value-set column and an invented code fails the load with no
    clue why."""
    for obj in OBJECTS:
        assert run(obj, {"Remittance E-Mail": "", "Remittance Fax": ""}) == "", obj
        assert run(obj, {}) == "", obj


def test_email_is_still_preferred_when_a_row_carries_both():
    for obj in OBJECTS:
        assert run(obj, {"Remittance E-Mail": "ap@acme.com",
                         "Remittance Fax": "+1 415 555 0100"}) == "EMAIL", obj


def test_the_column_spellings_the_extract_might_use_are_all_covered():
    """A branch on a column the extract lacks reads as blank and falls through,
    so naming several costs nothing — and one wrong guess costs the whole rule,
    which is how this column came to ship empty in the first place."""
    for spelling in ("Remittance E-Mail", "Remittance Email", "Remittance E-mail",
                     "remittance_email"):
        assert run("Supplier Site", {spelling: "ap@acme.com"}) == "EMAIL", spelling
    for spelling in ("Remittance Fax", "Remittance fax", "remittance_fax"):
        assert run("Supplier Site", {spelling: "+1 415 555 0100"}) == "FAX", spelling


# ── It has to be the NEWEST statement, or a stale seed shadows it ────────────

def test_the_instruction_carries_the_latest_date():
    """The whole point, and the reason the column shipped blank on 04-Aug: dated
    earlier, `_conversion_rule_wins` lets a rule SEEDED onto the conversion —
    carrying the date it was seeded, not the date it was decided — shadow it.

    MOVED 05-Aug from 04-Aug. Same assertion, one day on: the correction is a
    newly-dated file rather than an edit to 04-Aug, so what the overlay resolves
    to must move with it.
    """
    for obj in OBJECTS:
        assert rule_for(obj)["as_of"] == datetime(2026, 8, 5), obj


def test_it_is_newer_than_every_earlier_delivery_method_statement():
    import json
    for f in ("supplier_strategy_defaults.json", "supplier_corrections_30jul.json",
              "supplier_corrections_04aug.json"):
        doc = json.loads((_DATA / f).read_text(encoding="utf-8"))
        earlier = str(doc.get("_effective_date") or "")
        assert earlier < "2026-08-05", f"{f} is dated {earlier}"


def test_the_new_file_is_actually_read():
    """A rule file nothing loads is the shipped-and-inert shape this codebase
    repeats most. Both dated files stay registered — dropping 04-Aug would not
    change the answer today, and would delete the record of what it superseded."""
    src = (Path(__file__).resolve().parent.parent / "app" / "services"
           / "strategy_overlay.py").read_text(encoding="utf-8")
    assert '"supplier_corrections_04aug.json",' in src
    assert '"supplier_corrections_05aug.json",' in src


def test_it_reaches_every_supplier_sheet_rather_than_the_header_alone():
    """Delivery Method lives on Site and Address too. Filed under Supplier
    without applies_to_all_sheets, the 13-Jul version reached one sheet and the
    other two shipped empty — the same symptom, a different cause."""
    import json
    for f in ("supplier_corrections_04aug.json", "supplier_corrections_05aug.json"):
        doc = json.loads((_DATA / f).read_text(encoding="utf-8"))
        assert all(r.get("applies_to_all_sheets") for r in doc["rules"]), f


def test_the_open_question_is_carried_not_buried():
    """04-Aug emitted EMAIL in both branches because that is what was written,
    twice, and it said so in an _open_question rather than quietly correcting it.
    That question is the reason the 05-Aug answer exists and could be checked
    against Oracle's template, so BOTH ends stay readable: the question where it
    was asked, the answer where it was given."""
    import json
    q = json.loads((_DATA / "supplier_corrections_04aug.json")
                   .read_text(encoding="utf-8")).get("_open_question", "")
    assert "FAX" in q and "EMAIL" in q
    answer = json.loads((_DATA / "supplier_corrections_05aug.json")
                        .read_text(encoding="utf-8"))
    assert "supplier_corrections_04aug.json" in answer.get("_supersedes", "")
    assert "REMIT_ADVICE_EMAIL" in " ".join(answer["_why_a_new_dated_file"])
