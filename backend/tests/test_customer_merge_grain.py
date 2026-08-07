"""Multi-source Customer merge — grain-aware sheet rows + entityid linkage.

The bug this covers: a Customer load of four files (customer master, shipping and
billing addresses, contacts) was stacked into one frame and copied onto every one of
the 15 interface sheets, so each sheet carried all 31,511 rows — the sum of the four
files — with ~26k blank/duplicate party rows and child rows pointing at parties by
their own record id. These tests pin the fix: each sheet gets only its own grain's
rows, the party/account sheets collapse to one row per customer, and every row's
Party Original System Reference is retargeted to the customer key (entityid).
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import customer_merge as cm            # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# ── sheet_grain: which grain each interface sheet belongs to ────────────────
def test_sheet_grain_classification():
    party = ["HZ_IMP_PARTIES_T", "HZ_IMP_ACCOUNTS_T", "RA_CUSTOMER_PROFILES_INT_ALL",
             "HZ_IMP_RELSHIPS_T"]
    site = ["HZ_IMP_PARTYSITES_T", "HZ_IMP_PARTYSITEUSES_T", "HZ_IMP_ACCTSITES_T",
            "HZ_IMP_ACCTSITEUSES_T", "HZ_IMP_LOCATIONS_T"]
    contact = ["HZ_IMP_CONTACTS_T", "HZ_IMP_CONTACTPTS_T", "HZ_IMP_CONTACTROLES",
               "HZ_IMP_ACCTCONTACTS_T", "HZ_IMP_ROLERESP", "HZ_IMP_PERSONLANG"]
    for s in party:
        check(f"{s} -> party", cm.sheet_grain(s) == cm.PARTY, cm.sheet_grain(s))
    for s in site:
        check(f"{s} -> site", cm.sheet_grain(s) == cm.SITE, cm.sheet_grain(s))
    for s in contact:
        check(f"{s} -> contact", cm.sheet_grain(s) == cm.CONTACT, cm.sheet_grain(s))


def test_acctcontacts_is_contact_not_site_or_account():
    # "AcctContacts" contains both "acct" and "contact"; contact must win, or account
    # contacts would be filed with the account (party) grain.
    check("HZ_IMP_ACCTCONTACTS_T -> contact",
          cm.sheet_grain("HZ_IMP_ACCTCONTACTS_T") == cm.CONTACT)
    # "AcctSites" contains both "acct" and "site"; site must win.
    check("HZ_IMP_ACCTSITES_T -> site",
          cm.sheet_grain("HZ_IMP_ACCTSITES_T") == cm.SITE)


def test_unknown_sheet_is_none():
    check("unknown sheet -> None (safe passthrough)",
          cm.sheet_grain("SOME_OTHER_SHEET") is None)
    check("empty sheet name -> None", cm.sheet_grain("") is None)


# ── classify_source_columns: grain from the RAW source columns ──────────────
# The real fix — the converted frame carries the glue's reference columns and scored
# every source the same; the source columns do not.
def test_classify_source_columns_by_raw_names():
    master = ["internalid", "entityid", "companyname", "creditlimit", "subsidiary"]
    shipping = ["internalid", "entityid", "addressee", "addr1", "addr2", "city",
                "state", "zip", "country", "addresslabel"]
    contact = ["internalid", "entityid", "firstname", "middlename", "lastname",
               "fullname", "email", "phone"]
    check("master (companyname) -> party", cm.classify_source_columns(master) == cm.PARTY)
    check("shipping (addr/city/zip) -> site", cm.classify_source_columns(shipping) == cm.SITE)
    check("contact (firstname/lastname) -> contact",
          cm.classify_source_columns(contact) == cm.CONTACT)


def test_classify_source_columns_precedence_and_noise():
    # A master that happens to carry a "tax_contact" column must NOT be read as a
    # contact source — only real person-name columns count.
    master_noisy = ["entityid", "companyname", "custentity_tax_contact", "fax", "title"]
    check("tax_contact noise does not make the master a contact source",
          cm.classify_source_columns(master_noisy) == cm.PARTY)
    # An address file with an 'addressee' but no person-name column stays site.
    addr = ["entityid", "addressee", "attention", "addr1", "city", "zip"]
    check("addressee alone is still a site source", cm.classify_source_columns(addr) == cm.SITE)
    check("empty columns -> None", cm.classify_source_columns([]) is None)


# ── classify_frame_grain: fallback on the converted frame ───────────────────
def _frame(cols_values):
    return pd.DataFrame(cols_values)


def test_classify_master_as_party():
    f = _frame({"Organization Name": ["Sturgeon Electric", "Acme"],
                "Party Type": ["ORGANIZATION", "ORGANIZATION"]})
    check("master frame -> party", cm.classify_frame_grain(f) == cm.PARTY)


def test_classify_address_as_site():
    f = _frame({"Address Line 1": ["245 N 950 E", "1 Main St"],
                "Party Type": ["ORGANIZATION", "ORGANIZATION"]})
    check("address frame -> site", cm.classify_frame_grain(f) == cm.SITE)


def test_classify_contact_as_contact():
    f = _frame({"Person First Name": ["Avinash", "Jo"],
                "Person Last Name": ["Chaudhari", "Ng"]})
    check("contact frame -> contact", cm.classify_frame_grain(f) == cm.CONTACT)


def test_classify_no_anchor_is_none():
    f = _frame({"Party Original System Reference": ["1", "2"],
                "Party Type": ["ORGANIZATION", "ORGANIZATION"]})
    check("no name/address/contact anchor -> None (never filtered out on a guess)",
          cm.classify_frame_grain(f) is None)


# ── sheet_rows: the reshape ─────────────────────────────────────────────────
def _merged():
    """A tiny stand-in for the stacked four-source frame.

    Two customers (NT-1 has two addresses + one contact, NT-2 has one address).
    Party Original System Reference starts as the record's OWN id (the bug).
    """
    rows = [
        # master rows (party grain) — one per customer
        {"__grain": "party", "__entityid": "NT-1", "Organization Name": "Alpha",
         "Party Original System Reference": "100"},
        {"__grain": "party", "__entityid": "NT-2", "Organization Name": "Beta",
         "Party Original System Reference": "200"},
        # shipping addresses (site grain)
        {"__grain": "site", "__entityid": "NT-1", "Organization Name": "",
         "Party Original System Reference": "900", "Address Line 1": "1 A St"},
        {"__grain": "site", "__entityid": "NT-1", "Organization Name": "",
         "Party Original System Reference": "901", "Address Line 1": "2 B St"},
        {"__grain": "site", "__entityid": "NT-2", "Organization Name": "",
         "Party Original System Reference": "902", "Address Line 1": "3 C St"},
        # contacts (contact grain)
        {"__grain": "contact", "__entityid": "NT-1", "Organization Name": "",
         "Party Original System Reference": "700", "Person First Name": "Ann"},
    ]
    return pd.DataFrame(rows)


def test_party_sheet_dedupes_to_one_row_per_customer():
    out = cm.sheet_rows(_merged(), "HZ_IMP_PARTIES_T")
    check("party sheet has one row per customer (2), not all 6", len(out) == 2, len(out))
    check("both customers present",
          set(out["__entityid"]) == {"NT-1", "NT-2"}, list(out["__entityid"]))
    check("named master rows survived",
          set(out["Organization Name"]) == {"Alpha", "Beta"}, list(out["Organization Name"]))


def test_site_sheet_gets_only_address_rows():
    out = cm.sheet_rows(_merged(), "HZ_IMP_PARTYSITES_T")
    check("site sheet has the 3 address rows only", len(out) == 3, len(out))
    check("addresses carried", sorted(out["Address Line 1"]) == ["1 A St", "2 B St", "3 C St"],
          list(out["Address Line 1"]))


def test_contact_sheet_gets_only_contact_rows():
    out = cm.sheet_rows(_merged(), "HZ_IMP_CONTACTS_T")
    check("contact sheet has the 1 contact row", len(out) == 1, len(out))
    check("contact carried", list(out["Person First Name"]) == ["Ann"], list(out["Person First Name"]))


def test_party_link_retargeted_to_entityid_on_every_sheet():
    # The whole point of the linkage fix: a child's Party Original System Reference
    # must be its CUSTOMER key, not the child record's own id, so it resolves to the
    # one party row.
    parties = cm.sheet_rows(_merged(), "HZ_IMP_PARTIES_T")
    check("party rows link to their own entityid",
          list(parties["Party Original System Reference"]) == list(parties["__entityid"]),
          list(parties["Party Original System Reference"]))
    sites = cm.sheet_rows(_merged(), "HZ_IMP_PARTYSITES_T")
    check("every address links to its customer (NT-1/NT-1/NT-2), not 900/901/902",
          list(sites["Party Original System Reference"]) == ["NT-1", "NT-1", "NT-2"],
          list(sites["Party Original System Reference"]))
    contacts = cm.sheet_rows(_merged(), "HZ_IMP_CONTACTS_T")
    check("contact links to its customer NT-1, not 700",
          list(contacts["Party Original System Reference"]) == ["NT-1"],
          list(contacts["Party Original System Reference"]))


def test_row_count_total_matches_grains_not_the_stacked_sum():
    df = _merged()
    total_stacked = len(df)  # 6 — the "every sheet gets everything" number
    parties = len(cm.sheet_rows(df, "HZ_IMP_PARTIES_T"))
    sites = len(cm.sheet_rows(df, "HZ_IMP_PARTYSITES_T"))
    contacts = len(cm.sheet_rows(df, "HZ_IMP_CONTACTS_T"))
    check("no sheet carries the full stacked sum",
          parties < total_stacked and sites < total_stacked and contacts < total_stacked,
          f"{parties}/{sites}/{contacts} vs {total_stacked}")
    check("grains partition the rows (2 + 3 + 1 = 6)",
          parties + sites + contacts == total_stacked)


def test_unknown_sheet_returns_all_rows_unchanged():
    df = _merged()
    out = cm.sheet_rows(df, "SOME_UNMAPPED_SHEET")
    check("unknown sheet -> full frame (no rows dropped on a guess)", len(out) == len(df))


def test_missing_grain_column_is_passthrough():
    df = pd.DataFrame({"Organization Name": ["A", "B"]})
    out = cm.sheet_rows(df, "HZ_IMP_PARTIES_T")
    check("no __grain column -> unchanged (non-merged path untouched)",
          out is df or len(out) == 2)


def test_grain_present_but_no_source_of_that_grain_keeps_sheet_nonempty():
    # A load with no contact source must not ship an EMPTY contact backbone sheet
    # (that would orphan the load); it falls back to the full frame.
    rows = [{"__grain": "party", "__entityid": "NT-1", "Organization Name": "Alpha",
             "Party Original System Reference": "100"}]
    df = pd.DataFrame(rows)
    out = cm.sheet_rows(df, "HZ_IMP_CONTACTS_T")
    check("contact sheet with no contact source -> not emptied", len(out) >= 1, len(out))


def test_sheet_reference_is_the_entityid_list():
    sites = cm.sheet_rows(_merged(), "HZ_IMP_PARTYSITES_T")
    ref = cm.sheet_reference(sites)
    check("reference is the per-row customer key", ref == ["NT-1", "NT-1", "NT-2"], ref)
    check("no key column -> None",
          cm.sheet_reference(pd.DataFrame({"x": [1]})) is None)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nEach interface sheet carries only its own grain; children link by entityid")
