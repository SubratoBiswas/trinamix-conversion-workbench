"""Cross-grain enrichment by entityid (REC-08).

A Customer load is several source files at different grains, each converted on its own,
all keyed by entityid. Party Site From Date = COALESCE(startdate, datecreated) is
evaluated on the ADDRESS rows, but startdate/datecreated live only in the MASTER — so
those two columns are joined onto the address frames by entityid before conversion.

Person names and companyname are DELIBERATELY NOT borrowed: the contact people are now
materialised as PERSON party rows on HZ_IMP_PARTIES_T in their own right (see
sheet_rows / test_customer_merge_grain), so each grain keeps its own identity — master
= companyname (ORGANIZATION), contact = names (PERSON). Borrowing would cross them.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import customer_merge as CM            # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _master():
    return pd.DataFrame({
        "internalid": ["1001", "1002"],
        "entityid": ["NT-1", "NT-2"],
        "companyname": ["Acme Corp", ""],
        "startdate": ["2024-01-01", ""],
        "datecreated": ["2023-06-01", "2023-07-15"],
        "title": ["Project Manager", ""],   # the customer's primary-contact job title
    })


def _contacts():
    return pd.DataFrame({
        "entityid": ["NT-1", "NT-2"],
        "firstname": ["John", "Jane"],
        "middlename": ["", "Q"],
        "lastname": ["Buyer", "Doe"],
    })


def _addresses():
    return pd.DataFrame({
        "entityid": ["NT-1", "NT-1", "NT-2"],
        "addr1": ["1 St", "2 Ave", "9 Rd"],
        "city": ["Austin", "Dallas", "Reno"],
    })


def test_enrichment_borrows_site_dates_and_title_only():
    enr = CM.build_entity_enrichment([_master(), _contacts(), _addresses()])
    check("startdate gathered from master", enr["startdate"]["NT-1"] == "2024-01-01")
    check("datecreated gathered", enr["datecreated"]["NT-2"] == "2023-07-15")
    # title IS borrowed (REC-62): the customer's job title, to reach its contact rows.
    check("title gathered from master", enr["title"]["NT-1"] == "Project Manager")
    # Person names / companyname are NOT borrowed — the contact people carry their own.
    check("firstname NOT borrowed", "firstname" not in enr)
    check("companyname NOT borrowed", "companyname" not in enr)


def test_title_is_borrowed_onto_contact_rows():
    # REC-62: HZ_IMP_CONTACTS_T Job Title <- title. The contact extract has no title,
    # so the customer's title (master) is joined onto its contact rows by entityid.
    enr = CM.build_entity_enrichment([_master(), _contacts(), _addresses()])
    c = CM.enrich_source_frame(_contacts(), enr)
    check("contacts now carry a title column", "title" in list(c.columns))
    nt1 = c[c["entityid"] == "NT-1"].iloc[0]
    check("NT-1 contact gets the customer's title", nt1["title"] == "Project Manager", nt1["title"])
    nt2 = c[c["entityid"] == "NT-2"].iloc[0]
    check("NT-2 contact title blank (master had none)", str(nt2["title"]).strip() == "", nt2["title"])


def test_addresses_get_startdate_and_datecreated():
    enr = CM.build_entity_enrichment([_master(), _contacts(), _addresses()])
    a = CM.enrich_source_frame(_addresses(), enr)
    check("addresses now carry startdate", "startdate" in list(a.columns))
    check("addresses now carry datecreated", "datecreated" in list(a.columns))
    nt1 = a[a["entityid"] == "NT-1"]
    check("both NT-1 addresses get the master startdate",
          nt1["startdate"].tolist() == ["2024-01-01", "2024-01-01"], nt1["startdate"].tolist())
    nt2 = a[a["entityid"] == "NT-2"].iloc[0]
    check("NT-2 startdate blank (will COALESCE to datecreated)",
          str(nt2["startdate"]).strip() == "")
    check("NT-2 datecreated present", str(nt2["datecreated"]) == "2023-07-15")


def test_contacts_are_not_given_a_company_name():
    # Enrichment must not put the customer's company onto the contact rows, or every
    # contact would read as an ORGANIZATION. companyname is simply not a borrowable.
    enr = CM.build_entity_enrichment([_master(), _contacts(), _addresses()])
    c = CM.enrich_source_frame(_contacts(), enr)
    check("contacts have no companyname column", "companyname" not in list(c.columns))


def test_real_source_values_are_never_overwritten():
    # enrich_source_frame fills only a column the frame lacks or has all-blank.
    enr = {"companyname": {"NT-1": "WRONG", "NT-2": "WRONG"}}
    out = CM.enrich_source_frame(_master(), enr)
    check("existing companyname untouched",
          out[out["entityid"] == "NT-1"].iloc[0]["companyname"] == "Acme Corp")


def test_no_entityid_frame_is_returned_untouched():
    df = pd.DataFrame({"x": [1, 2]})
    out = CM.enrich_source_frame(df, {"startdate": {"NT-1": "2024-01-01"}})
    check("no entityid -> unchanged", out.equals(df))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nCross-grain enrichment borrows only startdate/datecreated; grains keep their identity.")
