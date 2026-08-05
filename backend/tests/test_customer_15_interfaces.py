"""The Customer load is EXACTLY these 15 interfaces — no more, no fewer.

Analyst, 05-Aug, with the interface table attached: "Follow this for customer
conversions, do not create any extra sheet from the ones above." Oracle's Customer
template ships 19 interface tables; NextPower loads 15. The four extras
(HZ_IMP_ACCOUNTRELS, HZ_IMP_CLASSIFICS_T, RA_CUST_PAY_METHOD_INT_ALL,
RA_CUSTOMER_BANKS_INT_ALL) must never be generated.

The 15, their field counts and their Oracle CSV names are transcribed here from
the analyst's own table, so the spec the generator reads is pinned to what the
analyst signed off. A drift in either direction — a dropped interface, a resur-
rected extra, a changed field count — fails here rather than in a load.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SPEC = json.loads(
    (Path(__file__).resolve().parent.parent / "app" / "data"
     / "customer_fbdi_column_order.json").read_text(encoding="utf-8"))

# (interface, field_count, csv_name) — the analyst's table, rows 2-16, in order.
_TABLE = [
    ("HZ_IMP_PARTIES_T", 49, "HzImpPartiesT"),
    ("HZ_IMP_PARTYSITES_T", 48, "HzImpPartySitesT"),
    ("HZ_IMP_PARTYSITEUSES_T", 43, "HzImpPartySiteUsesT"),
    ("HZ_IMP_ACCOUNTS_T", 84, "HzImpAccountsT"),
    ("HZ_IMP_ACCTSITES_T", 89, "HzImpAcctSitesT"),
    ("HZ_IMP_ACCTSITEUSES_T", 45, "HzImpAcctSiteUsesT"),
    ("HZ_IMP_ACCTCONTACTS_T", 68, "HzImpAcctContactsT"),
    ("HZ_IMP_CONTACTPTS_T", 79, "HzImpContactPtsT"),
    ("HZ_IMP_CONTACTROLES", 67, "HzImpContactRoles"),
    ("HZ_IMP_CONTACTS_T", 66, "HzImpContactsT"),
    ("HZ_IMP_LOCATIONS_T", 87, "HzImpLocationsT"),
    ("HZ_IMP_RELSHIPS_T", 69, "HzImpRelshipsT"),
    ("HZ_IMP_ROLERESP", 64, "HzImpRoleResp"),
    ("HZ_IMP_PERSONLANG", 6, "HzImpPersonLang"),
    ("RA_CUSTOMER_PROFILES_INT_ALL", 132, "RaCustomerProfilesIntAll"),
]

_EXTRAS_NEVER_GENERATED = {
    "HZ_IMP_ACCOUNTRELS", "HZ_IMP_CLASSIFICS_T",
    "RA_CUST_PAY_METHOD_INT_ALL", "RA_CUSTOMER_BANKS_INT_ALL",
}


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_the_load_scope_is_exactly_the_fifteen_in_order():
    want = [iface for iface, _, _ in _TABLE]
    check("load_scope is the 15, in the analyst's order",
          _SPEC["load_scope"] == want, f"got {_SPEC['load_scope']}")
    check("load_sequence agrees", _SPEC["load_sequence"] == want,
          f"got {_SPEC['load_sequence']}")


def test_each_interface_has_the_field_count_and_csv_name_the_analyst_gave():
    for iface, fc, csv in _TABLE:
        s = _SPEC["sheets"].get(iface)
        check(f"{iface} is in the spec", s is not None)
        check(f"{iface} field count is {fc}", s["field_count"] == fc,
              f"got {s['field_count']}")
        check(f"{iface} csv order is {fc} wide", len(s["csv_order"]) == fc,
              f"got {len(s['csv_order'])}")
        check(f"{iface} csv name is {csv}", s["csv"] == csv, f"got {s['csv']!r}")


def test_the_four_extras_are_excluded_and_never_in_scope():
    excluded = {e["sheet"] for e in _SPEC["excluded_interfaces"]}
    for extra in _EXTRAS_NEVER_GENERATED:
        check(f"{extra} is on the excluded list", extra in excluded,
              f"excluded = {sorted(excluded)}")
        check(f"{extra} is not in the load scope",
              extra not in _SPEC["load_scope"])


def test_nothing_outside_the_fifteen_is_in_scope():
    """The 'no extra sheet' half, stated directly: the scope holds the 15 and
    nothing else, so a sixteenth interface cannot slip in via the spec."""
    check("exactly 15 in scope", len(_SPEC["load_scope"]) == 15,
          f"got {len(_SPEC['load_scope'])}")
    check("no extra hides in the scope",
          not (set(_SPEC["load_scope"]) & _EXTRAS_NEVER_GENERATED))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nthe Customer load is exactly the 15")
