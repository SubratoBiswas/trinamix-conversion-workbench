"""Employee HDL Country / LegislationCode -> 2-letter ISO code (HCM-03 + follow-up).

The Location sheet's Country (and LegislationCode) must be the 2-letter ISO 3166-1
code, not the full name. The local COUNTRY_ISO2 crosswalk covered ~22 countries, so
Saudi Arabia / United Arab Emirates / Israel / Chile / South Africa (SA/AE/IL/CL/ZA)
fell through and shipped their full name. _iso_country now falls back to the
comprehensive COUNTRY_TO_ISO table so every country resolves.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.hdl_output_service import _iso_country   # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_the_reported_countries_now_resolve():
    for name, code in [("Saudi Arabia", "SA"), ("United Arab Emirates", "AE"),
                       ("Israel", "IL"), ("Chile", "CL"), ("South Africa", "ZA")]:
        check(f"{name} -> {code}", _iso_country(name) == code, _iso_country(name))


def test_existing_countries_unchanged():
    for name, code in [("United States", "US"), ("India", "IN"), ("Spain", "ES"),
                       ("Australia", "AU")]:
        check(f"{name} -> {code}", _iso_country(name) == code, _iso_country(name))


def test_already_a_code_passes_through_uppercased():
    check("us -> US", _iso_country("us") == "US")
    check("IN -> IN", _iso_country("IN") == "IN")


def test_blank_and_unknown():
    check("blank stays blank", _iso_country("") == "")
    # An unrecognised country is left as-is rather than guessed.
    check("unknown left as-is", _iso_country("Freedonia") == "Freedonia")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nEmployee HDL Country now resolves every country to its ISO-2 code.")
