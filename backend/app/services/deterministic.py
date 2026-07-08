"""Deterministic (no-LLM) resolvers to cut AI/token usage.

These handle the common, well-defined normalization domains — country → ISO,
currency → ISO, unit of measure → Oracle code, Y/N flags, plus fuzzy matching —
with plain Python + curated maps. The AI services call these FIRST and only fall
back to the LLM for the residual values/columns that stay unresolved, so most
runs make no LLM call at all.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").strip().lower())


# ── Country name → ISO-3166 alpha-2 ─────────────────────────────────────────
COUNTRY_TO_ISO = {
    "afghanistan": "AF", "albania": "AL", "algeria": "DZ", "argentina": "AR",
    "australia": "AU", "austria": "AT", "bahrain": "BH", "bangladesh": "BD",
    "belgium": "BE", "bolivia": "BO", "brazil": "BR", "brasil": "BR",
    "bulgaria": "BG", "cambodia": "KH", "canada": "CA", "chile": "CL",
    "china": "CN", "colombia": "CO", "costarica": "CR", "croatia": "HR",
    "cyprus": "CY", "czechia": "CZ", "czechrepublic": "CZ", "denmark": "DK",
    "dominicanrepublic": "DO", "ecuador": "EC", "egypt": "EG", "elsalvador": "SV",
    "estonia": "EE", "finland": "FI", "france": "FR", "germany": "DE",
    "deutschland": "DE", "ghana": "GH", "greece": "GR", "guatemala": "GT",
    "honduras": "HN", "hongkong": "HK", "hungary": "HU", "iceland": "IS",
    "india": "IN", "indonesia": "ID", "iran": "IR", "iraq": "IQ",
    "ireland": "IE", "israel": "IL", "italy": "IT", "italia": "IT",
    "jamaica": "JM", "japan": "JP", "jordan": "JO", "kazakhstan": "KZ",
    "kenya": "KE", "kuwait": "KW", "latvia": "LV", "lebanon": "LB",
    "lithuania": "LT", "luxembourg": "LU", "malaysia": "MY", "malta": "MT",
    "mexico": "MX", "morocco": "MA", "netherlands": "NL", "holland": "NL",
    "newzealand": "NZ", "nigeria": "NG", "norway": "NO", "oman": "OM",
    "pakistan": "PK", "panama": "PA", "paraguay": "PY", "peru": "PE",
    "philippines": "PH", "poland": "PL", "portugal": "PT", "puertorico": "PR",
    "qatar": "QA", "romania": "RO", "russia": "RU", "russianfederation": "RU",
    "saudiarabia": "SA", "serbia": "RS", "singapore": "SG", "slovakia": "SK",
    "slovenia": "SI", "southafrica": "ZA", "southkorea": "KR",
    "korea": "KR", "korearepublicof": "KR", "spain": "ES", "espana": "ES",
    "srilanka": "LK", "sweden": "SE", "switzerland": "CH", "taiwan": "TW",
    "tanzania": "TZ", "thailand": "TH", "tunisia": "TN", "turkey": "TR",
    "turkiye": "TR", "uae": "AE", "unitedarabemirates": "AE", "uganda": "UG",
    "ukraine": "UA", "unitedkingdom": "GB", "uk": "GB", "greatbritain": "GB",
    "england": "GB", "unitedstates": "US", "unitedstatesofamerica": "US",
    "usa": "US", "us": "US", "uruguay": "UY", "venezuela": "VE",
    "vietnam": "VN", "vietnamsocialistrepublicof": "VN", "yemen": "YE",
    "zambia": "ZM", "zimbabwe": "ZW",
}
_ISO_SET = set(COUNTRY_TO_ISO.values())

# ── Currency name/synonym → ISO 4217 ────────────────────────────────────────
CURRENCY = {
    "usd": "USD", "usdollar": "USD", "dollar": "USD", "dollars": "USD",
    "usdollars": "USD", "eur": "EUR", "euro": "EUR", "euros": "EUR",
    "gbp": "GBP", "pound": "GBP", "poundsterling": "GBP", "sterling": "GBP",
    "cad": "CAD", "canadiandollar": "CAD", "aud": "AUD", "australiandollar": "AUD",
    "brl": "BRL", "real": "BRL", "brazilianreal": "BRL", "reais": "BRL",
    "cny": "CNY", "rmb": "CNY", "yuan": "CNY", "renminbi": "CNY",
    "jpy": "JPY", "yen": "JPY", "inr": "INR", "rupee": "INR", "rupees": "INR",
    "mxn": "MXN", "peso": "MXN", "chf": "CHF", "swissfranc": "CHF",
    "sek": "SEK", "nok": "NOK", "dkk": "DKK", "sgd": "SGD", "hkd": "HKD",
    "aed": "AED", "dirham": "AED", "sar": "SAR", "riyal": "SAR",
    "ils": "ILS", "shekel": "ILS", "zar": "ZAR", "rand": "ZAR",
    "pln": "PLN", "zloty": "PLN", "huf": "HUF", "forint": "HUF",
    "try": "TRY", "lira": "TRY", "krw": "KRW", "won": "KRW",
}
_CUR_SET = set(CURRENCY.values())

# ── Unit of measure → Oracle UOM code ───────────────────────────────────────
UOM = {
    "each": "EA", "ea": "EA", "unit": "EA", "units": "EA", "pieces": "EA",
    "piece": "EA", "pcs": "EA", "pc": "EA", "kilogram": "KG", "kilograms": "KG",
    "kg": "KG", "kgs": "KG", "gram": "GR", "grams": "GR", "g": "GR",
    "pound": "LB", "pounds": "LB", "lb": "LB", "lbs": "LB", "ton": "TON",
    "tonne": "TON", "metricton": "TON", "meter": "M", "metre": "M", "m": "M",
    "centimeter": "CM", "cm": "CM", "millimeter": "MM", "mm": "MM",
    "foot": "FT", "feet": "FT", "ft": "FT", "inch": "IN", "inches": "IN",
    "in": "IN", "liter": "LT", "litre": "LT", "liters": "LT", "l": "LT",
    "gallon": "GAL", "gallons": "GAL", "gal": "GAL", "hour": "HR",
    "hours": "HR", "hr": "HR", "hrs": "HR", "day": "DAY", "days": "DAY",
    "box": "BOX", "boxes": "BOX", "case": "CS", "cases": "CS", "dozen": "DZ",
    "pallet": "PAL", "roll": "ROL", "set": "SET", "pair": "PR", "pairs": "PR",
}
_UOM_SET = set(UOM.values())

# ── Boolean / Y-N flag synonyms ─────────────────────────────────────────────
_TRUE = {"yes", "y", "true", "t", "1", "x", "on", "active", "enabled"}
_FALSE = {"no", "n", "false", "f", "0", "off", "inactive", "disabled"}


def _fuzzy(nv: str, table: dict) -> Optional[str]:
    best, ratio = None, 0.0
    for k, code in table.items():
        r = SequenceMatcher(None, nv, k).ratio()
        if r > ratio:
            best, ratio = code, r
    return best if ratio >= 0.90 else None


def _domain_for(field_name: str, description: Optional[str]) -> Optional[str]:
    """Infer the normalization domain from the target field's name/description."""
    t = f"{field_name or ''} {description or ''}".lower()
    if re.search(r"\bcountr", t):
        return "country"
    if re.search(r"currenc", t):
        return "currency"
    if re.search(r"\buom\b|unit of measure|\bunit\b", t):
        return "uom"
    return None


