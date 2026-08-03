"""Build app/data/customer_mapping_03aug.json from the two 03-Aug documents.

Kept in the repo so the file is reproducible and every entry is traceable to the
sentence that produced it, rather than being a hand-typed blob nobody can audit.
"""
import json
from pathlib import Path

OUT = (Path(__file__).resolve().parents[2] / "backend" / "app" / "data"
       / "customer_mapping_03aug.json")

SHEETS = ["HZ_IMP_PARTIES_T","HZ_IMP_PARTYSITES_T","HZ_IMP_PARTYSITEUSES_T","HZ_IMP_ACCOUNTS_T",
 "HZ_IMP_ACCTSITES_T","HZ_IMP_ACCTSITEUSES_T","HZ_IMP_ACCOUNTRELS","HZ_IMP_ACCTCONTACTS_T",
 "HZ_IMP_CONTACTPTS_T","HZ_IMP_CONTACTROLES","HZ_IMP_CONTACTS_T","HZ_IMP_LOCATIONS_T",
 "HZ_IMP_RELSHIPS_T","HZ_IMP_ROLERESP","HZ_IMP_CLASSIFICS_T","HZ_IMP_PERSONLANG",
 "RA_CUSTOMER_PROFILES_INT_ALL","RA_CUST_PAY_METHOD_INT_ALL","RA_CUSTOMER_BANKS_INT_ALL"]
PROFILES = "RA_CUSTOMER_PROFILES_INT_ALL"

def only(*ex):
    return [s for s in SHEETS if s not in ex]

# Candidate spellings. Every rule column is a LIST, for the reason
# customer_rules_nextpower.json gives: the rules were dictated in prose and the
# extract's real headers are whatever NetSuite exported. A missing column reads
# as blank and the branch falls through, so naming several costs nothing and a
# single wrong guess costs the whole rule.
ENTITY   = ["entityid", "Entity ID", "entity_id", "Customer ID"]
INTERNAL = ["internalid", "addressinternalid", "Internal ID", "address_internal_id", "internal_id"]
LABEL    = ["addresslabel", "address_label", "Address Label", "addresslable"]
ORGNAME  = ["companyname", "Company Name", "Organization Name", "altname"]
FIRST    = ["firstname", "First Name", "Person First Name"]
MIDDLE   = ["middlename", "Middle Name", "Person Middle Name"]
LAST     = ["lastname", "Last Name", "Person Last Name"]

rules: list[dict] = []

def add(**kw):
    """One row. ``sheets`` is set ONLY where the analyst narrowed further than the
    document's own scope — the 19 Customer interface sheets in ``_sheets``, which
    the seeder applies to every row. Two readers use this file and only one of
    them understands sheets: the store resolves per target field per sheet, while
    the write-time overlay is keyed by object and looks a field up by name alone.
    Marking the narrowing per row is what lets the overlay skip exactly the rows
    it cannot honour instead of over-applying them to all 19."""
    kw.setdefault("target_object", "Customer")
    rules.append(kw)


# ── 1. Column mappings — the workbook's GREEN ("Mapped") rows ────────────────
# Green is the workbook's own legend for "Mapped". The other colours are
# Questions to NextPower (216 rows), Duplicate (1) and Not-to-bring, none of
# which is an instruction to map anything, so none is imported. The "DFF" row
# (externalid) is excluded on the same footing as the supplier seeder excludes
# it: a descriptive flexfield is a decision about where a value belongs, not a
# mapping the engine can apply.
GREEN = [
 ("Last Review Date", "custentity_last_credit_review_submitdate", "Customer Expanded"),
 ("Credit Limit", "creditlimit", "Customer Expanded"),
 ("Email Address", "email", "Customer Expanded"),
 ("Person First Name", "firstname", "Customer Expanded"),
 ("Person Middle Name", "middlename", "Customer Expanded"),
 ("Person Last Name", "lastname", "Customer Expanded"),
 ("Language Name", "language", "Customer Expanded"),
 ("Taxpayer Identification Number", "vatregnumber", "Customer Expanded"),
 ("Organization Name", "companyname", "Customer Expanded"),
 ("Account Description", "companyname", "Customer Expanded"),
 ("Phone Number", "phone", "Customer Expanded"),
 ("Address Line 1", "addr1", "Address"),
 ("Address Line 2", "addr2", "Address"),
 ("Address Line 3", "addr3", "Address"),
 ("City", "city", "Address"),
 ("State", "state", "Address"),
 ("Postal Code", "zip", "Address"),
 ("Country", "country", "Address"),
]
for tgt, col, sheet in GREEN:
    add(target_field=tgt, action="derive", source_column=col,
        note=f"Green row of the mapping workbook ({sheet} sheet).")

