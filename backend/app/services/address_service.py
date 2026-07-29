"""US / Canada address validation, normalisation and Oracle-shape conformance.

TWO VALIDATIONS, AND THE SECOND IS THE ONE THAT DECIDES
-------------------------------------------------------
A postal API confirming an address is deliverable does NOT mean Fusion will
accept it. Supplier and customer address loads validate against the TCA
**geography hierarchy** (Country -> State -> County -> City -> Postal), and each
country carries its own validation level. An address USPS blesses still fails if
the pod's hierarchy has no such city, or if the state arrives as "California"
rather than "CA". So this module reports the two independently and never lets a
postal PASS imply an Oracle pass.

WHAT IS IN HERE
---------------
Everything that needs no network: country/state/province normalisation, postal
format checks, and a postal-prefix-vs-region cross-check that catches
transposed or mismatched addresses without calling anyone. That last one is the
highest-yield offline test — "V6B 1A1, Ontario" is wrong and provable from a
table, and it costs nothing per row.

External providers plug in behind ``AddressProvider``. Deliberately an interface
rather than a hardcoded vendor: USPS is not usable here (its SLA restricts the
API to mail and shipping, which excludes ERP master-data cleansing) so the
vendor choice is a procurement decision, and this module must not assume it.

Pure + dependency-light (stdlib + pandas) so every rule is unit-testable without
network, DB or credentials — the same contract as ``cleansing_rules`` and
``decision_engine``.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Protocol

# ── Country ──────────────────────────────────────────────────────────────────
# Oracle wants ISO-3166 alpha-2. §10.10 records Taxpayer Country still shipping
# raw values and needing COUNTRY_ISO2 routing — this is that mapping.
_COUNTRY_ISO2 = {
    "us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "united states": "US", "united states of america": "US", "america": "US",
    "ca": "CA", "can": "CA", "canada": "CA",
}

_US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "AS": "American Samoa", "GU": "Guam", "MP": "Northern Mariana Islands",
    "PR": "Puerto Rico", "VI": "U.S. Virgin Islands",
}

_CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}
# Legacy / French / common variants seen in NetSuite and eBOS extracts.
_CA_ALIASES = {
    "newfoundland": "NL", "labrador": "NL", "nfld": "NL", "nf": "NL",
    "quebec": "QC", "québec": "QC", "que": "QC", "pq": "QC",
    "ontario": "ON", "ont": "ON",
    "british columbia": "BC", "colombie-britannique": "BC",
    "prince edward island": "PE", "pei": "PE",
    "northwest territories": "NT", "nwt": "NT",
    "yukon territory": "YT", "yukon": "YT",
    "nova scotia": "NS", "new brunswick": "NB", "manitoba": "MB",
    "saskatchewan": "SK", "alberta": "AB", "nunavut": "NU",
}

# ── Postal formats ───────────────────────────────────────────────────────────
_US_ZIP = re.compile(r"^(\d{5})(?:-?(\d{4}))?$")
# Canadian postals never use D, F, I, O, Q or U; W and Z never lead.
_CA_POSTAL = re.compile(r"^([ABCEGHJKLMNPRSTVXY])(\d)([ABCEGHJKLMNPRSTVWXYZ])"
                        r"\s*(\d)([ABCEGHJKLMNPRSTVWXYZ])(\d)$")

# First letter of a Canadian postal code -> province. Assigned by Canada Post and
# stable, so a mismatch is a hard, provable defect with no API call.
_CA_PREFIX = {
    "A": {"NL"}, "B": {"NS"}, "C": {"PE"}, "E": {"NB"},
    "G": {"QC"}, "H": {"QC"}, "J": {"QC"},
    "K": {"ON"}, "L": {"ON"}, "M": {"ON"}, "N": {"ON"}, "P": {"ON"},
    "R": {"MB"}, "S": {"SK"}, "T": {"AB"}, "V": {"BC"},
    "X": {"NT", "NU"}, "Y": {"YT"},
}

# US ZIP 3-digit prefix ranges per state (inclusive). Sectional-centre
# allocations; stable enough to flag a state/ZIP contradiction, and deliberately
# a RANGE check rather than a full ZIP database — this is a contradiction
# detector, not a deliverability check.
_US_ZIP_RANGES: dict[str, list[tuple[int, int]]] = {
    "AL": [(350, 369)], "AK": [(995, 999)], "AZ": [(850, 865)],
    "AR": [(716, 729), (755, 755)], "CA": [(900, 961)], "CO": [(800, 816)],
    "CT": [(60, 69)], "DE": [(197, 199)], "DC": [(200, 205), (569, 569)],
    "FL": [(320, 349)], "GA": [(300, 319), (398, 399)], "HI": [(967, 968)],
    "ID": [(832, 838)], "IL": [(600, 629)], "IN": [(460, 479)],
    "IA": [(500, 528)], "KS": [(660, 679)], "KY": [(400, 427)],
    "LA": [(700, 714)], "ME": [(39, 49)], "MD": [(206, 219)],
    "MA": [(10, 27), (55, 55)], "MI": [(480, 499)], "MN": [(550, 567)],
    "MS": [(386, 397)], "MO": [(630, 658)], "MT": [(590, 599)],
    "NE": [(680, 693)], "NV": [(889, 898)], "NH": [(30, 38)],
    "NJ": [(70, 89)], "NM": [(870, 884)],
    # 005 is Holtsville and 063 is Fishers Island — both New York despite sitting
    # inside the Connecticut/Puerto Rico bands. Omitting them flagged real
    # addresses as contradictions.
    "NY": [(90, 149), (5, 6), (63, 63)],
    "NC": [(270, 289)], "ND": [(580, 588)], "OH": [(430, 459)],
    "OK": [(730, 749)], "OR": [(970, 979)], "PA": [(150, 196)],
    "RI": [(28, 29)], "SC": [(290, 299)], "SD": [(570, 577)],
    "TN": [(370, 385)], "TX": [(750, 799), (885, 885)], "UT": [(840, 847)],
    "VT": [(50, 59)], "VA": [(201, 201), (220, 246)], "WA": [(980, 994)],
    "WV": [(247, 268)], "WI": [(530, 549)], "WY": [(820, 831)],
    "PR": [(6, 9)], "VI": [(8, 8)], "GU": [(969, 969)],
    "AS": [(967, 967)], "MP": [(969, 969)],
}

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

_WS = re.compile(r"\s+")


def _n(v: Any) -> str:
    return _WS.sub(" ", "" if v is None else str(v)).strip()


def normalize_country(value: Any) -> Optional[str]:
    """Any spelling of the US or Canada -> ISO-2. None when unrecognised."""
    s = _n(value).casefold()
    if not s:
        return None
    if s in _COUNTRY_ISO2:
        return _COUNTRY_ISO2[s]
    # "U.S.A." must reach "usa". Stripping only a TRAILING dot leaves "u.s.a",
    # which matches nothing — drop every dot and inner space instead.
    bare = s.replace(".", "").replace(" ", "")
    if bare in _COUNTRY_ISO2:
        return _COUNTRY_ISO2[bare]
    up = bare.upper()
    return up if up in {"US", "CA"} else None


def normalize_region(value: Any, country: Optional[str]) -> Optional[str]:
    """State / province -> 2-char code. Accepts the code, the full name, and the
    French and legacy Canadian variants that turn up in NetSuite extracts."""
    s = _n(value)
    if not s:
        return None
    up = s.upper()
    low = s.casefold()
    if country == "CA" or country is None:
        if up in _CA_PROVINCES:
            return up
        if low in _CA_ALIASES:
            return _CA_ALIASES[low]
        for code, name in _CA_PROVINCES.items():
            if name.casefold() == low:
                return code
    if country == "US" or country is None:
        if up in _US_STATES:
            return up
        for code, name in _US_STATES.items():
            if name.casefold() == low:
                return code
    return None


def normalize_postal(value: Any, country: Optional[str]) -> Optional[str]:
    """Postal code in the shape Oracle expects: ``12345`` / ``12345-6789`` for
    the US, ``A1A 1A1`` (single space, upper) for Canada. None if unparseable."""
    s = _n(value).upper().replace("–", "-")
    if not s:
        return None
    if country == "CA" or country is None:
        m = _CA_POSTAL.match(s.replace(" ", ""))
        if m:
            return f"{m[1]}{m[2]}{m[3]} {m[4]}{m[5]}{m[6]}"
    if country == "US" or country is None:
        m = _US_ZIP.match(s.replace(" ", ""))
        if m:
            return f"{m[1]}-{m[2]}" if m[2] else m[1]
    return None


def _issue(field, code, severity, message, suggestion=None):
    return {"field": field, "code": code, "severity": severity,
            "message": message, "suggestion": suggestion}


def validate_address(addr: dict) -> dict:
    """Offline validation of one address.

    ``addr`` keys (any may be missing): line1, line2, city, region, postal,
    country. Returns {normalized, issues[], status}.

    status: "ok" | "warning" | "error". ERROR means the address contradicts
    itself or cannot be represented in Oracle's expected shape — those are
    provable without a provider. WARNING means something is missing or
    unverifiable offline and needs a provider or a human.
    """
    issues: list = []
    country = normalize_country(addr.get("country"))
    raw_country = _n(addr.get("country"))
    if raw_country and not country:
        issues.append(_issue("country", "country_unrecognised", SEVERITY_ERROR,
                             f"{raw_country!r} is not US or Canada — out of scope "
                             "for this validator; Oracle needs an ISO-2 code."))
    elif not raw_country:
        issues.append(_issue("country", "country_missing", SEVERITY_ERROR,
                             "Country is required; Oracle resolves the geography "
                             "hierarchy from it."))

    region = normalize_region(addr.get("region"), country)
    raw_region = _n(addr.get("region"))
    if raw_region and not region:
        issues.append(_issue("region", "region_unrecognised", SEVERITY_ERROR,
                             f"{raw_region!r} is not a valid "
                             f"{'province' if country == 'CA' else 'state'} code or name."))
    elif not raw_region:
        issues.append(_issue("region", "region_missing", SEVERITY_WARNING,
                             "No state/province — Oracle geography validation will "
                             "fail if the country requires one."))
    elif region != raw_region:
        issues.append(_issue("region", "region_normalised", SEVERITY_WARNING,
                             f"{raw_region!r} should be {region!r}.", region))

    postal = normalize_postal(addr.get("postal"), country)
    raw_postal = _n(addr.get("postal"))
    if raw_postal and not postal:
        issues.append(_issue("postal", "postal_malformed", SEVERITY_ERROR,
                             f"{raw_postal!r} is not a valid "
                             f"{'Canadian postal code' if country == 'CA' else 'ZIP code'}."))
    elif not raw_postal:
        issues.append(_issue("postal", "postal_missing", SEVERITY_WARNING,
                             "No postal code."))
    elif postal != raw_postal:
        issues.append(_issue("postal", "postal_normalised", SEVERITY_WARNING,
                             f"{raw_postal!r} should be {postal!r}.", postal))

    # The cross-check: postal codes encode their region, so a disagreement is a
    # hard defect provable from a table — no provider, no cost, no false positive.
    if postal and region:
        if country == "CA":
            allowed = _CA_PREFIX.get(postal[0], set())
            if allowed and region not in allowed:
                issues.append(_issue(
                    "postal", "postal_region_mismatch", SEVERITY_ERROR,
                    f"Postal {postal} belongs to {'/'.join(sorted(allowed))}, "
                    f"not {region}."))
        elif country == "US":
            ranges = _US_ZIP_RANGES.get(region)
            if ranges:
                p3 = int(postal[:3])
                if not any(lo <= p3 <= hi for lo, hi in ranges):
                    issues.append(_issue(
                        "postal", "postal_region_mismatch", SEVERITY_ERROR,
                        f"ZIP {postal[:5]} is outside the ranges assigned to "
                        f"{region}."))

    if not _n(addr.get("line1")):
        issues.append(_issue("line1", "line1_missing", SEVERITY_ERROR,
                             "Address Line 1 is required by every Oracle address "
                             "interface."))
    if not _n(addr.get("city")):
        issues.append(_issue("city", "city_missing", SEVERITY_WARNING,
                             "No city — Oracle geography validation usually needs one."))

    status = (SEVERITY_ERROR if any(i["severity"] == SEVERITY_ERROR for i in issues)
              else SEVERITY_WARNING if issues else "ok")
    return {
        "normalized": {
            "line1": _n(addr.get("line1")), "line2": _n(addr.get("line2")),
            "city": _n(addr.get("city")), "region": region or raw_region,
            "postal": postal or raw_postal, "country": country or raw_country,
        },
        "issues": issues,
        "status": status,
    }


def address_key(addr: dict) -> str:
    """Dedup key for one address.

    Why it matters: a 22,505-row address extract holds far fewer DISTINCT
    addresses, and every provider bills per lookup. Verifying by key and fanning
    the result back out is the difference between a viable spend and an absurd
    one — and it is also what makes a re-run free.
    """
    import hashlib
    parts = [_n(addr.get(k)).casefold() for k in
             ("line1", "line2", "city", "region", "postal", "country")]
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]


def distinct_addresses(rows: Iterable[dict]) -> dict[str, dict]:
    """{address_key: address} — what to actually send to a provider."""
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(address_key(r), r)
    return out


def validate_many(rows: list[dict]) -> dict:
    """Validate a list of addresses, deduplicating first. Returns a summary plus
    per-key results, so the caller can fan results back to every row."""
    uniq = distinct_addresses(rows)
    results = {k: validate_address(a) for k, a in uniq.items()}
    counts = {"ok": 0, SEVERITY_WARNING: 0, SEVERITY_ERROR: 0}
    for r in results.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    by_code: dict[str, int] = {}
    for r in results.values():
        for i in r["issues"]:
            by_code[i["code"]] = by_code.get(i["code"], 0) + 1
    return {
        "rows": len(rows),
        "distinct": len(uniq),
        # The saving that makes a paid provider affordable — worth reporting so
        # the number is visible before anyone signs an order form.
        "lookups_saved": len(rows) - len(uniq),
        "counts": counts,
        "issues_by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
        "results": results,
    }


class AddressProvider(Protocol):
    """External verification (CASS for the US, SERP for Canada).

    An interface, not a vendor: USPS's SLA restricts its API to mail and
    shipping, which excludes ERP master-data cleansing, so the provider is a
    procurement decision this module must not bake in. An implementation takes
    the DEDUPLICATED addresses and returns one verdict per key.
    """

    def verify(self, addresses: dict[str, dict]) -> dict[str, dict]:
        """{key: address} -> {key: {status, normalized, provider_code, raw}}."""
        ...
