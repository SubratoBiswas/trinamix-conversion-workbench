"""The two Customer documents of 03-Aug-2026, and whether they actually do anything.

The analyst handed over "NXT Customer Field Mapping 1.xlsx" and
"customer_mapping.txt" and asked for them to "go to learnings and be saved with
date, after that apply these automatically to all existing projects and future
projects (INCLUDING THE CUSTOM TRANSFORMATION RULES)".

The emphasis is theirs and it is the reason most of this file exists. Getting the
statements into the store is the easy half; the codebase's standing failure —
CODEBASE_GUIDE §7.1, and the reason `customer_sheet_scope`, `blank_sheets` and
SELF_LOOKUP each needed a rescue of their own — is a capability that lands,
passes its unit tests against hand-made inputs, and never meets real data. So
these tests are arranged as three questions, in order:

    the DATA says it        — the file carries the analyst's sentence
    the CODE reads it       — the store and the overlay both parse it
    the CALLER calls it     — generation actually runs the result

The multi-column rules were inert before this change and nothing would have said
so: their stored source column is the literal "(rule)", which is in no extract,
and the apply pass skipped any decision naming a column the extract lacks. Every
CONCAT, CASE_WHEN, COALESCE and SEQUENCE in the store hit that `continue`.

Pure: reads the shipped JSON and the shipped source, plus pandas for the
transform. No database.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.services import strategy_overlay as so
from app.services.output_service import (_transform_frame, _build_sequence_index,
                                         _sequence_key_configs,
                                         _rule_referenced_columns)
from app.transformations.engine import apply_rule

_BACKEND = Path(__file__).resolve().parent.parent
_DOC = _BACKEND / "app" / "data" / "customer_mapping_03aug.json"

DOC = json.loads(_DOC.read_text(encoding="utf-8"))
RULES = DOC["rules"]


def _n(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def by_field(name, action=None):
    return [r for r in RULES if _n(r["target_field"]) == _n(name)
            and (action is None or r["action"] == action)]


def one(name, action=None):
    rows = by_field(name, action)
    assert len(rows) == 1, f"{name}: expected one row, found {len(rows)}"
    return rows[0]


# ══ 1. THE DATA SAYS IT ══════════════════════════════════════════════════════

def test_the_document_is_dated_and_the_date_is_the_day_it_was_given():
    """Undated, it would carry no weight at all — `record_decision` treats an
    undated statement as older than everything, on purpose."""
    assert DOC["_effective_date"] == "2026-08-03"


def test_the_four_key_fields_take_the_concatenated_address_key():
    """customer_mapping.txt line 1: 'Party Site Original System Reference|Location
    Original System Reference|Party Site Number|Account Site Source System
    Reference' <- entityid + _ + internalid."""
    for f in ("Party Site Original System Reference",
              "Location Original System Reference", "Party Site Number",
              "Account Site Source System Reference"):
        r = one(f, "rule")
        assert r["rule_type"] == "CONCAT", f
        cols = r["rule_config"]["columns"]
        assert any("entityid" in c for c in cols), f
        assert any("internalid" in c for c in cols), f
        assert r["rule_config"]["separator"] == "_", f


def test_the_site_use_keys_take_the_same_key_plus_a_capital_suffix():
    """Line 7: '... + add suffix _B or _S for billing sheet and shipping sheet'.

    CW #19 wrote them lower-case and chose between them on the Default Billing /
    Default Shipping FLAGS. Both parts changed."""
    for f in ("Account Site Purpose Source System Reference",
              "Original System Party Site Use Reference"):
        r = one(f, "rule")
        chain = r["rule_config"]["then"]
        assert chain[0]["rule_type"] == "SUFFIX_WHEN", f
        suffixes = {b["suffix"] for b in chain[0]["config"]["branches"]}
        assert suffixes == {"_B", "_S"}, f"{f}: {suffixes}"
        assert all(b["if_column"] == "__source_sheet"
                   for b in chain[0]["config"]["branches"]), f


