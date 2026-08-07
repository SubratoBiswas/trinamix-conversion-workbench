"""Contact fan-out: contact people become PERSON parties (REC-05 / REC-06 / REC-07).

HZ_IMP_PARTIES_T holds EVERY party: the customer orgs (party grain, deduped to one per
entityid) AND the contact people (contact grain), each a PERSON party in its own right.
So a contact carries its own name (REC-07), Party Type = PERSON (REC-05), a unique party
reference (its own internalid), and a party number NXT<org>_C<n> that shares the org's
base number and increments per person in the customer (REC-06). Child sheets still link
to the customer's org party by the customer internalid.
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


def _merged():
    rows = [
        {cm.GRAIN_COL: "party", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "595895",
         "Party Type": "ORGANIZATION", "Party Number": "", "Organization Name": "Acme",
         "Party Original System Reference": "x", "Person First Name": ""},
        {cm.GRAIN_COL: "party", cm.ENTITYID_COL: "NT-2", cm.INTERNALID_COL: "606408",
         "Party Type": "ORGANIZATION", "Party Number": "", "Organization Name": "Beta",
         "Party Original System Reference": "x", "Person First Name": ""},
        {cm.GRAIN_COL: "contact", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "111",
         "Party Type": "PERSON", "Party Number": "", "Organization Name": "",
         "Party Original System Reference": "x", "Person First Name": "Alice"},
        {cm.GRAIN_COL: "contact", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "112",
         "Party Type": "PERSON", "Party Number": "", "Organization Name": "",
         "Party Original System Reference": "x", "Person First Name": "Bob"},
        {cm.GRAIN_COL: "contact", cm.ENTITYID_COL: "NT-2", cm.INTERNALID_COL: "113",
         "Party Type": "PERSON", "Party Number": "", "Organization Name": "",
         "Party Original System Reference": "x", "Person First Name": "Carol"},
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "999",
         "Party Type": "", "Party Number": "", "Organization Name": "",
         "Party Original System Reference": "x", "Person First Name": ""},
    ]
    return cm.set_party_ref_from_master(pd.DataFrame(rows))


def _parties():
    return cm.sheet_rows(_merged(), "HZ_IMP_PARTIES_T").reset_index(drop=True)


def test_parties_sheet_has_orgs_and_contact_people():
    p = _parties()
    check("2 orgs + 3 contacts = 5 party rows", len(p) == 5, len(p))
    types = list(p["Party Type"])
    check("2 ORGANIZATION, 3 PERSON", types.count("ORGANIZATION") == 2 and types.count("PERSON") == 3, types)
    check("person names present", set(p["Person First Name"]) >= {"Alice", "Bob", "Carol"},
          list(p["Person First Name"]))
    check("no site rows leaked", 999 not in [int(x) for x in p[cm.INTERNALID_COL]], list(p[cm.INTERNALID_COL]))


def test_party_number_shares_org_base_and_increments_per_person():
    p = _parties()
    num = dict(zip(p[cm.INTERNALID_COL], p["Party Number"]))
    check("org NT-1 = NXT000001", num["595895"] == "NXT000001", num)
    check("org NT-2 = NXT000002", num["606408"] == "NXT000002", num)
    check("NT-1 person A = NXT000001_C1", num["111"] == "NXT000001_C1", num)
    check("NT-1 person B = NXT000001_C2", num["112"] == "NXT000001_C2", num)
    check("NT-2 person C = NXT000002_C1", num["113"] == "NXT000002_C1", num)


def test_person_party_reference_is_its_own_internalid():
    p = _parties()
    ref = dict(zip(p[cm.INTERNALID_COL], p["Party Original System Reference"]))
    check("org ref = its internalid", ref["595895"] == "595895", ref)
    check("person A ref = its OWN internalid (not the customer's)", ref["111"] == "111", ref)
    check("person B ref = 112", ref["112"] == "112", ref)


def test_child_site_still_links_to_the_customer_org():
    sites = cm.sheet_rows(_merged(), "HZ_IMP_PARTYSITES_T")
    check("site links to customer internalid, not its own",
          list(sites["Party Original System Reference"]) == ["595895"],
          list(sites["Party Original System Reference"]))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nContact people are PERSON parties: own name, type, reference and NXT_C numbering.")