def _resolve_one(domain: str, value: str) -> Optional[str]:
    nv = _norm(value)
    if not nv:
        return None
    if domain == "country":
        if value.strip().upper() in _ISO_SET:
            return value.strip().upper()  # already an ISO code
        return COUNTRY_TO_ISO.get(nv) or _fuzzy(nv, COUNTRY_TO_ISO)
    if domain == "currency":
        if value.strip().upper() in _CUR_SET:
            return value.strip().upper()
        return CURRENCY.get(nv) or _fuzzy(nv, CURRENCY)
    if domain == "uom":
        if value.strip().upper() in _UOM_SET:
            return value.strip().upper()
        return UOM.get(nv) or _fuzzy(nv, UOM)
    return None


def deterministic_crosswalk(
    field_name: str, description: Optional[str], values: list[str],
) -> dict[str, str]:
    """Return {source_value: fusion_code} for EVERY value resolvable WITHOUT the
    LLM — including already-valid values (code == value), so the caller can mark
    them valid and NOT send them to AI. Values the resolver can't place are simply
    absent; the caller falls back to AI for just those."""
    out: dict[str, str] = {}
    domain = _domain_for(field_name, description)
    # Value-set boolean detection: if EVERY distinct value is a yes/no synonym,
    # normalize to Y/N regardless of the field name (covers the many flag fields).
    normset = {_norm(v) for v in values if _norm(v)}
    if normset and normset <= (_TRUE | _FALSE):
        for v in values:
            out[v] = "Y" if _norm(v) in _TRUE else "N"
        return out
    if not domain:
        return out
    for v in values:
        code = _resolve_one(domain, v)
        if code:
            out[v] = code
    return out