# Three keys the 29-Jul per-sheet scope already narrowed. Restating them without
# those exclusions would silently WIDEN an instruction the analyst gave in
# writing — the newer statement would reach the very sheets row 13/14 named.
add(target_field="Party Original System Reference", action="derive", source_column="id",
    sheets=only("HZ_IMP_CLASSIFICS_T"), exclude_sheets=["HZ_IMP_CLASSIFICS_T"],
    note="Green row. Keeps the 29-Jul exclusion (CW_Issues 2 row 13): every sheet "
         "EXCEPT the classifications sheet.")
for tgt in ("Account Number", "Customer Account Source System Reference"):
    add(target_field=tgt, action="derive", source_column="entityid",
        sheets=only("RA_CUST_PAY_METHOD_INT_ALL", "RA_CUSTOMER_BANKS_INT_ALL",
                    "HZ_IMP_ACCOUNTRELS"),
        exclude_sheets=["RA_CUST_PAY_METHOD_INT_ALL", "RA_CUSTOMER_BANKS_INT_ALL",
                        "HZ_IMP_ACCOUNTRELS"],
        note="Green row: entityid feeds two targets. Keeps the 29-Jul exclusions "
             "(CW_Issues 2 row 14).")


# ── 2. Transformation rules — customer_mapping.txt ───────────────────────────

# "concatenate: entityid (address sheet) + _ + internalid (address sheet)"
for tgt in ("Party Site Original System Reference", "Location Original System Reference",
            "Party Site Number", "Account Site Source System Reference"):
    kw = dict(target_field=tgt, action="rule", rule_type="CONCAT",
              rule_config={"separator": "_", "columns": [ENTITY, INTERNAL],
                           "require_all": True},
              note="customer_mapping.txt line 1-2: entityid + _ + internalid, both "
                   "from the address sheet. require_all: half a composite key is a "
                   "wrong key, not a partial one — it would be both invalid and "
                   "duplicated across every row missing the other half.")
    if tgt == "Account Site Source System Reference":
        # CW_Issues 2 row 15 named four sheets this key must not reach.
        ex = ["RA_CUSTOMER_BANKS_INT_ALL", "RA_CUST_PAY_METHOD_INT_ALL",
              PROFILES, "HZ_IMP_ACCTCONTACTS_T"]
        kw["sheets"] = only(*ex)
        kw["exclude_sheets"] = ex
        kw["note"] += " Keeps the 29-Jul exclusions (CW_Issues 2 row 15)."
    add(**kw)

# "concatenate values from source column entityid, then _ and then addresslabel"
add(target_field="Party Site Name", action="rule", rule_type="CONCAT",
    rule_config={"separator": "_", "columns": [ENTITY, LABEL]},
    note="customer_mapping.txt line 4. Supersedes CW #20, which named only the "
         "'address_label' spelling.")

# "... + add suffix _B or _S for billing sheet and shipping sheet respectively."
# One sentence, so one entry: the CONCAT builds the key and the chained
# SUFFIX_WHEN chooses the suffix. Split across two entries the store could hold
# only one of them, because a field has at most one live rule.
SUFFIX_BY_SHEET = {
    "rule_type": "SUFFIX_WHEN",
    "config": {
        "branches": [
            {"if_column": "__source_sheet", "op": "contains", "value": "Billing", "suffix": "_B"},
            {"if_column": "__source_sheet", "op": "contains", "value": "Bill", "suffix": "_B"},
            {"if_column": "__source_sheet", "op": "contains", "value": "Shipping", "suffix": "_S"},
            {"if_column": "__source_sheet", "op": "contains", "value": "Ship", "suffix": "_S"},
        ],
        "default_suffix": "", "skip_blank": True, "skip_if_present": True,
    },
}
for tgt in ("Account Site Purpose Source System Reference",
            "Original System Party Site Use Reference"):
    add(target_field=tgt, action="rule", rule_type="CONCAT",
        rule_config={"separator": "_", "columns": [ENTITY, INTERNAL],
                     "require_all": True, "then": [SUFFIX_BY_SHEET]},
        note="customer_mapping.txt lines 6-7: the same entityid_internalid key, "
             "plus _B on a row that came from Customer_Billing_Address and _S on "
             "one from Customer_Shipping_Address. Supersedes CW #19, which chose "
             "the suffix from the Default Billing / Default Shipping FLAGS and "
             "wrote them lower-case; the analyst has since said the SHEET the row "
             "came from decides, and the suffixes are capitals.")