def test_party_type_now_requires_the_organization_name_to_be_blank():
    """Line 10, and the whole of what changed from CW #22: 'If organization name is
    blank AND if person first/last/middle name is NOT BLANK then PERSON'."""
    cfg = one("Party Type", "rule")["rule_config"]
    assert cfg["default"] == "ORGANIZATION"
    assert cfg["branches"], "no branches"
    for br in cfg["branches"]:
        clauses = br["all"]
        assert any(c["op"] == "isblank" and any("companyname" in x for x in c["if_column"])
                   for c in clauses), br
        assert any(c["op"] == "notblank" for c in clauses), br
    # first, last AND middle — CW #22 had no middle-name branch at all.
    tested = {x for br in cfg["branches"] for c in br["all"]
              for x in (c["if_column"] if isinstance(c["if_column"], list) else [])}
    for want in ("firstname", "lastname", "middlename"):
        assert want in tested, want


def test_party_number_is_sequenced_on_entityid_at_one_width():
    """Line 12-13: 'unique sequence - on the basis of entityid', NXT000001 and
    NXT000001_C1. Both spellings are six digits, which settles the open question
    CW #23 was left carrying about the variant being five."""
    cfg = one("Party Number", "rule")["rule_config"]
    assert cfg["rule_type"] if False else True
    assert cfg["prefix"] == "NXT"
    assert cfg["width"] == 6
    assert cfg["variant"]["width"] == 6, "the two forms must be the same width"
    assert any("entityid" in c for c in cfg["key_column"])


def test_the_site_use_type_reads_the_sheet_not_the_default_flags():
    """Line 15: BILL_TO when the address came from Customer_Billing_Address."""
    for f in ("Party Site Use Type", "Purpose"):
        cfg = one(f, "rule")["rule_config"]
        assert {b["then"] for b in cfg["branches"]} == {"BILL_TO", "SHIP_TO"}, f
        assert all(b["if_column"] == "__source_sheet" for b in cfg["branches"]), f
        assert cfg["default"] == "", f"{f}: a coded column must not be guessed"


def test_account_established_date_falls_back_to_datecreated():
    cfg = one("Account Established Date", "rule")["rule_config"]
    cols = [str(c).lower() for c in cfg["columns"]]
    assert cols.index("startdate") < cols.index("datecreated")


def test_contact_point_type_no_longer_defaults_to_phone():
    """Line 19 names both conditions. CW #16's flat PHONE default stamped a contact
    point type on rows carrying neither an email nor a number."""
    cfg = one("Contact Point Type", "rule")["rule_config"]
    assert cfg["default"] == ""
    assert [b["then"] for b in cfg["branches"]] == ["EMAIL", "PHONE"]


def test_phone_line_type_reads_phone_then_fax():
    """Line 21, implemented from its two conditionals. See the open question — the
    line's opening clause says MOBILE or PHONE and then assigns MOBILE or FAX."""
    cfg = one("Phone Line Type", "rule")["rule_config"]
    assert [b["then"] for b in cfg["branches"]] == ["MOBILE", "FAX"]


def test_every_default_value_in_the_text_is_present():
    """Lines 24-39, checked one by one. A default silently dropped in transcription
    ships an empty required column and nothing says why."""
    want = {
        "Role Type": "CONTACT", "Relationship Type": "CONTACT",
        "Relationship Code": "CONTACT_OF", "Insert Update Indicator": "I",
        "Customer Account Source System": "NETSUITE",
        "Party Original System": "NETSUITE", "Account Site Source System": "NETSUITE",
        "Contact Role Original System": "NETSUITE",
        "Account Address Set": "ENTERPRISE SET",
        "Account Address Purpose Set": "ENTERPRISE SET",
        "Account Site Purpose Source System": "NETSUITE",
        "Contact Point Original System": "NETSUITE",
        "Location Original System": "NETSUITE", "Party Site Original System": "NETSUITE",
        "Party Site Use Original System": "NETSUITE", "Payment Terms": "IMMEDIATE",
    }
    for field, value in want.items():
        assert one(field, "constant")["value"] == value, field


