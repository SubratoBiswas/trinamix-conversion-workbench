"""Phone/fax splitting for the PHONE_PART rule. Relocated VERBATIM out of engine.py so
the phone rule strategy owns no parsing logic itself. The region table + region lookup +
the libphonenumber split are pure functions of (value, row); phonenumbers is imported
lazily so importing this module never requires the package. ``phone_region_for`` reads
the row's Country column via the domain's own case-insensitive row accessor."""
from __future__ import annotations

import re
from typing import Any

from app.domain.rules.context import _row_value_ci


# Country name -> ISO-3166 alpha-2 region. Covers every country present in the
# NextPower supplier extract plus the common English aliases an extract uses.
_PHONE_REGION_BY_COUNTRY = {
    "albania": "AL", "australia": "AU", "austria": "AT", "belgium": "BE",
    "brazil": "BR", "brunei darussalam": "BN", "brunei": "BN", "bulgaria": "BG",
    "cambodia": "KH", "canada": "CA", "chile": "CL", "china": "CN",
    "colombia": "CO", "costa rica": "CR", "cyprus": "CY", "denmark": "DK",
    "egypt": "EG", "estonia": "EE", "finland": "FI", "france": "FR",
    "germany": "DE", "hong kong": "HK", "hungary": "HU", "india": "IN",
    "indonesia": "ID", "israel": "IL", "italy": "IT", "kenya": "KE",
    "malaysia": "MY", "malta": "MT", "mauritius": "MU", "mexico": "MX",
    "netherlands": "NL", "new zealand": "NZ", "norway": "NO", "panama": "PA",
    "peru": "PE", "philippines": "PH", "poland": "PL", "portugal": "PT",
    "romania": "RO", "rwanda": "RW", "saudi arabia": "SA", "singapore": "SG",
    "south africa": "ZA", "spain": "ES", "sweden": "SE", "switzerland": "CH",
    "taiwan (province of china)": "TW", "taiwan": "TW", "thailand": "TH",
    "tunisia": "TN", "turkiye": "TR", "turkey": "TR", "united arab emirates": "AE",
    "united kingdom": "GB", "united states": "US", "uruguay": "UY",
    "viet nam": "VN", "vietnam": "VN",
    # common aliases the extract may carry
    "usa": "US", "u.s.a.": "US", "united states of america": "US",
    "u.s.": "US", "uk": "GB", "u.k.": "GB", "great britain": "GB",
    "england": "GB", "uae": "AE", "u.a.e.": "AE", "korea": "KR",
    "south korea": "KR", "japan": "JP", "ireland": "IE", "russia": "RU",
    "russian federation": "RU", "argentina": "AR", "greece": "GR",
    "czech republic": "CZ", "czechia": "CZ", "slovakia": "SK", "slovenia": "SI",
    "croatia": "HR", "luxembourg": "LU", "iceland": "IS", "nigeria": "NG",
    "ghana": "GH", "morocco": "MA", "qatar": "QA", "kuwait": "KW",
    "bahrain": "BH", "oman": "OM", "jordan": "JO", "lebanon": "LB",
    "pakistan": "PK", "bangladesh": "BD", "sri lanka": "LK", "nepal": "NP",
}
_PHONE_REGION_NORM = {
    re.sub(r"[^a-z0-9]", "", k): v for k, v in _PHONE_REGION_BY_COUNTRY.items()
}


def phone_region_for(row: Any) -> "str | None":
    """The ISO-2 region hint for this row, read from its Country column."""
    if row is None:
        return None
    for name in ("Country", "country", "Country Name", "country_name",
                 "Country Code", "country_code"):
        v = _row_value_ci(row, name)
        key = re.sub(r"[^a-z0-9]", "", str(v or "").strip().lower())
        if key and key in _PHONE_REGION_NORM:
            return _PHONE_REGION_NORM[key]
    return None