# "If organization name is blank and if person first/last/middle name is NOT
#  BLANK then Party Type = PERSON, else ORGANIZATION"
add(target_field="Party Type", action="rule", rule_type="CASE_WHEN",
    rule_config={"branches": [
        {"all": [{"if_column": ORGNAME, "op": "isblank"},
                 {"if_column": FIRST, "op": "notblank"}], "then": "PERSON"},
        {"all": [{"if_column": ORGNAME, "op": "isblank"},
                 {"if_column": LAST, "op": "notblank"}], "then": "PERSON"},
        {"all": [{"if_column": ORGNAME, "op": "isblank"},
                 {"if_column": MIDDLE, "op": "notblank"}], "then": "PERSON"},
    ], "default": "ORGANIZATION"},
    note="customer_mapping.txt lines 9-10. Supersedes CW #22, which tested only "
         "first/last name and had NO organization-name test at all — so every "
         "company whose contact name happened to be populated came out PERSON.")

# "Party Number: unique sequence - on the basis of entityid.
#  NXT000001 (Organization) / NXT000001_C1 (Person)"
add(target_field="Party Number", action="rule", rule_type="SEQUENCE",
    rule_config={"prefix": "NXT", "width": 6, "start": 1, "preserve_source": False,
                 "key_column": ENTITY,
                 "variant": {"if_column": "Party Type", "op": "eq", "value": "PERSON",
                             "width": 6, "suffix": "_C{n}", "counter": 1}},
    note="customer_mapping.txt lines 12-13. Two changes to CW #23. (a) 'unique "
         "sequence ON THE BASIS OF entityid' — the number is now per entityid, not "
         "per row, so one customer's 5 address rows all carry the SAME party "
         "number and the 18 sheets that reference it agree. Numbered by row index "
         "they would have had five different ones. (b) The document writes "
         "NXT000001 and NXT000001_C1 — both six digits — which settles the open "
         "question left on CW #23 about the variant being five. Reads Party Type, "
         "so it must run after that rule. preserve_source is off: the analyst "
         "asked for a generated key, and entityid is already carried by Account "
         "Number, so keeping a source value here would defeat the sequence.")

# "address line 1 2 3 city ... Part Site Use Type, Purpose should have values as
#  BILL_TO / SHIP_TO based on the sheet from which the values are taken"
SHEET_PURPOSE = {"branches": [
    {"if_column": "__source_sheet", "op": "contains", "value": "Billing", "then": "BILL_TO"},
    {"if_column": "__source_sheet", "op": "contains", "value": "Bill", "then": "BILL_TO"},
    {"if_column": "__source_sheet", "op": "contains", "value": "Shipping", "then": "SHIP_TO"},
    {"if_column": "__source_sheet", "op": "contains", "value": "Ship", "then": "SHIP_TO"},
], "default": ""}
for tgt in ("Party Site Use Type", "Purpose", "Site Use Code"):
    add(target_field=tgt, action="rule", rule_type="CASE_WHEN",
        rule_config=dict(SHEET_PURPOSE),
        note="customer_mapping.txt line 15: BILL_TO when the address came from "
             "Customer_Billing_Address, SHIP_TO when it came from "
             "Customer_Shipping_Address. Supersedes CW #24, which read the Default "
             "Billing / Default Shipping flags — those mark the DEFAULT address, "
             "not the sheet, so a customer's second billing address was neither. "
             "The default is blank rather than a guess: these are coded columns "
             "and an invented code fails the value set with no clue why.")