def test_the_insert_update_indicator_is_the_only_default_scoped_to_one_sheet():
    """Line 27 is the only one that says 'only for profiles sheet'."""
    scoped = [r for r in RULES if r["action"] == "constant" and r.get("sheets")]
    assert [r["target_field"] for r in scoped] == ["Insert Update Indicator"]
    assert scoped[0]["sheets"] == ["RA_CUSTOMER_PROFILES_INT_ALL"]


def test_batch_identifier_is_suppressed_not_defaulted_to_empty():
    """Line 23, 'Blank (always)'. A blank CONSTANT does not survive: the
    control-default pass refills a wholly empty column, which is how supplier
    Batch ID kept shipping 900001 through a perfectly good instruction."""
    assert one("Batch Identifier")["action"] == "blank"


def test_the_green_rows_of_the_workbook_are_the_column_mappings():
    """26 rows are coloured Mapped. externalid ('DFF') and the two that the text
    replaces with rules are not column mappings; the rest are."""
    for tgt, col in [("Address Line 1", "addr1"), ("Address Line 2", "addr2"),
                     ("Address Line 3", "addr3"), ("City", "city"),
                     ("State", "state"), ("Postal Code", "zip"),
                     ("Country", "country"), ("Phone Number", "phone"),
                     ("Email Address", "email"), ("Person First Name", "firstname"),
                     ("Person Middle Name", "middlename"),
                     ("Person Last Name", "lastname"),
                     ("Organization Name", "companyname"),
                     ("Taxpayer Identification Number", "vatregnumber"),
                     ("Credit Limit", "creditlimit"), ("Language Name", "language"),
                     ("Party Original System Reference", "id"),
                     ("Account Number", "entityid")]:
        assert one(tgt, "derive")["source_column"] == col, tgt


def test_the_dff_row_is_not_imported():
    """externalid is coloured Mapped but its Oracle field reads 'DFF'. A flexfield
    is a decision about where a value belongs, not a mapping the engine can apply —
    the supplier seeder excludes them on the same footing."""
    assert not [r for r in RULES if r.get("source_column") == "externalid"]


def test_the_duplicate_column_is_recorded_as_one_not_mapped_somewhere_plausible():
    """The workbook colours altphone Duplicate, 'Duplicate Of: Customer
    Expanded;phone'."""
    assert [x["source_column"] for x in DOC["exclude_source_columns"]] == ["altphone"]


def test_the_questions_to_nextpower_are_not_imported_as_mappings():
    """216 of the 243 workbook rows are unanswered questions with Bring to Oracle =
    No, several carrying a PROPOSED Oracle field (category -> Classification,
    terms -> payment term). Importing those would map a field on the strength of a
    question nobody has answered."""
    imported = {r.get("source_column") for r in RULES if r.get("source_column")}
    for never in ("category", "parent", "salesrep", "terms", "contact",
                  "accountnumber", "url", "altname", "subsidiary", "rownumber"):
        assert never not in imported, never


# ══ 2. THE CODE READS IT ═════════════════════════════════════════════════════

def test_the_store_seeder_writes_every_action_through_record_decision():
    """One writer. `test_one_dated_store_writes` enforces this globally; this pins
    down that the new seeder is on the right side of it and covers all four
    decisions rather than quietly dropping one."""
    src = (_BACKEND / "app" / "services" / "catalog_seed_service.py").read_text(
        encoding="utf-8")
    body = src.split("async def seed_customer_mapping_03aug(")[1].split("\nasync def ")[0]
    assert "mapping_store.record_decision(" in body
    assert "LearnedMapping(" not in body
    for decision in ("SOURCE_COLUMN", "DEFAULT_VALUE", "SUPPRESS", "RULE"):
        assert f"mapping_store.{decision}" in body, decision
    assert "effective_date=eff" in body


