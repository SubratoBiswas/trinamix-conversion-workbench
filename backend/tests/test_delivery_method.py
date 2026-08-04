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


def test_a_remittance_fax_with_no_email_also_gives_email():
    """As stated, twice: "if remittance fax is not blank but remittance-email is
    blank then the delivery method is EMAIL". See _open_question in the data file
    — the 28-Jul version said FAX here."""
    for obj in OBJECTS:
        assert run(obj, {"Remittance Fax": "+1 415 555 0100"}) == "EMAIL", obj


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
    for spelling in ("Remittance Fax", "remittance_fax"):
        assert run("Supplier Site", {spelling: "+1 415 555 0100"}) == "EMAIL", spelling


# ── It has to be the NEWEST statement, or a stale seed shadows it ────────────

def test_the_instruction_carries_todays_date():
    """The whole point. Dated earlier, `_conversion_rule_wins` lets a rule seeded
    onto the conversion — carrying the date it was SEEDED — shadow it, and the
    column ships blank exactly as reported."""
    for obj in OBJECTS:
        assert rule_for(obj)["as_of"] == datetime(2026, 8, 4), obj


def test_it_is_newer_than_every_earlier_delivery_method_statement():
    import json
    for f in ("supplier_strategy_defaults.json", "supplier_corrections_30jul.json"):
        doc = json.loads((_DATA / f).read_text(encoding="utf-8"))
        earlier = str(doc.get("_effective_date") or "")
        assert earlier < "2026-08-04", f"{f} is dated {earlier}"


def test_the_new_file_is_actually_read():
    """A rule file nothing loads is the shipped-and-inert shape this codebase
    repeats most."""
    src = (Path(__file__).resolve().parent.parent / "app" / "services"
           / "strategy_overlay.py").read_text(encoding="utf-8")
    assert '"supplier_corrections_04aug.json",' in src


def test_it_reaches_every_supplier_sheet_rather_than_the_header_alone():
    """Delivery Method lives on Site and Address too. Filed under Supplier
    without applies_to_all_sheets, the 13-Jul version reached one sheet and the
    other two shipped empty — the same symptom, a different cause."""
    import json
    doc = json.loads((_DATA / "supplier_corrections_04aug.json").read_text(encoding="utf-8"))
    assert all(r.get("applies_to_all_sheets") for r in doc["rules"])


def test_the_open_question_is_carried_not_buried():
    """Both branches emit EMAIL because that is what was written, twice. If the
    fax branch was meant to stay FAX, that has to be visible without reading a
    diff."""
    import json
    doc = json.loads((_DATA / "supplier_corrections_04aug.json").read_text(encoding="utf-8"))
    q = doc.get("_open_question", "")
    assert "FAX" in q and "EMAIL" in q