# "Account Established Date ... map from StartDate, if blank take datecreated"
add(target_field="Account Established Date", action="rule", rule_type="COALESCE",
    rule_config={"columns": ["StartDate", "Start Date", "startdate",
                             "datecreated", "DateCreated", "Date Created"],
                 "default": ""},
    note="customer_mapping.txt line 17, and the workbook says the same thing in "
         "its TRX comment on both the startdate and datecreated rows. Restates "
         "CW #15 unchanged — it is here so the whole instruction carries one date.")

# "Contact Point Type ... EMAIL if Email Address has a value, PHONE if Phone
#  Number has a value"
add(target_field="Contact Point Type", action="rule", rule_type="CASE_WHEN",
    rule_config={"branches": [
        {"if_column": ["Email Address", "Email", "email"], "op": "notblank", "then": "EMAIL"},
        {"if_column": ["Phone Number", "Phone", "phone"], "op": "notblank", "then": "PHONE"},
    ], "default": ""},
    note="customer_mapping.txt line 19. Supersedes CW #16, whose default was a "
         "flat PHONE — that stamped PHONE on rows carrying neither an email nor a "
         "number, i.e. a contact point type for a contact point that does not "
         "exist. Now a row with neither is blank.")

# "Phone Line Type ... MOBILE if the value came from the Phone source column,
#  FAX if it came from the Fax source column"
add(target_field="Phone Line Type", action="rule", rule_type="CASE_WHEN",
    rule_config={"branches": [
        {"if_column": ["phone", "Phone", "Phone Number"], "op": "notblank", "then": "MOBILE"},
        {"if_column": ["fax", "Fax", "Fax Number"], "op": "notblank", "then": "FAX"},
    ], "default": ""},
    note="customer_mapping.txt line 21. Supersedes CW #17, which read a 'Mobile "
         "Phone' column; the analyst has since named the plain 'Phone' column as "
         "the one that means MOBILE, and the workbook maps 'fax' to Phone Line "
         "Type/Number. Phone is tested first, matching the order of the sentence. "
         "SEE THE OPEN QUESTION: the line opens 'can have values as MOBILE or "
         "PHONE' and then assigns MOBILE or FAX. The two conditionals are "
         "unambiguous and are what is implemented; the word PHONE in the opening "
         "clause is not reachable by either of them.")

# Workbook TRX comment on defaultbillingaddress: "T to be kept as Yes and others
# will be flagged as N".
add(target_field="Identifying Address", action="rule", rule_type="MAP_BOOLEAN",
    source_column="defaultbillingaddress",
    rule_config={"true_output": "Y", "false_output": "N", "default": "N"},
    note="Green row plus its TRX comment: 'defaultbilling (Where T to be kept as "
         "Yes and others will be flagged as N)'. NetSuite exports T/F and the "
         "Oracle column is a Y/N flag, so shipping the raw 'T' fails the load. "
         "SEE THE OPEN QUESTION about 'Yes' vs 'Y'.")


# ── 3. Constant defaults ─────────────────────────────────────────────────────
DEFAULTS = [
    ("Role Type", "CONTACT", None),
    ("Relationship Type", "CONTACT", None),
    ("Relationship Code", "CONTACT_OF", None),
    ("Insert Update Indicator", "I", [PROFILES]),
    ("Customer Account Source System", "NETSUITE", None),
    ("Party Original System", "NETSUITE", None),
    ("Account Site Source System", "NETSUITE", None),
    ("Contact Role Original System", "NETSUITE", None),
    ("Account Address Set", "ENTERPRISE SET", None),
    ("Account Address Purpose Set", "ENTERPRISE SET", None),
    ("Account Site Purpose Source System", "NETSUITE", None),
    ("Contact Point Original System", "NETSUITE", None),
    ("Location Original System", "NETSUITE", None),
    ("Party Site Original System", "NETSUITE", None),
    ("Party Site Use Original System", "NETSUITE", None),
    ("Payment Terms", "IMMEDIATE", None),
]
for tgt, val, sheets in DEFAULTS:
    note = "customer_mapping.txt lines 24-39, stated as a default value."
    if sheets:
        note = ("customer_mapping.txt line 27: 'I (default) ONLY for profiles "
                "sheet'. Scoped to that one sheet — Insert Update Indicator "
                "appears on others and the analyst named one.")
    add(target_field=tgt, action="constant", value=val,
        **({"sheets": sheets} if sheets else {}), note=note)


