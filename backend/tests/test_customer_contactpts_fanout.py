"""Contact-point fan-out on HZ_IMP_CONTACTPTS_T (REC-48/53/54/56/57).

A NetSuite contact carries an e-mail and a phone in ONE row, but Oracle's contact-
points interface is one row per contact point. Each contact fans out into an EMAIL row
(Contact Point Type=EMAIL, Email Address set) and a PHONE row (Type=PHONE, Phone Number
set, Phone Line Type=MOBILE), only for the points the source actually has, with the
point's Original System Reference tagged _EMAIL / _PHONE. A contact with neither keeps a
single row so no contact is dropped.
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


def _contacts():
    # Fields the sheet carries + the raw contact-point columns threaded as __*.
    cols = {
        cm.GRAIN_COL: "contact",
        cm.ENTITYID_COL: ["NT-1", "NT-2", "NT-3", "NT-4"],
        cm.INTERNALID_COL: ["111", "222", "333", "444"],
        "Contact Point Type": ["EMAIL", "EMAIL", "EMAIL", "EMAIL"],   # stray constant
        "Email Address": ["", "", "", ""],
        "Phone Number": ["", "", "", ""],
        "Phone Line Type": ["", "", "", ""],
        "Contact Point Original System Reference": ["", "", "", ""],
        "__email": ["a@x.com", "b@x.com", "", ""],
        "__altemail": ["", "", "", ""],
        "__phone": ["555-1", "", "555-3", ""],
        "__mobilephone": ["", "", "", ""],
    }
    return pd.DataFrame(cols)


def _fanned():
    return cm._fanout_contact_points(_contacts()).reset_index(drop=True)


def test_contact_with_email_and_phone_makes_two_points():
    f = _fanned()
    nt1 = f[f[cm.ENTITYID_COL] == "NT-1"]
    check("NT-1 -> 2 rows (email + phone)", len(nt1) == 2, len(nt1))
    types = sorted(nt1["Contact Point Type"])
    check("NT-1 has EMAIL and PHONE", types == ["EMAIL", "PHONE"], types)
    em = nt1[nt1["Contact Point Type"] == "EMAIL"].iloc[0]
    ph = nt1[nt1["Contact Point Type"] == "PHONE"].iloc[0]
    check("EMAIL row Email Address set", em["Email Address"] == "a@x.com", em["Email Address"])
    check("EMAIL row Phone Number blank", em["Phone Number"] == "", em["Phone Number"])
    check("EMAIL OSR _EMAIL", em["Contact Point Original System Reference"] == "NT-1_111_EMAIL",
          em["Contact Point Original System Reference"])
    check("PHONE row Phone Number set", ph["Phone Number"] == "555-1", ph["Phone Number"])
    check("PHONE row line type MOBILE", ph["Phone Line Type"] == "MOBILE", ph["Phone Line Type"])
    check("PHONE OSR _PHONE", ph["Contact Point Original System Reference"] == "NT-1_111_PHONE",
          ph["Contact Point Original System Reference"])


def test_email_only_and_phone_only():
    f = _fanned()
    nt2 = f[f[cm.ENTITYID_COL] == "NT-2"]
    check("NT-2 email-only -> 1 EMAIL row", len(nt2) == 1 and nt2.iloc[0]["Contact Point Type"] == "EMAIL", len(nt2))
    nt3 = f[f[cm.ENTITYID_COL] == "NT-3"]
    check("NT-3 phone-only -> 1 PHONE row", len(nt3) == 1 and nt3.iloc[0]["Contact Point Type"] == "PHONE", len(nt3))
    check("NT-3 phone number set", nt3.iloc[0]["Phone Number"] == "555-3", nt3.iloc[0]["Phone Number"])


def test_contact_with_no_points_is_kept():
    f = _fanned()
    nt4 = f[f[cm.ENTITYID_COL] == "NT-4"]
    check("NT-4 no points -> kept as 1 row", len(nt4) == 1, len(nt4))


def test_merge_owned_fields_cover_the_right_sheets():
    cp = cm.merge_owned_fields("HZ_IMP_CONTACTPTS_T")
    check("CONTACTPTS owns Contact Point Type", "Contact Point Type" in cp, cp)
    check("CONTACTPTS owns Email Address / Phone Number",
          {"Email Address", "Phone Number", "Phone Line Type"} <= cp, cp)
    psu = cm.merge_owned_fields("HZ_IMP_PARTYSITEUSES_T")
    check("PARTYSITEUSES owns Primary Indicator", psu == {"Primary Indicator"}, psu)
    check("PARTIES owns nothing", cm.merge_owned_fields("HZ_IMP_PARTIES_T") == set(),
          cm.merge_owned_fields("HZ_IMP_PARTIES_T"))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nContact points fan out into e-mail and phone rows, each with its own type/value/ref.")
