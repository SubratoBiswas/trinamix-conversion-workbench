"""Country name -> ISO-3166 alpha-2 crosswalk. Relocated VERBATIM out of
app.services.deterministic so the domain owns this reference data and the geo rule
strategies (COUNTRY_ISO2 / CITY_COUNTRY_KEY) can use it without importing the service
layer (which would invert the dependency rule). services.deterministic now re-imports
these names, so its own callers — and hdl_output_service — are unchanged.

Keys are punctuation/space-stripped, lower-cased country names; values are ISO codes.
``_ISO_SET`` is the set of valid codes, used to pass an already-valid code through
untouched. No framework imports; pure data."""
from __future__ import annotations


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