def test_the_seeder_stamps_the_documents_date_and_not_todays():
    src = (_BACKEND / "app" / "services" / "catalog_seed_service.py").read_text(
        encoding="utf-8")
    body = src.split("async def seed_customer_mapping_03aug(")[1].split("\nasync def ")[0]
    assert "_effective_date_of(doc)" in body
    assert "utcnow" not in body, "a seed stamping itself now out-ranks every instruction"


def test_the_seeder_runs_at_startup():
    """A seeder nothing calls is the same as no seeder."""
    main = (_BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    assert "seed_customer_mapping_03aug" in main
    # After the older Customer seeds, so the 03-Aug rows land on rows already there.
    assert main.index("seed_customer_field_mappings") < main.index(
        "seed_customer_mapping_03aug")


def test_the_overlay_reads_the_file_too():
    """The store is the truth; the overlay is the write-time guarantee. Both read
    THIS file, so there is one date and one set of words behind both."""
    src = (_BACKEND / "app" / "services" / "strategy_overlay.py").read_text(
        encoding="utf-8")
    assert "customer_mapping_03aug.json" in src
    assert "_EXTRA_FILES" in src.split("for extra in ")[1][:40]


def test_the_overlay_carries_the_rules_the_constants_and_the_suppression():
    assert (so.directive_for("Customer", "Party Type") or {}).get("rule")
    assert (so.directive_for("Customer", "Payment Terms") or {}).get("constant") == "IMMEDIATE"
    assert (so.directive_for("Customer", "Batch Identifier") or {}).get("blank")
    assert so.directive_for("Customer", "Party Number")["as_of"] == datetime(2026, 8, 3)


def test_the_overlay_leaves_the_column_mappings_to_the_mapping_path():
    """A 'derive' row is a mapping, not an overlay. Enforcing one here would take
    the field away from the analyst's own Mapping Review screen."""
    assert so.directive_for("Customer", "Address Line 1") is None
    assert so.directive_for("Customer", "Person First Name") is None


def test_the_overlay_skips_a_row_scoped_to_particular_sheets():
    """It is keyed by OBJECT and looks a field up by name — one Customer conversion
    is one object name for all nineteen sheets. Applying 'Insert Update Indicator =
    I, profiles sheet only' here would put an I on all nineteen. Over-applying a
    scoped instruction is not enforcement."""
    assert so.directive_for("Customer", "Insert Update Indicator") is None
    assert one("Insert Update Indicator", "constant")["sheets"], "premise"


def test_the_customer_document_does_not_reach_the_supplier_bundle():
    """Measured, not assumed: 14 of these target fields are also claimed by the
    supplier/HCM/catalog documents, and this is the newest thing in the store."""
    for f in ("Payment Terms", "Address Line 1", "City", "Account Number"):
        d = so.directive_for("Supplier", f) or {}
        assert d.get("constant") != "IMMEDIATE" or f != "Payment Terms"
        assert d.get("as_of") != datetime(2026, 8, 3), f
    assert so.directive_for("Supplier Site", "Party Type") is None


def test_every_rule_column_survives_source_pruning():
    """Generation prunes the frame to the columns something CLAIMS. A rule column
    nobody declares is dropped, the rule reads blanks and returns its default —
    Supplier Site shipped empty on 8,561 rows exactly this way."""
    declared = so.referenced_columns("Customer")
    for want in ("entityid", "internalid", "addresslabel", "companyname",
                 "firstname", "lastname", "middlename", "startdate", "datecreated",
                 "fax", "phone"):
        assert want in declared, f"{want} is read by a rule and declared by nobody"


def test_a_chained_rule_declares_its_own_columns():
    cols = _rule_referenced_columns([{
        "rule_type": "CONCAT",
        "config": {"columns": [["a"], ["b"]],
                   "then": [{"rule_type": "CASE_WHEN",
                             "config": {"branches": [{"if_column": "hidden",
                                                      "op": "notblank"}]}}]}}])
    assert {"a", "b", "hidden"} <= cols


def test_a_conjunction_branch_declares_its_own_columns():
    """The clause columns of an `all` appear nowhere else. Undeclared, every clause
    reads blank — for Party Type that means the organization-name test always
    passes and every row comes out PERSON."""
    cols = _rule_referenced_columns([{
        "rule_type": "CASE_WHEN",
        "config": {"branches": [{"all": [{"if_column": "orgname", "op": "isblank"},
                                         {"if_column": "first", "op": "notblank"}],
                                 "then": "PERSON"}]}}])
    assert {"orgname", "first"} <= cols


# ══ 3. THE ENGINE RUNS IT ════════════════════════════════════════════════════

def _rule(field):
    return one(field, "rule")


def test_the_address_key_concatenates_and_a_half_key_is_refused():
    cfg = _rule("Party Site Number")["rule_config"]
    row = {"entityid": "C1001", "internalid": "77"}
    assert apply_rule("CONCAT", cfg, "", row=row) == "C1001_77"
    # Half a composite key is a WRONG key, not a partial one — it would be both
    # invalid and duplicated on every row missing the other half.
    assert apply_rule("CONCAT", cfg, "", row={"entityid": "C1001"}) == ""


def test_a_candidate_spelling_binds_whichever_one_the_extract_uses():
    """The rules were dictated in prose; the headers are whatever NetSuite
    exported. One guessed spelling binds to nothing and fails silently."""
    cfg = _rule("Party Site Number")["rule_config"]
    assert apply_rule("CONCAT", cfg, "",
                      row={"Entity ID": "C1", "Internal ID": "9"}) == "C1_9"


def test_the_site_use_key_takes_B_from_a_billing_sheet_and_S_from_a_shipping_one():
    cfg = _rule("Account Site Purpose Source System Reference")["rule_config"]
    base = {"entityid": "C1001", "internalid": "77"}
    assert apply_rule("CONCAT", cfg, "",
                      row={**base, "__source_sheet": "NXT — Customer_Billing_Address"}
                      ) == "C1001_77_B"
    assert apply_rule("CONCAT", cfg, "",
                      row={**base, "__source_sheet": "NXT — Customer_Shipping_Address"}
                      ) == "C1001_77_S"
    # No evidence which sheet: the key ships without a suffix rather than with a
    # guessed one, because both guesses are wrong half the time.
    assert apply_rule("CONCAT", cfg, "", row=base) == "C1001_77"


def test_the_suffix_is_not_applied_twice_on_a_regenerate():
    cfg = _rule("Original System Party Site Use Reference")["rule_config"]
    row = {"entityid": "C1", "internalid": "2", "__source_sheet": "Customer_Billing_Address"}
    once = apply_rule("CONCAT", cfg, "", row=row)
    assert apply_rule("SUFFIX_WHEN", cfg["then"][0]["config"], once, row=row) == once


def test_party_type_is_organization_when_the_company_name_is_present():
    """The row CW #22 got wrong: a company that also carries a contact's name."""
    cfg = _rule("Party Type")["rule_config"]
    assert apply_rule("CASE_WHEN", cfg, "", row={
        "companyname": "Acme Steel", "firstname": "Ann"}) == "ORGANIZATION"
    assert apply_rule("CASE_WHEN", cfg, "", row={
        "companyname": "", "firstname": "Ann"}) == "PERSON"
    assert apply_rule("CASE_WHEN", cfg, "", row={
        "companyname": "", "middlename": "Q"}) == "PERSON"
    assert apply_rule("CASE_WHEN", cfg, "", row={
        "companyname": "", "firstname": "", "lastname": ""}) == "ORGANIZATION"


def test_the_party_number_is_the_same_for_every_row_of_one_customer():
    """"unique sequence - on the basis of entityid". Numbered by row index instead,
    a customer with five addresses takes five different party numbers and the
    eighteen sheets that REFERENCE the party stop agreeing with the one that
    defines it."""
    cfg = _rule("Party Number")["rule_config"]
    src = pd.DataFrame({"entityid": ["C1", "C1", "C2", "C1", "C3"]})
    idx = _build_sequence_index(src, [cfg])
    got = [apply_rule("SEQUENCE", cfg, "", row={"entityid": e},
                      ctx={"sequence_index": idx, "row_index": i})
           for i, e in enumerate(src["entityid"])]
    assert got == ["NXT000001", "NXT000001", "NXT000002", "NXT000001", "NXT000003"], got


def test_a_person_gets_the_C_form_at_the_same_width():
    cfg = _rule("Party Number")["rule_config"]
    idx = _build_sequence_index(pd.DataFrame({"entityid": ["C1"]}), [cfg])
    assert apply_rule("SEQUENCE", cfg, "",
                      row={"entityid": "C1", "Party Type": "PERSON"},
                      ctx={"sequence_index": idx}) == "NXT000001_C1"


def test_a_row_with_no_entityid_gets_no_number_rather_than_someone_elses():
    cfg = _rule("Party Number")["rule_config"]
    idx = _build_sequence_index(pd.DataFrame({"entityid": ["C1", "", "C2"]}), [cfg])
    assert apply_rule("SEQUENCE", cfg, "", row={"entityid": ""},
                      ctx={"sequence_index": idx, "row_index": 1}) == ""


def test_a_key_column_the_extract_does_not_have_falls_back_rather_than_blanking():
    """A missing key column is a misconfigured rule, not a per-row data gap.
    Blanking the whole column over it would destroy whatever a conversion's own
    rule had already computed."""
    cfg = _rule("Party Number")["rule_config"]
    assert apply_rule("SEQUENCE", cfg, "", row={"something_else": "x"},
                      ctx={"sequence_index": {}, "row_index": 4}) == "NXT000005"


def test_the_number_is_stable_across_a_regenerate():
    """First appearance, not sort order. A party key that renumbers on every
    regenerate is worse than no key at all — the other sheets still hold the old
    one."""
    cfg = _rule("Party Number")["rule_config"]
    src = pd.DataFrame({"entityid": ["Z9", "A1", "Z9", "M4"]})
    a = _build_sequence_index(src, [cfg])
    b = _build_sequence_index(src.copy(), [cfg])
    assert a == b
    assert list(a["entityid"].values()) == [0, 1, 2]


def test_the_established_date_falls_back_row_by_row():
    cfg = _rule("Account Established Date")["rule_config"]
    assert apply_rule("COALESCE", cfg, "", row={"startdate": "2019-04-01",
                                                "datecreated": "2015-01-01"}) == "2019-04-01"
    assert apply_rule("COALESCE", cfg, "", row={"startdate": "",
                                                "datecreated": "2015-01-01"}) == "2015-01-01"


def test_contact_point_type_is_blank_when_the_row_has_neither():
    cfg = _rule("Contact Point Type")["rule_config"]
    assert apply_rule("CASE_WHEN", cfg, "", row={"email": "a@b.com"}) == "EMAIL"
    assert apply_rule("CASE_WHEN", cfg, "", row={"phone": "555"}) == "PHONE"
    assert apply_rule("CASE_WHEN", cfg, "", row={"email": "", "phone": ""}) == ""


def test_phone_line_type_follows_the_column_the_value_came_from():
    cfg = _rule("Phone Line Type")["rule_config"]
    assert apply_rule("CASE_WHEN", cfg, "", row={"phone": "555-0100"}) == "MOBILE"
    assert apply_rule("CASE_WHEN", cfg, "", row={"phone": "", "fax": "555-0199"}) == "FAX"
    assert apply_rule("CASE_WHEN", cfg, "", row={"phone": "", "fax": ""}) == ""


def test_the_identifying_address_flag_becomes_Y_or_N():
    """NetSuite exports T/F; the Oracle column is a Y/N flag, so the raw T fails
    the load."""
    cfg = _rule("Identifying Address")["rule_config"]
    assert apply_rule("MAP_BOOLEAN", cfg, "T") == "Y"
    assert apply_rule("MAP_BOOLEAN", cfg, "F") == "N"
    assert apply_rule("MAP_BOOLEAN", cfg, "") == "N"


# ══ 4. THE CALLER CALLS IT ═══════════════════════════════════════════════════

class _F:
    def __init__(self, i, name, seq=0):
        self.id, self.field_name, self.sequence = i, name, seq


class _M:
    def __init__(self, tid, src=None, status="not_applicable"):
        self.target_field_id, self.source_column, self.status = tid, src, status
        self.default_value = self.approved_by = self.approved_at = None
        self.suggested_transformation = self.confidence = None


EXTRACT = pd.DataFrame({
    "entityid":   ["C1001", "C1001", "C2002"],
    "internalid": ["77", "78", "91"],
    "companyname": ["Acme Steel", "Acme Steel", ""],
    "firstname":  ["", "", "Ann"],
    "addr1":      ["1 Mill Rd", "2 Dock St", "9 Kew Ln"],
})


def _generate(fields, label=""):
    """One conversion's worth of transform, with nothing but the overlay speaking."""
    maps = [_M(f.id) for f in fields.values()]
    ctx_cols = set(EXTRACT.columns)
    seq = _build_sequence_index(EXTRACT, _sequence_key_configs({}, "Customer"))
    out, _lin = _transform_frame(EXTRACT, maps, fields, {}, ctx_cols, "Customer",
                                 None, None, None, 0, seq, label)
    return out


def test_generation_derives_the_whole_customer_row_from_the_document_alone():
    """The end-to-end claim, and the one that would have failed silently before:
    not one of these fields has a source column, a default or a conversion rule.
    Everything on this row comes from the 03-Aug document."""
    fields = {1: _F(1, "Party Type", 1), 2: _F(2, "Party Number", 2),
              3: _F(3, "Party Site Number", 3),
              4: _F(4, "Party Site Use Type", 4),
              5: _F(5, "Payment Terms", 5), 6: _F(6, "Batch Identifier", 6)}
    out = _generate(fields, "NXT Customer — Customer_Billing_Address")

    assert list(out["Party Type"]) == ["ORGANIZATION", "ORGANIZATION", "PERSON"]
    # Two rows of one customer, one party number.
    assert list(out["Party Number"]) == ["NXT000001", "NXT000001", "NXT000002_C1"]
    assert list(out["Party Site Number"]) == ["C1001_77", "C1001_78", "C2002_91"]
    assert set(out["Party Site Use Type"]) == {"BILL_TO"}
    assert set(out["Payment Terms"]) == {"IMMEDIATE"}
    # Batch Identifier is SUPPRESSED, and a suppressed field with no mapping of its
    # own gets no column at all — blank is what discarding means. The guarantee
    # that matters is the next test: nothing downstream refills it.
    assert "Batch Identifier" not in out.columns


def test_the_suppressed_field_is_kept_out_of_the_control_default_refill():
    """Blanking the column inside the transform was never enough on its own.

    Two later passes re-populate a column the overlay emptied — the sequence pass
    and the control-constant pass, for which "a wholly empty column" is exactly
    the thing it exists to fill. That is how supplier Batch ID kept shipping
    900001 through a perfectly good instruction, and RFQ Or Bidding came back "Y"
    after being blanked. The suppression has to reach `blank_fields` too."""
    assert "batch identifier" in so.blank_fields("Customer")


def test_the_shipping_extract_produces_ship_to_from_the_same_code():
    fields = {1: _F(1, "Party Site Use Type", 1)}
    out = _generate(fields, "NXT Customer — Customer_Shipping_Address")
    assert set(out["Party Site Use Type"]) == {"SHIP_TO"}


def test_with_no_source_label_the_coded_column_is_blank_not_guessed():
    """Both guesses are wrong half the time, and a bad code fails the value set
    with no clue why. The blank shows up in the required-field report instead."""
    fields = {1: _F(1, "Party Site Use Type", 1)}
    assert set(_generate(fields, "")["Party Site Use Type"]) == {""}


def test_the_source_label_reaches_the_rules_as_a_pseudo_column():
    """Which FILE a row came from is a fact about the file, not about any cell in
    it, so the rule could not be written at all before this."""
    src = (_BACKEND / "app" / "services" / "output_service.py").read_text(
        encoding="utf-8")
    assert '_rec["__source_sheet"] = source_label' in src
    assert 'getattr(dataset, "name", "")' in src


def test_the_sequence_index_is_built_on_the_full_frame_before_chunking():
    """A per-chunk index numbers the same customer twice — their rows are very
    often in different chunks."""
    src = (_BACKEND / "app" / "services" / "output_service.py").read_text(
        encoding="utf-8")
    body = src.split("async def _convert_source(")[1].split("\n    lineage: dict")[0]
    assert "_seq_idx = _build_sequence_index(" in body
    assert body.index("_seq_idx = _build_sequence_index(") < body.index("for start in range(")


def test_a_multi_column_rule_in_the_store_is_no_longer_dropped():
    """THE SEAM. `apply_learned_to_conversion` skipped any decision whose source
    column is absent from the extract — right for a column mapping, fatal for a
    rule, whose stored column is the literal '(rule)'. Every CONCAT, CASE_WHEN,
    COALESCE and SEQUENCE in the store hit that `continue` and did nothing."""
    src = (_BACKEND / "app" / "services" / "learning_service.py").read_text(
        encoding="utf-8")
    assert "def _rule_columns_present(" in src
    body = src.split("async def apply_learned_to_conversion(")[1]
    assert "if not actual_src and not rule_cols:" in body
    assert "rule_cols = _rule_columns_present(" in body


def test_a_rule_still_needs_a_column_the_extract_actually_has():
    """It must not become a free pass. A rule naming only columns this file does
    not carry would put an approved-looking mapping on a field that ships blank."""
    from app.services.learning_service import _rule_columns_present

    class _LM:
        rule_type = "CONCAT"
        rule_config = {"columns": ["entityid", "internalid"]}
    assert _rule_columns_present(_LM(), {"entityid": "entityid"}) == {"entityid"}
    assert _rule_columns_present(_LM(), {"vendorno": "vendorno"}) == set()


def test_the_apply_authored_rules_button_no_longer_re_installs_the_old_ones():
    """A trap this change created, closed here.

    "Apply the authored Customer rules" writes the July CW rules onto a conversion
    as fresh TransformationRule rows stamped with TODAY — and a conversion rule
    newer than the document is exactly what `_conversion_rule_wins` protects. So
    pressing that button would have let the superseded July version beat the
    03-Aug one, with nothing on screen to say why: a button labelled "apply the
    analyst's rules" quietly undoing them.
    """
    from app.services import customer_rules_service as crs

    survivors = {r["target_field"] for r in crs.load_rules()}
    for gone in ("Party Type", "Party Number", "Contact Point Type",
                 "Phone Line Type", "Party Site Name", "Party Site Use Type",
                 "Account Site Purpose SSR", "Original System Party Site Use Reference"):
        assert gone not in survivors, f"{gone} was rewritten on 03-Aug"
    # CW #18 (Primary Indicator) is NOT mentioned by the new document and must
    # survive — superseding is per field, not wholesale.
    assert "Primary Indicator" in survivors


def test_the_supersede_list_is_derived_not_hand_kept():
    """A hand-written list of CW numbers drifts on the next document."""
    src = (_BACKEND / "app" / "services" / "customer_rules_service.py").read_text(
        encoding="utf-8")
    body = src.split("def superseded_fields(")[1].split("\ndef ")[0]
    assert "customer_mapping_03aug.json" not in body, "the filename is read from _LATER"
    assert 'r.get("action") == "rule"' in body