def phone_split(raw: Any, region: "str | None") -> "dict | None":
    """Split a phone/fax string into {country, area, number, extension} with
    libphonenumber. Returns None when the number cannot be parsed, so the caller
    falls back to the legacy tokeniser (no regression on unparseable input).

    An explicit ``+``/``00`` country code is trusted OVER the region hint — a
    US-registered supplier can legitimately carry a ``+972`` number — because
    libphonenumber ignores the region when the string is already international.
    """
    try:
        import phonenumbers  # lazy: engine stays importable without the package
    except Exception:  # noqa: BLE001
        return None
    s = str(raw or "").strip()
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None

    def _try(text, reg):
        try:
            return phonenumbers.parse(text, reg)
        except Exception:  # noqa: BLE001
            return None

    # Two readings of the string:
    #  * region_cand — parsed as dialled in the row's country. Keeps a leading
    #    group as the AREA code (Brazil's DDD 55, a US area code), which is right
    #    when the number does NOT carry a country code.
    #  * intl_cand — only when the bare digits (no + / 00) START with the region's
    #    own calling code: re-read as international so that embedded code is
    #    stripped off the subscriber number instead of kept in it. This is what
    #    "5515981205351" (55 + 15 + 981205351) needs.
    region_cand = _try(s, region)
    intl_cand = None
    lead = s.lstrip()
    if region and not lead.startswith("+") and not lead.startswith("00"):
        try:
            cc = phonenumbers.country_code_for_region(region)
        except Exception:  # noqa: BLE001
            cc = None
        if cc and digits.startswith(str(cc)) and len(digits) > len(str(cc)) + 4:
            intl_cand = _try("+" + digits, None)
    if region_cand is None and intl_cand is None:
        return None

    def _valid(o):
        try:
            return o is not None and phonenumbers.is_valid_number(o)
        except Exception:  # noqa: BLE001
            return False

    def _possible(o):
        try:
            return o is not None and phonenumbers.is_possible_number(o)
        except Exception:  # noqa: BLE001
            return False

    def _has_area(o):
        try:
            return o is not None and phonenumbers.length_of_geographical_area_code(o) > 0
        except Exception:  # noqa: BLE001
            return False

    # Prefer a reading that carries a geographical AREA CODE, region reading FIRST so a
    # genuine local area code equal to the calling code (Brazil DDD 55) is never
    # mistaken for a country code; then plain VALID; then POSSIBLE (international first).
    best = next((o for o in (region_cand, intl_cand) if _valid(o) and _has_area(o)), None)
    if best is None:
        best = next((o for o in (region_cand, intl_cand) if _valid(o)), None)
    if best is None:
        best = next((o for o in (intl_cand, region_cand) if _possible(o)), None)
    if best is None:
        return None
    try:
        nsn = phonenumbers.national_significant_number(best)
    except Exception:  # noqa: BLE001
        nsn = str(getattr(best, "national_number", "") or "")
    try:
        aclen = phonenumbers.length_of_geographical_area_code(best)
    except Exception:  # noqa: BLE001
        aclen = 0
    area = nsn[:aclen] if aclen and aclen > 0 else ""
    number = nsn[aclen:] if aclen and aclen > 0 else nsn
    # DELIMITER FALLBACK for the area code. libphonenumber only yields a geographical
    # area code for a number it considers VALID for its country; a fax whose subscriber
    # part is the wrong length (a Changzhou number typed "0086-519-8776134", where
    # 8776134 is 7 digits not the 8 the CN plan expects) comes back area-less with the
    # whole national number in the subscriber field. But the analyst SPELLED the split
    # out with dashes/spaces — 0086 | 519 | 8776134 — so when the area is still empty,
    # recover it from those groups: the group AFTER the country-code group is the area,
    # the remainder is the subscriber number. Guarded to ≥3 delimiter-separated digit
    # groups led by the country code, so an unbroken string is never mis-split and a
    # reading libphonenumber already resolved (area non-empty) is left untouched.
    if not area:
        groups = [g for g in re.split(r"\D+", s) if g]
        cc_str = str(best.country_code)
        if len(groups) >= 3 and groups[0] in ("00" + cc_str, "0" + cc_str, cc_str):
            cand_area, cand_number = groups[1], "".join(groups[2:])
            if cand_area and cand_number:
                area, number = cand_area, cand_number
    ext = getattr(best, "extension", "") or ""
    return {"country": str(best.country_code), "area": area,
            "number": number, "extension": ext}
