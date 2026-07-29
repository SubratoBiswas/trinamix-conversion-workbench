"""Offline US / Canada address validation.

Pure: stdlib only, no network, no credentials, no DB — so the rules that decide
whether an address ships can be exercised without a provider account.

Runs under pytest or `python3 backend/tests/test_address_service.py`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.address_service import (  # noqa: E402
    address_key, distinct_addresses, normalize_country, normalize_postal,
    normalize_region, validate_address, validate_many,
)

_failures = []


def check(name, cond, detail=""):
    """Record AND raise.

    It must raise: pytest judges a test by whether it throws, so a check that
    only printed FAIL reported the whole file green. This suite showed
    "15 passed" while two checks were failing.
    """
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def codes(addr):
    return {i["code"] for i in validate_address(addr)["issues"]}


# ── Country ──────────────────────────────────────────────────────────────────
def test_country_to_iso2():
    for raw in ("US", "usa", "U.S.A.", "United States", "united states of america"):
        check(f"{raw!r} -> US", normalize_country(raw) == "US",
              f"got {normalize_country(raw)!r}")
    for raw in ("CA", "can", "Canada", "canada"):
        check(f"{raw!r} -> CA", normalize_country(raw) == "CA",
              f"got {normalize_country(raw)!r}")
    check("Brazil is out of scope", normalize_country("Brazil") is None)
    check("blank is None", normalize_country("") is None)


# ── State / province ─────────────────────────────────────────────────────────
def test_region_codes_and_names():
    check("TX stays TX", normalize_region("TX", "US") == "TX")
    check("California -> CA", normalize_region("California", "US") == "CA")
    check("Ontario -> ON", normalize_region("Ontario", "CA") == "ON")
    check("ON stays ON", normalize_region("ON", "CA") == "ON")
    check("garbage is None", normalize_region("Freedonia", "US") is None)


def test_canadian_legacy_and_french_variants():
    """NetSuite and eBOS extracts carry these; a strict code check rejects them."""
    for raw, want in (("Québec", "QC"), ("PQ", "QC"), ("Ont", "ON"),
                      ("Newfoundland", "NL"), ("PEI", "PE"), ("NWT", "NT")):
        got = normalize_region(raw, "CA")
        check(f"{raw!r} -> {want}", got == want, f"got {got!r}")


# ── Postal ───────────────────────────────────────────────────────────────────
def test_postal_normalisation():
    check("zip5 kept", normalize_postal("78741", "US") == "78741")
    check("zip+4 hyphenated", normalize_postal("787416789", "US") == "78741-6789")
    check("zip+4 already hyphenated", normalize_postal("78741-6789", "US") == "78741-6789")
    check("CA postal spaced and uppercased",
          normalize_postal("m5h2n2", "CA") == "M5H 2N2")
    check("CA postal already spaced", normalize_postal("M5H 2N2", "CA") == "M5H 2N2")


def test_invalid_postals_rejected():
    check("letters in a ZIP", normalize_postal("ABCDE", "US") is None)
    check("4-digit ZIP", normalize_postal("1234", "US") is None)
    # D, F, I, O, Q, U never appear in a Canadian postal code.
    check("banned letter D", normalize_postal("D5H 2N2", "CA") is None)
    check("banned letter O in position 3", normalize_postal("M5O 2N2", "CA") is None)


# ── The cross-check: postal vs region ────────────────────────────────────────
def test_canadian_postal_region_mismatch():
    bad = dict(line1="1 Main St", city="Vancouver", region="ON",
               postal="V6B 1A1", country="CA")
    check("V-prefix in Ontario is an error",
          "postal_region_mismatch" in codes(bad))
    good = dict(bad, region="BC")
    check("V-prefix in BC is clean", codes(good) == set(), f"got {codes(good)}")


def test_us_zip_region_mismatch():
    bad = dict(line1="500 Oracle Pkwy", city="Austin", region="California",
               postal="78741", country="US")
    check("Texas ZIP in California is an error",
          "postal_region_mismatch" in codes(bad))
    good = dict(bad, region="TX")
    check("same ZIP in Texas is clean", codes(good) == set(), f"got {codes(good)}")


def test_multi_range_states_are_not_false_positives():
    """NY, MA, TX and VA hold several non-contiguous prefix ranges — a naive
    single-range check flags their real addresses as wrong."""
    for region, postal, city in (("NY", "10001", "New York"),
                                 ("NY", "06390", "Fishers Island"),
                                 ("MA", "02101", "Boston"),
                                 ("TX", "88501", "El Paso"),
                                 ("VA", "20101", "Herndon"),
                                 ("VA", "23219", "Richmond")):
        a = dict(line1="1 Main St", city=city, region=region,
                 postal=postal, country="US")
        check(f"{region} {postal} accepted",
              "postal_region_mismatch" not in codes(a), f"got {codes(a)}")


def test_northern_territories_share_a_prefix():
    """X covers both NT and NU — neither may be flagged."""
    for r in ("NT", "NU"):
        a = dict(line1="1 Main St", city="Yellowknife", region=r,
                 postal="X1A 1A1", country="CA")
        check(f"X-prefix accepted for {r}",
              "postal_region_mismatch" not in codes(a), f"got {codes(a)}")


# ── Required fields and status ───────────────────────────────────────────────
def test_missing_required_fields():
    c = codes(dict(city="Austin", region="TX", postal="78741", country="US"))
    check("missing line1 is an error", "line1_missing" in c)
    c = codes(dict(line1="1 Main St", region="TX", postal="78741", country="US"))
    check("missing city is a warning only", "city_missing" in c)
    c = codes(dict(line1="1 Main St", city="Austin", region="TX", postal="78741"))
    check("missing country is an error", "country_missing" in c)


def test_status_ladder():
    clean = validate_address(dict(line1="500 Oracle Pkwy", city="Austin",
                                  region="TX", postal="78741", country="US"))
    check("fully valid -> ok", clean["status"] == "ok", f"got {clean['status']}")
    warn = validate_address(dict(line1="1 Main St", city="Toronto",
                                 region="Ontario", postal="m5h2n2", country="Canada"))
    check("only normalisation -> warning", warn["status"] == "warning",
          f"got {warn['status']}")
    check("...and it returns the Oracle shapes",
          warn["normalized"]["region"] == "ON"
          and warn["normalized"]["postal"] == "M5H 2N2"
          and warn["normalized"]["country"] == "CA",
          f"got {warn['normalized']}")
    err = validate_address(dict(line1="1 Main St", city="Vancouver",
                                region="ON", postal="V6B 1A1", country="CA"))
    check("contradiction -> error", err["status"] == "error")


def test_out_of_scope_country_is_flagged_not_crashed():
    """Their supplier data carries Brazilian addresses (Nextpower Brasil). Those
    must report cleanly as out of scope, not blow up or silently pass."""
    r = validate_address(dict(line1="Rua Pará, 126", city="Sao Paulo",
                              region="SP", postal="01234-567", country="Brazil"))
    check("flagged as unrecognised country",
          "country_unrecognised" in {i["code"] for i in r["issues"]})
    check("status is error, not ok", r["status"] == "error")


# ── Deduplication — the cost story ───────────────────────────────────────────
def test_address_key_ignores_case_and_spacing():
    a = dict(line1="500 Oracle Pkwy", city="Austin", region="TX",
             postal="78741", country="US")
    b = dict(line1="  500  ORACLE PKWY ", city="austin", region="tx",
             postal="78741", country="us")
    check("cosmetic differences share a key", address_key(a) == address_key(b))
    c = dict(a, line1="501 Oracle Pkwy")
    check("a real difference does not", address_key(a) != address_key(c))


def test_validate_many_deduplicates():
    a = dict(line1="500 Oracle Pkwy", city="Austin", region="TX",
             postal="78741", country="US")
    b = dict(line1="1 Main St", city="Vancouver", region="ON",
             postal="V6B 1A1", country="CA")
    s = validate_many([a, a, a, b, b])
    check("5 rows collapse to 2 lookups", s["distinct"] == 2, f"got {s['distinct']}")
    check("saving is reported", s["lookups_saved"] == 3, f"got {s['lookups_saved']}")
    check("one ok, one error",
          s["counts"]["ok"] == 1 and s["counts"]["error"] == 1, f"got {s['counts']}")
    check("issue codes are tallied",
          s["issues_by_code"].get("postal_region_mismatch") == 1)


def test_distinct_addresses_keeps_one_representative():
    a = dict(line1="1 Main St", city="Austin", region="TX", postal="78741", country="US")
    d = distinct_addresses([a, dict(a), dict(a)])
    check("three identical rows -> one entry", len(d) == 1)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except AssertionError:
            pass          # already recorded; keep going so one run shows them all
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("all checks passed")
