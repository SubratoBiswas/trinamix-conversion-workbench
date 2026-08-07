"""REC-04 / REC-11: Party Original System Reference = the CUSTOMER's internalid.

The multi-grain Customer merge links a customer's party, addresses and contacts by
a single key. It used to stamp the customer's ``entityid`` (NT-1872) into Party
Original System Reference, but the analyst's document maps that column to the
customer's ``internalid`` (595895) — the learning-centre rule is right and the file
was wrong (REC-04). The fix threads the CUSTOMER's internalid (taken from the master
rows, resolved by entityid) onto every row via ``__partyref`` and points the party
link and the linkage reference at it, so the party row and all its children agree on
the customer's internalid (REC-11), while the site-level keys are left untouched.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd                                       # noqa: E402
from app.services import customer_merge as cm             # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _merged():
    rows = [
        {cm.GRAIN_COL: "party", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "595895",
         "Party Original System Reference": "x"},
        {cm.GRAIN_COL: "party", cm.ENTITYID_COL: "NT-2", cm.INTERNALID_COL: "606408",
         "Party Original System Reference": "x"},
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-1", cm.INTERNALID_COL: "999001",
         "Party Site Original System Reference": "s1", "Party Original System Reference": "x"},
        {cm.GRAIN_COL: "contact", cm.ENTITYID_COL: "NT-2", cm.INTERNALID_COL: "888002",
         "Party Original System Reference": "x"},
        {cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-9", cm.INTERNALID_COL: "777003",
         "Party Original System Reference": "x"},          # orphan: no master row
    ]
    return cm.set_party_ref_from_master(pd.DataFrame(rows))


def test_partyref_is_the_customers_internalid_on_every_grain():
    df = _merged()
    # party rows carry their own internalid (== the customer internalid);
    # a child carries its CUSTOMER's internalid, not its own record internalid.
    got = dict(zip(df[cm.ENTITYID_COL] + "/" + df[cm.GRAIN_COL], df[cm.PARTYREF_COL]))
    check("party NT-1", got["NT-1/party"] == "595895", got)
    check("site NT-1 -> customer internalid", got["NT-1/site"] == "595895", got)
    check("contact NT-2 -> customer internalid", got["NT-2/contact"] == "606408", got)


def test_orphan_without_master_falls_back_to_entityid():
    df = _merged()
    orphan = df[df[cm.ENTITYID_COL] == "NT-9"].iloc[0]
    check("orphan -> entityid", orphan[cm.PARTYREF_COL] == "NT-9", orphan[cm.PARTYREF_COL])


def test_set_party_link_writes_customer_internalid_and_leaves_site_key():
    df = _merged()
    site = df[df[cm.GRAIN_COL] == "site"].reset_index(drop=True)
    cm._set_party_link(site)
    check("site party link = customer internalid",
          site["Party Original System Reference"].tolist() == ["595895", "NT-9"],
          site["Party Original System Reference"].tolist())
    # The site-level key is NOT retargeted — only the party link is.
    check("site source system reference untouched",
          str(site["Party Site Original System Reference"].tolist()[0]) == "s1")


def test_sheet_reference_returns_the_customer_internalid():
    df = _merged()
    site = df[df[cm.GRAIN_COL] == "site"].reset_index(drop=True)
    check("sheet_reference = customer internalid",
          cm.sheet_reference(site) == ["595895", "NT-9"], cm.sheet_reference(site))


def test_no_internalid_column_falls_back_to_entityid_behaviour():
    # Backward compatibility: a frame the resolve never ran on (no __internalid /
    # no __partyref) links by entityid exactly as before.
    df = pd.DataFrame([{cm.GRAIN_COL: "site", cm.ENTITYID_COL: "NT-5",
                        "Party Original System Reference": "old"}])
    cm._set_party_link(df)
    check("entityid fallback", df["Party Original System Reference"].tolist() == ["NT-5"])


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nParty Original System Reference is the customer's internalid on every grain (REC-04/REC-11).")
