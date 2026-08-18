"""Country/geo rule strategies migrated out of engine._apply_one_rule. The ISO
crosswalk now lives in app.domain.geo.country; these compose it. Each reproduces its
former branch VERBATIM (``cfg`` -> ``config``; the lazy service import replaced by the
domain import above), so behaviour is byte-identical."""
from __future__ import annotations
import re
from typing import Any

from app.domain.text import to_str as _to_str
from app.domain.geo.country import COUNTRY_TO_ISO, _ISO_SET


class CountryIso2Rule:
    rule_type = "COUNTRY_ISO2"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        # Resolve a country NAME to its 2-character ISO 3166-1 alpha-2 code
        # (United States -> US, Italy -> IT). This is a lookup, never a truncation:
        # slicing "United States" to 2 chars gives "Un". Reuses the curated
        # COUNTRY_TO_ISO table (plus its fuzzy fallback) already used by the value
        # crosswalk service, so the two layers cannot disagree. A value that is
        # already a valid 2-char code passes through unchanged; anything
        # unresolvable is left AS-IS rather than guessed, so a bad country is
        # visible in review instead of silently becoming a wrong code.
        raw = _to_str(value).strip()
        if not raw:
            return ""
        if len(raw) == 2 and raw.upper() in _ISO_SET:
            return raw.upper()
        key = "".join(ch for ch in raw.lower() if ch.isalnum())
        return COUNTRY_TO_ISO.get(key, raw)


class CityCountryKeyRule:
    rule_type = "CITY_COUNTRY_KEY"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        # Supplier Site: a 2-character ISO country code, a hyphen, and the city.
        #   {"country_column": "Country Code", "city_column": "City",
        #    "separator": "-", "resolve_country_from_city": true}
        #
        # Analyst, 30-Jul: "if no city, keep just the country code, if no country
        # code but there is city, just mention city" and then "if no country code,
        # fill in country code based on the city".
        #
        # So this is a join with two asymmetric fallbacks, which is why it is a rule
        # type rather than a CONCAT flag: CONCAT can drop a blank part, but it
        # cannot go and FIND the missing one.
        #
        # The country is resolved, in order:
        #   1. the row's own country column;
        #   2. the city -> code index built from the rest of THIS extract, which is
        #      free, needs no model, and is the best possible evidence — the file
        #      already says which country its own cities are in. Ambiguous cities
        #      (New York appears as US 48 times and CN once) take the majority;
        #   3. whatever the index was seeded with from a prior AI resolution.
        # If none of them answers, the city alone is the key, which is the analyst's
        # stated fallback and is never worse than a dangling separator.
        sep = cfg.get("separator", "-")
        row = row or {}

        def _first(spec):
            """First non-blank value across the candidate column names.

            country_column / city_column accept a STRING or a LIST, because the
            column a sheet actually carries is not always the one the analyst named.
            The site sheet is routed to whichever bound source supplies most of its
            mapped columns, and that frame turned out to hold "Billing Country Code"
            / "Shipping Country Code" rather than the plain "Country Code" the rule
            asked for — so the 30-Jul output shipped "Hyderabad" where it should have
            said "IN-Hyderabad". Matching is case- and punctuation-insensitive for
            the same reason "Internal Id" vs "Internal ID" cost the whole parent
            lookup.
            """
            names = spec if isinstance(spec, (list, tuple)) else [spec]
            by_norm = {re.sub(r"[^a-z0-9]", "", str(k).lower()): k for k in row}
            for n in names:
                key = by_norm.get(re.sub(r"[^a-z0-9]", "", str(n or "").lower()))
                if key is None:
                    continue
                v = _to_str(row.get(key, "")).strip()
                if v:
                    return v
            return ""

        cc = _first(cfg.get("country_column") or "")
        # A FIXED prefix wins over any resolved country. Analyst, 10-Aug: Supplier Site
        # = "US-<City>", US a literal on EVERY row — all suppliers load into the NX US
        # BU regardless of their own country. Set via ``country_value`` so the rest of
        # the rule (city case-collapse, blank-city fallback) is unchanged.
        _fixed_cc = cfg.get("country_value") or cfg.get("fixed_country")
        if _fixed_cc:
            cc = _to_str(_fixed_cc).strip()
        city = _first(cfg.get("city_column") or "")
        # With a fixed prefix and NO city, ship the bare prefix ("US"). Analyst, 10-Aug:
        # a city-less supplier site is just "US". (Earlier this kept the incoming value to
        # avoid several city-less sites collapsing to one "US" key, but the country name
        # it fell back to — "Brazil", "India" — did not match the US-<City> convention and
        # the analyst chose the bare prefix instead; sites that need to stay distinct
        # carry a city.) Only for the fixed-prefix case — a column-derived country with no
        # city still returns the incoming value below, unchanged.
        if _fixed_cc and not city:
            return cc
        # Collapse capitalisation variants onto the spelling this extract uses most,
        # because the site key is REQUIRED and UNIQUE: "IN-Hyderabad" appeared 461
        # times and "IN-HYDERABAD" 103, and Fusion would have created two sites for
        # one. 427 keys collided this way. Analyst, 30-Jul: "Keep it IN-Hyderabad
        # for now."
        if city:
            city = ((ctx or {}).get("city_case") or {}).get(
                re.sub(r"[^a-z0-9]", "", city.lower()), city)
        if not cc and city and cfg.get("resolve_country_from_city"):
            idx = (ctx or {}).get("city_country") or {}
            cc = idx.get(re.sub(r"[^a-z]", "", city.lower()), "")
        # BU(2-letter country code). The key is meant to read "US-Texas", but a source
        # whose country column holds the full NAME ("United States") shipped
        # "United States-Texas" — the exact PROC-03 report. Normalise the resolved
        # country to its ISO 3166-1 alpha-2 code, reusing the same COUNTRY_TO_ISO table
        # COUNTRY_ISO2 uses so the layers cannot disagree. A value already a valid
        # 2-char code passes through (upper-cased); an unresolvable one is left as-is so
        # a bad country is visible rather than silently wrong. Opt out with
        # country_to_iso:false for a source with bespoke codes.
        if cc and cfg.get("country_to_iso", True):
            if len(cc) == 2 and cc.upper() in _ISO_SET:
                cc = cc.upper()
            else:
                _isok = "".join(ch for ch in cc.lower() if ch.isalnum())
                cc = COUNTRY_TO_ISO.get(_isok, cc)
        # BU(country code): optionally map the resolved code through a lookup before
        # joining, so Supplier Site becomes "<BU>-City" (e.g. US -> US-PROC) instead
        # of the raw "<code>-City". The lookup is case/punctuation-insensitive on the
        # key; a code not in the map falls through unchanged, so a partial map still
        # ships the raw code rather than a blank. Empty/absent map = old behaviour.
        cmap = cfg.get("country_map") or cfg.get("bu_map")
        if cc and isinstance(cmap, dict) and cmap:
            _cm = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in cmap.items()}
            cc = _to_str(_cm.get(re.sub(r"[^a-z0-9]", "", cc.lower()), cc))
        parts = [p for p in (cc, city) if p]
        if not parts:
            # Neither column had anything — which is ALSO what it looks like when
            # the rule is pointed at columns this extract does not have. Falling
            # back to the incoming value keeps that misconfiguration visible
            # instead of silently blanking a column something else had populated.
            # 8,561 rows once shipped a literal "-" into this required unique key
            # for exactly that reason; the guard is kept, not lost in the rewrite.
            return value
        return sep.join(parts)