# ── 4. Suppression ───────────────────────────────────────────────────────────
add(target_field="Batch Identifier", action="blank",
    note="customer_mapping.txt line 23: 'Blank (always)'. The same instruction the "
         "analyst gave for the supplier bundle on 30-Jul — a batch identifier the "
         "loader assigns is not ours to invent. Recorded as a suppression rather "
         "than a blank constant so the control-default pass cannot refill it, "
         "which is exactly how supplier Batch ID kept shipping 900001.")


# ── 5. Source columns not to map ─────────────────────────────────────────────
EXCLUDE_SOURCES = [
    {"source_column": "altphone",
     "note": "Workbook colours this row Duplicate, 'Duplicate Of: Customer "
             "Expanded;phone'. Left unmapped rather than mapped somewhere "
             "plausible."},
]

doc = {
  "_label": "NXT Customer Field Mapping + customer_mapping.txt (03-Aug-2026)",
  "_source": (
    "Two documents handed over on 03-Aug-2026: 'NXT Customer Field Mapping 1.xlsx' "
    "(sheet 'Source Files Mapping', 243 rows, of which 26 are coloured Mapped) and "
    "'customer_mapping.txt' (the transformation rules and default values)."),
  "_effective_date": "2026-08-03",
  "_scope": (
    "Client NextPower, source system NetSuite, target object Customer. Every entry "
    "is scoped to the 19 sheets of the Fusion Customer Import interface. That is "
    "not a precedence tier — it is what the document is ABOUT. Without it, this "
    "being the newest statement in the store, 'Payment Terms = IMMEDIATE' would "
    "have become the answer for Supplier Site too, and Address Line 1/2/3, City, "
    "State, Postal Code, Country, Account Number and Email Address would all have "
    "been contested across objects. Measured: 14 of these 53 target fields are "
    "also claimed by the supplier, HCM or catalog documents."),
  "_precedence": (
    "One dated store. These entries carry effective_date 2026-08-03, so they beat "
    "everything said earlier about the same field and lose to anything said later "
    "— including an analyst's own edit in the UI. Nothing here is a special case."),
  "_supersedes": (
    "customer_rules_nextpower.json (CW #16, #17, #19, #20, #22, #23, #24 — see each "
    "rule's note). CW #15 and #18 are unchanged; #15 is restated so the whole "
    "instruction carries one date, #18 is not mentioned by the new document and is "
    "left alone."),
  "_open_questions": [
    "Phone Line Type opens 'can have values as MOBILE or PHONE' and then assigns "
    "MOBILE (from the Phone column) or FAX (from the Fax column). The two "
    "conditionals are implemented exactly as written; PHONE is not reachable. If "
    "the intended pair is MOBILE/PHONE rather than MOBILE/FAX, say so and the "
    "second branch changes to PHONE.",
    "Identifying Address: the workbook says 'T to be kept as Yes and others will be "
    "flagged as N'. Yes and N are not the same vocabulary. Implemented as Y/N, "
    "which is what the Oracle flag column takes.",
    "Party Number is generated for EVERY party (preserve_source is off). If a "
    "customer already carries an Oracle party number that must be kept, that is a "
    "different instruction and this rule would overwrite it.",
    "The BILL_TO / SHIP_TO and _B / _S rules read which SOURCE SHEET a row came "
    "from, matching 'Billing' or 'Shipping' in the uploaded file or sheet name. "
    "Upload the two address extracts as separate sources with those words in their "
    "names, or the rules cannot tell them apart and leave the column blank rather "
    "than guessing.",
    "The workbook leaves 216 of 243 source columns as 'Question to Nextpower' with "
    "Bring to Oracle = No. None of them is imported. Several carry a proposed "
    "Oracle field (category -> Classification, terms -> payment term, parent -> "
    "party relationship, salesrep -> sales person); those become mappings the day "
    "they are answered and coloured Mapped.",
    "externalid is coloured Mapped but its Oracle field reads 'DFF', pending the "
    "flexfield design. Not imported — a DFF is a decision about where a value "
    "belongs, not a mapping the engine can apply.",
  ],
  "_sheets": SHEETS,
  "rules": rules,
  "exclude_source_columns": EXCLUDE_SOURCES,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", OUT, len(rules), "rules")
from collections import Counter
print(Counter(r["action"] for r in rules))
