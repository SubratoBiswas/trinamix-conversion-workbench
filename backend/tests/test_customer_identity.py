"""Engine-stamped party identity, NETSUITE constants and PROFILES blanks.

These are the fan-out fixes: the merge sets them deterministically (and owns them),
so a brand-new project reproduces them without any per-conversion Party Type rule /
name mapping / constant being present. Covers REC-02/05/07/10/12/15/17/25/67/69/75/
80/81/82/83/84/88.
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


def _col(s, c):
    cc = cm._find_col_ci(s.columns, c)
    return s[cc].tolist() if cc else None


def _parties_frame():
    return pd.DataFrame({
        cm.GRAIN_COL: ["party", "party", "contact", "contact", "contact"],
        cm.ENTITYID_COL: ["NT-1", "NT-2", "NT-1", "NT-1", "NT-2"],
        cm.INTERNALID_COL: ["100", "200", "11", "12", "21"],
        cm.PARTYREF_COL: ["100", "200", "100", "100", "200"],
        "__companyname": ["Acme Corp", "Beta LLC", "Acme Corp", "Acme Corp", "Beta LLC"],
        "__firstname": ["", "", "John", "Jane", "Bob"],
        "__middlename": ["", "", "Q", "", "R"],
        "__lastname": ["", "", "Buyer", "Doe", "Smith"],
        "Party Type": ["", "", "", "", ""],          # fan-out gap: not_applicable / blank
        "Organization Name": ["", "", "", "", ""],
        "Person First Name": ["", "", "", "", ""],
    })


def test_party_type_by_grain():
    s = cm.sheet_rows(_parties_frame(), "HZ_IMP_PARTIES_T")
    check("org rows ORGANIZATION, contact rows PERSON",
          _col(s, "Party Type") == ["ORGANIZATION", "ORGANIZATION", "PERSON", "PERSON", "PERSON"],
          _col(s, "Party Type"))


def test_org_name_only_on_org_rows():
    s = cm.sheet_rows(_parties_frame(), "HZ_IMP_PARTIES_T")
    check("Organization Name = companyname on org rows, blank on contacts",
          _col(s, "Organization Name") == ["Acme Corp", "Beta LLC", "", "", ""],
          _col(s, "Organization Name"))


def test_person_names_only_on_contact_rows():
    s = cm.sheet_rows(_parties_frame(), "HZ_IMP_PARTIES_T")
    check("Person First Name on contacts only",
          _col(s, "Person First Name") == ["", "", "John", "Jane", "Bob"], _col(s, "Person First Name"))
    check("Person Last Name on contacts only",
          _col(s, "Person Last Name") == ["", "", "Buyer", "Doe", "Smith"], _col(s, "Person Last Name"))


def test_account_description_from_companyname():
    df = pd.DataFrame({
        cm.GRAIN_COL: ["party", "party"], cm.ENTITYID_COL: ["NT-1", "NT-2"],
        cm.INTERNALID_COL: ["1", "2"], cm.PARTYREF_COL: ["1", "2"],
        "__companyname": ["Acme Corp", "Beta LLC"], "Account Description": ["", ""],
    })
    s = cm.sheet_rows(df, "HZ_IMP_ACCOUNTS_T")
    check("Account Description <- companyname", _col(s, "Account Description") == ["Acme Corp", "Beta LLC"],
          _col(s, "Account Description"))


def test_relationships_netsuite_constant():
    df = pd.DataFrame({
        cm.GRAIN_COL: ["party", "party"], cm.ENTITYID_COL: ["NT-1", "NT-2"],
        cm.INTERNALID_COL: ["1", "2"], cm.PARTYREF_COL: ["1", "2"],
        "Subject Relationship Party Original System": ["", ""],
        "Object Relationship Party Original System": ["", ""],
    })
    s = cm.sheet_rows(df, "HZ_IMP_RELSHIPS_T")
    check("Subject Original System = NETSUITE",
          _col(s, "Subject Relationship Party Original System") == ["NETSUITE", "NETSUITE"])
    check("Object Original System = NETSUITE",
          _col(s, "Object Relationship Party Original System") == ["NETSUITE", "NETSUITE"])


def test_profiles_forced_blank():
    df = pd.DataFrame({
        cm.GRAIN_COL: ["party", "party"], cm.ENTITYID_COL: ["NT-1", "NT-2"],
        cm.INTERNALID_COL: ["1", "2"], cm.PARTYREF_COL: ["1", "2"],
        "Party Original System": ["NETSUITE", "NETSUITE"], "Credit Rating": ["21", "8"],
        "Party Number": ["NXT1", "NXT2"], "Credit Limit": ["500", "600"],
    })
    s = cm.sheet_rows(df, "RA_CUSTOMER_PROFILES_INT_ALL")
    check("Party Original System blanked", _col(s, "Party Original System") == ["", ""])
    check("Credit Rating blanked", _col(s, "Credit Rating") == ["", ""])
    check("Party Number blanked", _col(s, "Party Number") == ["", ""])
    check("Credit Limit kept (not blanked)", _col(s, "Credit Limit") == ["500", "600"])


def test_owned_fields_cover_new_rules():
    check("PARTIES owns identity",
          {"Party Type", "Organization Name", "Person First Name",
           "Person Middle Name", "Person Last Name"} <= cm.merge_owned_fields("HZ_IMP_PARTIES_T"))
    check("ACCOUNTS owns Account Description",
          "Account Description" in cm.merge_owned_fields("HZ_IMP_ACCOUNTS_T"))
    check("PROFILES owns its blanked fields",
          {"Party Original System", "Credit Rating", "Party Number"} <= cm.merge_owned_fields("RA_CUSTOMER_PROFILES_INT_ALL"))
    check("PARTYSITEUSES not swept by the partysites blank rule",
          "Relationship Source System Reference" not in cm.merge_owned_fields("HZ_IMP_PARTYSITEUSES_T"))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nEngine stamps party identity + NETSUITE constants + PROFILES blanks; all owned → fan out.")
