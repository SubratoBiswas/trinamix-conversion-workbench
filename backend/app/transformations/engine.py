"""Transformation rule engine.

Each rule has a `rule_type` and a `config` dict. Rules execute serially over
either a single value (per-cell) or a row dict (for rules that pull other
columns: CONCAT, COALESCE, CONDITIONAL, CASE_WHEN). Some rules also need a
broader runtime context (row index, current user, today's date, named
crosswalks) — that's the optional ``ctx`` argument.

Adding a rule type
------------------

* Implement the branch in ``apply_rule``.
* Add the string to ``RULE_TYPES`` in ``app/models/transformation.py``.
* Add a default config + a typed form on the frontend
  ``TransformationStudioPage``. The form contributes the same JSON the engine
  consumes here.
"""
from __future__ import annotations

import re
from typing import Any

from app.domain.text import (
    to_str as _to_str, is_blank as _is_blank,
    to_float as _to_float, TRUEISH as _TRUEISH, FALSEISH as _FALSEISH,
)
from app.domain.rules.registry import standard_rule_engine
from app.domain.rules.context import (
    _PLACEHOLDER, _interpolate, _COMPARISON_OPS,
    _resolve_column, _row_value_ci, _branch_holds,
)

# Phase 1c: rule types migrated to app.domain.rules dispatch through this registry;
# the rest keep their if/elif branch below. Adding a type is one class + one register().
_RULE_REGISTRY = standard_rule_engine()


# _TRUEISH / _FALSEISH moved to app.domain.text (imported above as aliases); kept so
# _COMPARISON_OPS' istrue/isfalse still read them.
#
# Phase 1c (date-ops): the output spelling (_OUT_DATE_FORMAT), the Oracle-token
# translator (_oracle_date_to_py) and the forgiving parser (_parse_any_date), along
# with the four date rule types that used them (FORMAT_DATE / DATE_FORMAT /
# CONDITIONAL_DATE / COMPUTED), now live in app.domain (dates.fbdi_date +
# rules.library.date_ops). engine no longer owns any date-format knowledge.


# ── Phone parsing (PHONE_PART) ────────────────────────────────────────────────
# A phone with no international prefix ("5515981205351") cannot be split on its
# own: libphonenumber (and the old tokeniser) cannot tell a country code from an
# area code on a bare national string, so the legacy code shipped the WHOLE
# number — country + area + subscriber — in the "number" part. Reported 10-Aug
# (NextPower Supplier): "The Phone field should contain only the phone number,
# not the country code and area code." The row's Country column is the missing
# hint: it fixes the region so the number can be parsed and split properly.
#
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


def _phone_region_for(row: Any) -> str | None:
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


def _phone_split(raw: Any, region: str | None) -> dict | None:
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


def apply_rule(
    rule_type: str,
    config: dict[str, Any],
    value: Any,
    row: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
) -> Any:
    """One rule, plus any rules chained after it in ``config["then"]``.

    The chain exists because THE STORE HOLDS ONE RULE PER FIELD. That is the
    point of the one dated store — a field has a single live answer, so "newest
    wins" is decidable — but an analyst's single sentence is not always a single
    rule type: "concatenate entityid and internalid, and add _B on a billing row"
    is one instruction that needs a CONCAT and then a SUFFIX_WHEN. Split across
    two entries the store could keep only one of them, and the field would ship
    half its key. Chained inside one config, one entry carries the whole sentence.
    """
    out = _apply_one_rule(rule_type, config, value, row=row, ctx=ctx)
    for nxt in ((config or {}).get("then") or []):
        if isinstance(nxt, dict) and nxt.get("rule_type"):
            out = apply_rule(nxt["rule_type"], nxt.get("config") or {}, out,
                             row=row, ctx=ctx)
    return out


def _apply_one_rule(
    rule_type: str,
    config: dict[str, Any],
    value: Any,
    row: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
) -> Any:
    rt = (rule_type or "").upper().strip()
    cfg = config or {}
    ctx = ctx or {}

    # Migrated rule types (Phase 1c) dispatch to app.domain.rules strategies; every
    # other type falls through to the if/elif below until it is migrated too.
    if rt in _RULE_REGISTRY:
        return _RULE_REGISTRY.apply(rt, cfg, value, row, ctx)

    if rt == "PHONE_STRIP_AREA":
        # Oracle stores Area Code and Phone Number in SEPARATE columns. When the
        # extract already has the area code in its own column, leaving it on the
        # front of the number duplicates it (e.g. area 512 + number "512-555-0134"
        # loads as "512 512-555-0134"). Strip it — but ONLY when the number really
        # begins with that area code, so a number that was already clean, or one
        # that happens to start with the same digits by coincidence of formatting,
        # is left alone. Digits-only comparison, original formatting preserved on
        # whatever remains. cfg: {"area_code_column": "<name>"}.
        col = cfg.get("area_code_column")
        raw = _to_str(value).strip()
        if row is None or not col or not raw:
            return value
        area = "".join(ch for ch in _to_str(row.get(col, "")) if ch.isdigit())
        if not area:
            return value
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits.startswith(area) or len(digits) <= len(area):
            return value          # not a duplicated prefix — leave untouched
        # Walk the original string and drop the leading separators + area digits.
        seen = 0
        for i, ch in enumerate(raw):
            if ch.isdigit():
                seen += 1
                if seen == len(area):
                    return raw[i + 1:].lstrip(" -.()/").strip()
        return value

    if rt == "COUNTRY_ISO2":
        # Resolve a country NAME to its 2-character ISO 3166-1 alpha-2 code
        # (United States -> US, Italy -> IT). This is a lookup, never a truncation:
        # slicing "United States" to 2 chars gives "Un". Reuses the curated
        # COUNTRY_TO_ISO table (plus its fuzzy fallback) already used by the value
        # crosswalk service, so the two layers cannot disagree. A value that is
        # already a valid 2-char code passes through unchanged; anything
        # unresolvable is left AS-IS rather than guessed, so a bad country is
        # visible in review instead of silently becoming a wrong code.
        from app.services.deterministic import COUNTRY_TO_ISO, _ISO_SET
        raw = _to_str(value).strip()
        if not raw:
            return ""
        if len(raw) == 2 and raw.upper() in _ISO_SET:
            return raw.upper()
        key = "".join(ch for ch in raw.lower() if ch.isalnum())
        return COUNTRY_TO_ISO.get(key, raw)

    if rt == "CITY_COUNTRY_KEY":
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
            from app.services.deterministic import COUNTRY_TO_ISO, _ISO_SET
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

    if rt == "SELF_LOOKUP":
        # Supplier correction 30-Jul: "for Parent Supplier — get the Parent Vendor Id
        # and then get the value for that ID from Internal Id, and then populate the
        # name." A self-join: the value in THIS row's key column identifies ANOTHER row
        # in the same extract, and a column of that row is what belongs here.
        #   {"key_column": "Parent Vendor Id", "match_column": "Internal Id",
        #    "value_column": "Name"}
        # The index is built once per generation and handed in via ctx, because doing
        # it per row is O(n squared) — 7,495 vendors would be 56 million comparisons.
        cfg_key = cfg.get("key_column")
        # PROC-01 Gap B: resolve the key column case/space-insensitively — the config
        # says "Parent Vendor Id", the extract says parent_vendor_id.
        want = _to_str(_row_value_ci(row, cfg_key) if row is not None else value).strip()
        if not want:
            return cfg.get("default", "")
        index = (ctx.get("self_index") or {}).get(
            f"{cfg.get('match_column')}->{cfg.get('value_column')}")
        if index is None:
            # No index available (preview, or a caller that did not build one).
            # Returning the raw id would ship an id where a NAME belongs and look
            # populated — blank is the honest answer.
            return cfg.get("default", "")
        return index.get(want, cfg.get("default", ""))

    if rt == "CROSS_CONVERSION_LOOKUP":
        # SELF_LOOKUP across conversions: resolve a value from ANOTHER conversion of
        # the same project. Same shape as SELF_LOOKUP, but the index is built from the
        # referenced conversion's source rather than this one's — so e.g. Parent
        # Supplier Name can pull legal_name from the Suppliers conversion when this
        # file only carries the parent's id.
        #   {"ref_conversion_id": "<id>", "key_column": "<this row's key>",
        #    "match_column": "<other conv's key col>",
        #    "value_column": "<other conv's value col>", "default": ""}
        # The index is built once per generation and handed in via ctx.cross_index,
        # keyed "<ref_conversion_id>:<match>-><value>", exactly as self_index is — a
        # per-row scan of another whole extract would be O(n*m).
        want = _to_str(row.get(_resolve_column(cfg.get("key_column"), row), "")
                       if row else value).strip()
        if not want:
            return cfg.get("default", "")
        ref = (cfg.get("ref_conversion_id") or cfg.get("ref_conversion")
               or cfg.get("conversion_id") or "")
        index = (ctx.get("cross_index") or {}).get(
            f"{ref}:{cfg.get('match_column')}->{cfg.get('value_column')}")
        if index is None:
            # No index (preview, an unbuilt/unknown reference). Blank is the honest
            # answer — an id where a name belongs looks populated but is wrong.
            return cfg.get("default", "")
        v = index.get(want)
        if v is None and want.endswith(".0"):
            v = index.get(want[:-2])
        return v if v is not None else cfg.get("default", "")

    if rt == "GROUP_FIRST_FLAG":
        # "group by <key>, mark the FIRST row of each group with <flag>, blank the
        # rest" — e.g. Identifying Address = Y on the first billing-address row per
        # entityid (the identifying/primary address), blank on that customer's other
        # addresses. Which row is "first" is decided ONCE over the whole extract
        # (first appearance), handed in via ctx.group_first_index keyed exactly like
        # sequence_index — because it cannot be known from a single row, and a
        # per-chunk index would flag one row per chunk instead of one per customer.
        #   {"key_column": "entityid", "flag": "Y", "default": ""}
        key_spec = cfg.get("key_column")
        flag = cfg.get("flag", "Y")
        default = cfg.get("default", "")
        if not key_spec or row is None:
            return default
        col = _resolve_column(key_spec, row)
        kv = _to_str(row.get(col)).strip() if col else ""
        if not kv:
            return default
        table = (ctx.get("group_first_index") or {}).get(
            re.sub(r"[^a-z0-9]", "", str(col or "").lower())) or {}
        first_idx = table.get(kv)
        if first_idx is None and kv.endswith(".0"):
            first_idx = table.get(kv[:-2])
        if first_idx is None:
            return default
        return flag if int(ctx.get("row_index", 0) or 0) == int(first_idx) else default

    if rt == "SEQUENCE":
        # CW #23: a unique running key — NXT000001, and a "_C1" form for a PERSON.
        #   {"prefix": "NXT", "width": 6, "start": 1, "preserve_source": true,
        #    "variant": {"if_column": "Party Type", "op": "eq", "value": "PERSON",
        #                "suffix": "_C{n}", "width": 5, "counter": 1}}
        #
        # Derived from the ROW INDEX, not a running counter: the value has to be
        # stable for a given row across re-runs, or regenerating the file renumbers
        # every party and breaks the references the other 18 Customer sheets carry.
        #
        # SECTION 10.6 APPLIES, and this is the rule that section was written about.
        # Auto-generated key numbers were removed once before because a manufactured
        # unique value makes genuine duplicates look distinct, and they then load
        # twice. Two things keep that from recurring and both matter: this runs at
        # finalize, AFTER duplicate decisions have dropped the rows that must not
        # ship; and a field carrying a SEQUENCE must never be used as a
        # duplicate-identity column — the natural key is.
        if cfg.get("preserve_source", True) and not _is_blank(value):
            # A real source key always beats a manufactured one.
            return value
        # ``key_column`` — "unique sequence ON THE BASIS OF entityid" (analyst,
        # 03-Aug). Without it the number comes from the row index, so a customer
        # with five addresses gets five different party numbers and the eighteen
        # other Customer sheets that reference the party stop agreeing with the
        # one that defines it. With it, every row carrying the same key gets the
        # same number, and the count is of DISTINCT keys rather than of rows.
        #
        # The index is built once over the whole extract and handed in through
        # ctx, the same way SELF_LOOKUP's is, because it cannot be computed from
        # one row and a per-chunk index would number the same customer twice.
        idx = int(ctx.get("row_index", 0) or 0)
        key_spec = cfg.get("key_column")
        if key_spec:
            col = _resolve_column(key_spec, row)
            kv = _to_str(row.get(col)).strip() if (col and row is not None) else ""
            table = (ctx.get("sequence_index") or {}).get(
                re.sub(r"[^a-z0-9]", "", str(col or "").lower())) or {}
            ordinal = table.get(kv)
            if ordinal is None and kv.endswith(".0"):
                ordinal = table.get(kv[:-2])
            if ordinal is not None:
                idx = int(ordinal)
            elif table and not kv:
                # The extract HAS the key column and this row's cell is empty.
                # Falling through to the row index would hand this row a number
                # that belongs to whichever customer happens to sit at that
                # position, so leave it blank and let the required-field report
                # show the gap as the data problem it is.
                #
                # Only when the index exists. A key column the extract does not
                # carry at all is a misconfigured rule, not a per-row data gap,
                # and blanking the whole column over it would destroy a value
                # something upstream had already computed. There the rule falls
                # back to the row index — what it did before key_column existed.
                return ""
        n = int(cfg.get("start", 1) or 1) + idx
        prefix = _to_str(cfg.get("prefix", ""))
        width = int(cfg.get("width", 6) or 6)
        suffix = ""
        variant = cfg.get("variant") or {}
        if variant and _branch_holds(variant, value, row):
            if variant.get("width"):
                width = int(variant["width"])
            suffix = _to_str(variant.get("suffix", "")).replace(
                "{n}", str(variant.get("counter", 1)))
        return f"{prefix}{n:0{width}d}{suffix}"

    if rt == "PHONE_PART":
        # Split a single phone/fax string into its Oracle parts. Handles the common
        # legacy forms: "+91 22 1234567", "+1 (415) 555-0100 x23", "0044-20-7946-0000".
        # config: {"part": "country" | "area" | "number" | "extension"}. Deterministic
        # (no per-format regex config needed); unknown/degenerate inputs return "".
        part = (cfg.get("part") or "number").lower()
        raw = _to_str(value).strip()
        if not raw:
            return ""
        # PRIMARY: libphonenumber, with the row's Country column as the region hint.
        # This is what lets a bare "5515981205351" (no + / no separators) be split
        # into +55 / 15 / 981205351 instead of dumping the whole string into the
        # "number" part — the reported 10-Aug defect. Only used when it yields a
        # parseable number; otherwise the legacy tokeniser below runs unchanged, so
        # an unparseable value never regresses.
        _split = _phone_split(raw, _phone_region_for(row))
        if _split is not None:
            return _split.get(part, "")
        # FALLBACK (legacy tokeniser): no region and no international prefix, or an
        # unparseable value. 1) pull an extension off the end, if any.
        ext = ""
        mext = re.search(r"(?i)(?:ext|extn|extension|x)\.?\s*(\d{1,6})\s*$", raw)
        if mext:
            ext = mext.group(1)
            raw = raw[:mext.start()].strip()
        if part == "extension":
            return ext
        has_plus = raw.lstrip().startswith("+") or raw.lstrip().startswith("00")
        # 2) tokenize into digit groups (preserving order); a leading 00 is an
        # international prefix, treat like '+'.
        body = raw.lstrip()
        if body.startswith("00"):
            body = body[2:]
            has_plus = True
        groups = re.findall(r"\d+", body)
        if not groups:
            return ""
        country = area = ""
        rest = list(groups)
        if has_plus:
            country = rest.pop(0)
        if part == "country":
            return country
        # area code = the next group when there are still >=2 groups left (so a
        # bare local number isn't misread as an area code).
        if len(rest) >= 2:
            area = rest.pop(0)
        if part == "area":
            return area
        # number = whatever remains, concatenated.
        return "".join(rest)

    if rt == "CROSSWALK_LOOKUP":
        # Look up ``value`` in a named crosswalk that the caller has loaded
        # into ctx['crosswalks'][<name>] as a {source_value: target_value} dict.
        name = cfg.get("crosswalk")
        default = cfg.get("default", value)
        crosswalks = ctx.get("crosswalks") or {}
        table = crosswalks.get(name) if name else None
        if not table:
            return default
        s = _to_str(value)
        if s in table:
            return table[s]
        # case-insensitive fallback
        lower = {k.lower(): v for k, v in table.items() if isinstance(k, str)}
        return lower.get(s.lower(), default)

    return value


def apply_pipeline(
    rules: list[dict[str, Any]],
    value: Any,
    row: dict[str, Any] | None = None,
    ctx: dict[str, Any] | None = None,
) -> Any:
    out = value
    for r in rules:
        out = apply_rule(
            r.get("rule_type", ""), r.get("config", {}), out, row=row, ctx=ctx
        )
    return out
