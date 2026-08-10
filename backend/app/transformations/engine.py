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
import uuid
from datetime import datetime
from typing import Any


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


# The spellings these extracts actually carry. Shared with MAP_BOOLEAN so a rule
# written one way cannot disagree with a rule written the other.
_TRUEISH = {"yes", "y", "1", "true", "t"}
_FALSEISH = {"no", "n", "0", "false", "f"}

# The output spelling every date rule defaults to. Analyst, 05-Aug: "all dates
# should be yyyy/mm/dd format." A rule that names its own output_format still wins;
# this is the default when none is given, kept in step with
# output_service.FBDI_DATE_FORMAT.
_OUT_DATE_FORMAT = "%Y/%m/%d"


def _is_blank(v: Any) -> bool:
    return v is None or _to_str(v).strip() == ""


# Oracle/ISO date TOKENS (as written in FORMAT_DATE's from_format/to_format, e.g.
# "YYYY-MM-DD HH:MM:SS") translated to Python strftime directives. Longest tokens
# first so YYYY is consumed before YY and HH24 before HH. Only ever applied to an
# OUTPUT format, which is date-only in practice, so MM is month (never the minutes
# spelling some extracts use).
_ORACLE_DATE_TOKENS = [
    ("YYYY", "%Y"), ("YY", "%y"), ("MON", "%b"), ("MONTH", "%B"),
    ("DD", "%d"), ("HH24", "%H"), ("HH", "%H"), ("MI", "%M"), ("SS", "%S"), ("MM", "%m"),
]

# The input spellings a date value actually arrives in. Tried in order; the first
# that parses wins. A FORMAT_DATE rule names a from_format, but extracts are not
# reliably in it (Workday hands "2024-02-12" where the rule says
# "YYYY-MM-DD HH:MM:SS"), so the value is parsed by probing rather than by trusting
# the declared format — the same forgiving approach the date column pass uses.
_DATE_IN_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y",
    "%Y%m%d", "%d-%b-%Y", "%d-%b-%y", "%d-%B-%Y",
)


def _oracle_date_to_py(fmt: Any) -> str | None:
    """An Oracle/ISO date format string -> Python strftime, or None if not given."""
    s = _to_str(fmt).strip()
    if not s:
        return None
    for tok, py in _ORACLE_DATE_TOKENS:
        s = s.replace(tok, py)
    return s


def _parse_any_date(s: str) -> "datetime | None":
    """Parse a date value written in any of the common spellings, else None."""
    s = s.strip()
    if not s:
        return None
    for fmt in _DATE_IN_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ``{Column_Name}`` inside a rule's result string, so a CASE/WHEN "then" can build
# a value out of OTHER columns on the row.
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _interpolate(template: Any, row: Any) -> Any:
    """Substitute ``{Column}`` tokens in a result string with the row's values.

    Reported 05-Aug: a CASE/WHEN branch set to ``E{Employee_ID}`` shipped the
    literal text ``E{Employee_ID}`` — the live preview showed it, and so did the
    file. The analyst means "an E, then this row's Employee_ID": the only way to
    express "prefix the id with a letter that depends on Worker Type" in one rule.
    The engine returned every ``then`` verbatim, so no result value could reference
    another column.

    A token is a column NAME (matched case- and punctuation-loosely, the same way
    the rest of the engine resolves columns), replaced by that column's cell value.
    A token that names no column on the row is left EXACTLY as written — including
    its braces — so a result that legitimately contains braces, or names a column
    this extract does not have, is never silently blanked. Non-strings and rows
    without a lookup pass straight through.
    """
    if row is None or not isinstance(template, str) or "{" not in template:
        return template
    # Resolve each column name once, tolerant of case/spacing/punctuation, because
    # a frame header ("EMPLOYEE_ID") and what the analyst typed ("Employee_ID" or
    # "employee id") routinely differ — the same mismatch _resolve_column handles.
    def _lookup(name: str):
        if row is None:
            return None
        try:
            if name in row:
                return row.get(name)
        except Exception:  # noqa: BLE001 — row may not support `in`
            pass
        want = re.sub(r"[^a-z0-9]", "", name.lower())
        keys = None
        for attr in ("keys", "_keys"):
            fn = getattr(row, attr, None)
            if callable(fn):
                try:
                    keys = list(fn())
                    break
                except Exception:  # noqa: BLE001
                    keys = None
        for k in (keys or []):
            if re.sub(r"[^a-z0-9]", "", str(k).lower()) == want:
                return row.get(k)
        return None

    def _sub(match: "re.Match") -> str:
        raw = match.group(1).strip()
        # Optional modifier: ``{Column|digits}`` inserts only the DIGITS of the cell.
        # Needed for AssignmentNumber: a contingent worker's Employee_ID already
        # carries a letter prefix (C12345), so "C{Employee_ID}" doubled it to CC12345 —
        # the analyst wants "C" + the digits (C12345). Employees (numeric ids) are
        # unaffected. Extensible: |alnum keeps letters+digits, |upper/|lower fold case.
        mod = ""
        name = raw
        if "|" in raw:
            name, mod = raw.split("|", 1)
            name, mod = name.strip(), mod.strip().lower()
        val = _lookup(name)
        if val is None or _to_str(val).strip() == "":
            # Unknown column -> leave the token untouched (do not blank a result).
            # A resolved-but-empty cell -> empty string, so "E{Employee_ID}" on a
            # row with no id becomes "E", which is the sensible reading.
            try:
                if row is not None and (name in row):
                    return ""
            except Exception:  # noqa: BLE001
                pass
            if _lookup(name) is not None:
                return ""
            return match.group(0)
        s = _to_str(val)
        if mod == "digits":
            s = re.sub(r"\D", "", s)
        elif mod == "alnum":
            s = re.sub(r"[^A-Za-z0-9]", "", s)
        elif mod == "upper":
            s = s.upper()
        elif mod == "lower":
            s = s.lower()
        return s

    return _PLACEHOLDER.sub(_sub, template)


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    s = _to_str(v).strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


_COMPARISON_OPS = {
    "eq": lambda a, b: _to_str(a) == _to_str(b),
    "neq": lambda a, b: _to_str(a) != _to_str(b),
    "gt": lambda a, b: (_to_float(a) or 0) > (_to_float(b) or 0),
    "gte": lambda a, b: (_to_float(a) or 0) >= (_to_float(b) or 0),
    "lt": lambda a, b: (_to_float(a) or 0) < (_to_float(b) or 0),
    "lte": lambda a, b: (_to_float(a) or 0) <= (_to_float(b) or 0),
    "in": lambda a, b: _to_str(a) in (b if isinstance(b, (list, tuple)) else _to_str(b).split(",")),
    "notin": lambda a, b: _to_str(a) not in (b if isinstance(b, (list, tuple)) else _to_str(b).split(",")),
    "contains": lambda a, b: _to_str(b) in _to_str(a),
    "startswith": lambda a, b: _to_str(a).startswith(_to_str(b)),
    "endswith": lambda a, b: _to_str(a).endswith(_to_str(b)),
    "regex": lambda a, b: re.search(_to_str(b), _to_str(a)) is not None,
    "isblank": lambda a, _b: _is_blank(a),
    "notblank": lambda a, _b: not _is_blank(a),
    # A boolean-ish column is TRUE/FALSE, not present/absent, and reading one with
    # `notblank` inverts the rule on almost every row. Tax Organization Type is the
    # proof: its branch was {"if_column": "Is Individual", "op": "notblank"}, and in
    # the NetSuite extract that column reads "No" on 6,985 of 7,495 rows and "Yes" on
    # 437. "No" is not blank, so 7,422 suppliers — 99% of them, including "3D Hubs
    # Manufacturing LLC" and "A.B Boyd Co" — were loaded as INDIVIDUAL, and only the
    # 73 rows where the column was EMPTY came out CORPORATION. Exactly backwards.
    #
    # Comparison is trimmed and case-insensitive, and the vocabulary is the one
    # MAP_BOOLEAN already uses, so the two cannot disagree about what "Y" means.
    "istrue": lambda a, _b: _to_str(a).strip().lower() in _TRUEISH,
    "isfalse": lambda a, _b: _to_str(a).strip().lower() in _FALSEISH,
}


def _resolve_column(spec: Any, row: Any) -> Any:
    """The first of ``spec``'s candidate spellings this row actually has.

    A rule column may be written as one name or as a LIST of candidate spellings.
    The reason is in customer_rules_nextpower.json: rules are dictated in prose
    ("entityid + _ + internalid") and the extract's real headers are whatever the
    legacy system exported, so a single guessed spelling binds to nothing and
    fails silently in a file that looks correct. Naming several costs nothing.

    Returns the name to read, or None when the row has none of them — in which
    case the caller reads a blank, which is the same thing an absent column has
    always meant here. Preference order is the order written: a spelling the row
    HAS but leaves blank still beats one it does not have at all, so a genuinely
    empty cell is not silently replaced by a different column's value.
    """
    if not isinstance(spec, (list, tuple)):
        return spec
    names = [c for c in spec if str(c or "").strip()]
    if row is None:
        return names[0] if names else None
    present = [c for c in names if c in row]
    for c in present:
        if not _is_blank(row.get(c)):
            return c
    return present[0] if present else (names[0] if names else None)


def _row_value_ci(row: Any, name: Any) -> Any:
    """This row's value for ``name``, resolving the column case/space-insensitively.

    ``_resolve_column`` returns a single-string spec verbatim, so a config spelling
    ("Parent Vendor Id") that differs from the extract's raw header (parent_vendor_id)
    read a blank — the reason SELF_LOOKUP (Parent Supplier) returned empty on every
    row (PROC-01 Gap B). Try the resolved name, then fall back to a normalised match
    over the row's own keys. Works for a dict or a pandas Series (both expose keys())."""
    if row is None:
        return ""
    col = _resolve_column(name, row)
    if col is not None and col in row:
        return row.get(col)
    target = re.sub(r"[^a-z0-9]", "", str(col if col is not None else name).lower())
    try:
        for k in (row.keys() if hasattr(row, "keys") else row):
            if re.sub(r"[^a-z0-9]", "", str(k).lower()) == target:
                return row.get(k)
    except Exception:  # noqa: BLE001
        pass
    return ""


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


def _branch_holds(br: dict, value: Any, row: Any) -> bool:
    """Does one CASE_WHEN / SUFFIX_WHEN branch fire?

    Two shapes. A plain branch names one column, one op and one value. A branch
    carrying ``all`` is a CONJUNCTION of those — every clause must hold — which
    the analyst's Party Type rule needs and the single-clause form cannot say:
    "if organization name is blank AND a person name is not blank". Written as
    three separate branches instead, the first one to match wins and the
    organization-name test is never reached.
    """
    clauses = br.get("all")
    if isinstance(clauses, (list, tuple)) and clauses:
        return all(_branch_holds(c, value, row) for c in clauses)
    clauses = br.get("any")
    if isinstance(clauses, (list, tuple)) and clauses:
        return any(_branch_holds(c, value, row) for c in clauses)
    cmp = _COMPARISON_OPS.get((br.get("op") or "eq").lower())
    if not cmp:
        return False
    col = _resolve_column(br.get("if_column"), row)
    left = row.get(col) if (col and row is not None) else value
    try:
        return bool(cmp(left, br.get("value")))
    except Exception:                                           # noqa: BLE001
        return False


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

    if rt == "TRIM":
        return _to_str(value).strip()

    if rt == "UPPERCASE":
        return _to_str(value).upper()

    if rt == "LOWERCASE":
        return _to_str(value).lower()

    if rt == "TITLE_CASE":
        return _to_str(value).title()

    if rt == "REMOVE_HYPHEN":
        return _to_str(value).replace("-", "")

    if rt == "REMOVE_SPECIAL_CHARS":
        keep = cfg.get("keep", "")
        pattern = re.compile(rf"[^A-Za-z0-9{re.escape(keep)} ]")
        return pattern.sub("", _to_str(value))

    if rt == "REPLACE":
        find = cfg.get("find", "")
        repl = cfg.get("replace", "")
        return _to_str(value).replace(find, repl)

    if rt == "REGEX_REPLACE":
        pattern = cfg.get("pattern", "")
        # Accept both "replace" and "replacement". The rule-authoring UI and the
        # AI translator have historically emitted "replacement", but only "replace"
        # was read — so a non-empty replacement string was silently dropped and the
        # rule ran as a pure deletion. Reading either key makes the two spellings
        # behave the same instead of one of them quietly doing nothing.
        repl = cfg.get("replace")
        if repl is None:
            repl = cfg.get("replacement", "")
        flags_s = cfg.get("flags", "") or ""
        flags = 0
        if "i" in flags_s.lower():
            flags |= re.IGNORECASE
        if "m" in flags_s.lower():
            flags |= re.MULTILINE
        try:
            return re.sub(pattern, repl, _to_str(value), flags=flags)
        except re.error:
            return value

    if rt == "REGEX_EXTRACT":
        pattern = cfg.get("pattern", "")
        group = int(cfg.get("group", 0))
        try:
            m = re.search(pattern, _to_str(value))
        except re.error:
            return value
        if not m:
            return cfg.get("default", "")
        try:
            return m.group(group)
        except IndexError:
            return cfg.get("default", "")

    if rt == "PAD":
        side = (cfg.get("side") or "left").lower()
        length = int(cfg.get("length", 0))
        char = (cfg.get("char") or "0")[:1] or "0"
        s = _to_str(value)
        if length <= 0 or len(s) >= length:
            return s
        return s.rjust(length, char) if side == "left" else s.ljust(length, char)

    if rt == "SUBSTRING":
        s = _to_str(value)
        start = int(cfg.get("start", 0))
        length = cfg.get("length")
        if length is None or length == "":
            return s[start:]
        try:
            length = int(length)
        except (TypeError, ValueError):
            return s
        return s[start : start + length]

    if rt == "DEFAULT_VALUE":
        return cfg.get("value", "") if _is_blank(value) else value

    if rt == "CONSTANT":
        # Always overwrite with the configured value, regardless of source.
        return cfg.get("value", "")

    if rt == "VALUE_MAP":
        # Direct dict lookup, optionally case-insensitive. Reserved keys
        # (case_insensitive, default) are stripped from the lookup.
        s = _to_str(value)
        case_insensitive = cfg.get("case_insensitive", True)
        default = cfg.get("default")
        # `_prompt` is a reserved META key: the authoring sentence is stashed under it
        # so it travels with the rule, and it must NEVER be read as a from→to pair.
        # Without this a VALUE_MAP that carried its prompt would gain a phantom
        # "_prompt" -> "<the sentence>" mapping.
        mapping = {
            k: v for k, v in cfg.items()
            if k not in ("case_insensitive", "default", "_prompt")
        }
        if case_insensitive:
            for k, v in mapping.items():
                if isinstance(k, str) and k.lower() == s.lower():
                    return v
        else:
            if s in mapping:
                return mapping[s]
        return default if default is not None else value

    if rt == "FORMAT_DATE":
        # Same intent as DATE_FORMAT, but the config is written in Oracle/ISO tokens
        # (from_format / to_format, e.g. "YYYY-MM-DD HH:MM:SS" -> "YYYY/MM/DD"). This
        # rule_type existed on Customer/Employee mappings with NO handler, so it was
        # an unknown rule and passed the value straight through — which is why an
        # Employee EffectiveStartDate shipped "2024-02-12" (hyphens) instead of the
        # "YYYY/MM/DD" its own rule asked for. Parse the value forgivingly (extracts
        # do not honour the declared from_format) and emit the requested output,
        # defaulting to the standard yyyy/mm/dd.
        s = _to_str(value).strip()
        if not s:
            return s
        to_fmt = _oracle_date_to_py(cfg.get("to_format")) or _OUT_DATE_FORMAT
        dt = _parse_any_date(s)
        return dt.strftime(to_fmt) if dt else value

    if rt == "DATE_FORMAT":
        in_fmt = cfg.get("input_format", "%m/%d/%Y")
        # yyyy/mm/dd is the output spelling every file uses now (analyst, 05-Aug:
        # "all dates should be yyyy/mm/dd format"). A rule that names its own
        # output_format still wins — this is only the default when none is given.
        out_fmt = cfg.get("output_format", _OUT_DATE_FORMAT)
        s = _to_str(value).strip()
        if not s:
            return s
        try:
            return datetime.strptime(s, in_fmt).strftime(out_fmt)
        except ValueError:
            return value  # leave for validation to flag

    if rt == "NUMBER_FORMAT":
        decimals = int(cfg.get("decimals", 2))
        s = _to_str(value).strip().replace(",", "")
        if s == "":
            return s
        try:
            return f"{float(s):.{decimals}f}"
        except ValueError:
            return value

    if rt == "ARITHMETIC":
        op = (cfg.get("op") or "round").lower()
        amount = _to_float(cfg.get("amount"))
        decimals = cfg.get("decimals")
        n = _to_float(value)
        if n is None:
            return value
        if op == "add" and amount is not None:
            n = n + amount
        elif op == "subtract" and amount is not None:
            n = n - amount
        elif op == "multiply" and amount is not None:
            n = n * amount
        elif op == "divide" and amount not in (None, 0):
            n = n / amount
        elif op == "abs":
            n = abs(n)
        elif op == "negate":
            n = -n
        if decimals not in (None, ""):
            try:
                return round(n, int(decimals))
            except (TypeError, ValueError):
                return n
        if op == "round":
            return round(n)
        return n

    if rt == "CONCAT":
        sep = cfg.get("separator", " ")
        if not row:
            return value
        # LITERAL SEGMENTS. A CONCAT often ends in a fixed tag — "col1_col2_RS" — and
        # the only way to express it used to be to drop "RS" into `columns`, where it
        # was read as a COLUMN name: `row.get("RS")` is empty, so the tag vanished
        # (and under `require_all` the empty part blanked the whole key). Reported as
        # "the last suffix (_RS) is not reflecting in the rules". A literal piece is
        # now first-class, three compatible ways:
        #   * `parts`: an ordered list of {"col": name} / {"literal": text} — full
        #     control over where literals sit; joined with NO auto-separator so the
        #     literals carry their own (e.g. "_RS"). require_all gates on COLUMN parts.
        #   * `prefix` / `suffix`: literal strings placed around the joined columns —
        #     the simple "append _RS" case without re-modelling the whole rule.
        # The plain `columns` list is unchanged.
        _parts_spec = cfg.get("parts")
        if isinstance(_parts_spec, list) and _parts_spec:
            col_vals: list[str] = []          # the column pieces, for the blank tests
            rendered: list[str] = []          # every piece, in order, for the output
            for seg in _parts_spec:
                if isinstance(seg, dict) and "literal" in seg:
                    rendered.append(_to_str(seg.get("literal", "")))
                else:
                    name = seg.get("col") if isinstance(seg, dict) else seg
                    v = _to_str(row.get(_resolve_column(name, row), ""))
                    col_vals.append(v)
                    rendered.append(v)
            if not any(p.strip() for p in col_vals):
                return value                  # no real column data — see note below
            if cfg.get("require_all") and not all(p.strip() for p in col_vals):
                return ""                     # a half key is a wrong key
            out = "".join(rendered)
            return out
        cols = cfg.get("columns", [])
        parts = [_to_str(row.get(_resolve_column(c, row), "")) for c in cols]
        # A CONCAT whose every input is missing must not emit the separator alone.
        # Supplier Site was configured as CONCAT("Country Code", "-", "City"); neither
        # column exists in the NetSuite extract, so all 8,561 rows shipped the literal
        # "-" — a required, must-be-unique key filled with a guaranteed-invalid,
        # guaranteed-duplicate value. Falling back to the incoming value makes the
        # misconfiguration visible instead of manufacturing a bad key.
        if not any(p.strip() for p in parts):
            return value
        # A HALF key is a wrong key, not a partial one. Supplier Site is
        # "Country Code-City", and City is empty on 1,299 of the 7,495 NetSuite
        # rows — joining regardless emits "US-", a required unique key that is both
        # invalid and duplicated 1,299 times. `require_all` blanks it instead, so
        # the gap shows up in the required-field report as the data problem it is
        # rather than as a value that looks filled in.
        # `omit_blank` is the softer option: join only the parts that have content.
        if cfg.get("require_all") and not all(p.strip() for p in parts):
            return ""
        if cfg.get("omit_blank"):
            kept = [p for p in parts if p.strip()]
            joined = sep.join(kept)
        else:
            joined = sep.join(parts)
        # Literal book-ends. Only wrap when there is real content, so an all-blank
        # CONCAT (handled above) never turns into a bare "PFX-SFX".
        _pfx = _to_str(cfg.get("prefix", ""))
        _sfx = _to_str(cfg.get("suffix", ""))
        return f"{_pfx}{joined}{_sfx}"

    if rt == "SPLIT":
        sep = cfg.get("separator", " ")
        idx = int(cfg.get("index", 0))
        parts = _to_str(value).split(sep)
        return parts[idx] if 0 <= idx < len(parts) else value

    if rt == "COALESCE":
        cols = cfg.get("columns", [])
        if row is not None:
            for c in cols:
                v = row.get(_resolve_column(c, row))
                if not _is_blank(v):
                    return v
        if not _is_blank(value):
            return value
        return cfg.get("default", "")

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

    if rt == "BLANK_IF_EQUALS":
        # Blank this field when it duplicates ANOTHER COLUMN's value. Oracle does
        # not want a redundant alias: NextPower's rule is that Alternate Name is
        # left empty when it is identical to Supplier Name. Row-aware, and the
        # comparison is case/whitespace-insensitive so "ACME Inc" vs "Acme  Inc "
        # is still treated as a duplicate. cfg: {"other_column": "<name>"}.
        other = cfg.get("other_column")
        if row is None or not other:
            return value
        def _norm(v):
            return " ".join(_to_str(v).strip().lower().split())
        return "" if _norm(value) and _norm(value) == _norm(row.get(other, "")) else value

    if rt == "CONDITIONAL":
        # Legacy single-equality conditional kept for back-compat.
        col = cfg.get("if_column")
        eq = cfg.get("equals")
        then_v = cfg.get("then", value)
        else_v = cfg.get("else", value)
        if row is None or col is None:
            return value
        chosen = then_v if _to_str(row.get(col, "")) == _to_str(eq) else else_v
        # Same {Column} interpolation as CASE_WHEN, so the two branch rules behave
        # alike — a result may build itself from other columns on the row.
        return _interpolate(chosen, row)

    if rt == "CASE_WHEN":
        # Multi-branch CASE/SWITCH with comparison ops. Each branch:
        #   {"if_column": "x", "op": "eq|gt|...", "value": "...", "then": "..."}
        # ``if_column`` defaults to the cell value when omitted.
        branches = cfg.get("branches", []) or []
        default = cfg.get("default", value)
        for br in branches:
            if _branch_holds(br, value, row):
                # ``then`` may reference other columns as ``{Column}`` — e.g.
                # ``E{Employee_ID}``. Literal results (``SA``, ``AE``) have no
                # braces and pass through unchanged.
                return _interpolate(br.get("then", default), row)
        return _interpolate(default, row)

    if rt == "MAP_BOOLEAN":
        # Normalise a boolean-ish source value to a fixed pair of output codes.
        # config: {"true_values":[...], "false_values":[...],
        #          "true_output":"Y", "false_output":"N", "default":""}.
        # Comparison is case-insensitive & trimmed; blank source -> default.
        s = _to_str(value).strip()
        if s == "":
            return cfg.get("default", "")
        low = s.lower()
        trues = [str(x).strip().lower() for x in (cfg.get("true_values") or sorted(_TRUEISH))]
        falses = [str(x).strip().lower() for x in (cfg.get("false_values") or sorted(_FALSEISH))]
        if low in trues:
            return cfg.get("true_output", "Y")
        if low in falses:
            return cfg.get("false_output", "N")
        return cfg.get("default", "")

    if rt == "CONDITIONAL_DATE":
        # Emit a date when a condition on another column holds, else a blank/other
        # value. Supports two config schemas seen in the catalog:
        #   {"condition":"COL = VAL", "value":"SYSDATE"|<col>|literal, "else":"null"|...}
        #   {"condition":"COL = VAL", "then_value":..., "else_value":...}
        # A token may be SYSDATE/today/now (-> today, FBDI %Y%m%d), null/None (-> blank),
        # the name of another column (-> that column's date value, normalised), or a
        # literal. Reads referenced columns from ``row`` directly. e.g. Inactive Date
        # <- Inactive: Inactive=Yes -> today's date, else blank.
        now = ctx.get("now") or datetime.utcnow()

        def _norm_date(s: str) -> str:
            for fmt_in in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
                           "%Y%m%d", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S",
                           "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(s, fmt_in).strftime(_OUT_DATE_FORMAT)
                except ValueError:
                    continue
            return s

        def _resolve_date_token(tok: Any) -> str:
            if tok is None:
                return ""
            s = _to_str(tok).strip()
            low = s.lower()
            if low in ("null", "none", ""):
                return ""
            if low in ("sysdate", "today", "now"):
                return now.strftime(_OUT_DATE_FORMAT)
            if row is not None and s in row:  # token is another column's name
                return _norm_date(_to_str(row.get(s, "")).strip())
            return _norm_date(s)

        cond = _to_str(cfg.get("condition", "")).strip()
        matched = False
        if cond and row is not None:
            m = re.match(r"^\s*(.+?)\s*(!=|<>|>=|<=|=|>|<)\s*(.*?)\s*$", cond)
            if m:
                col_c, op_c, rhs = m.group(1), m.group(2), m.group(3)
                left = _to_str(row.get(col_c, "")).strip().lower()
                right = rhs.strip().lower()
                if op_c == "=":
                    matched = left == right
                elif op_c in ("!=", "<>"):
                    matched = left != right
                else:
                    lf, rf = _to_float(left), _to_float(right)
                    if lf is not None and rf is not None:
                        matched = {">": lf > rf, "<": lf < rf,
                                   ">=": lf >= rf, "<=": lf <= rf}[op_c]
            else:
                # Bare column name -> truthy when the referenced cell is non-blank.
                matched = not _is_blank(row.get(cond, ""))
        val_tok = cfg.get("value")
        if val_tok is None:
            val_tok = cfg.get("then_value", "SYSDATE")
        else_tok = cfg.get("else", cfg.get("else_value", cfg.get("otherwise", "null")))
        return _resolve_date_token(val_tok if matched else else_tok)

    if rt == "COMPUTED":
        source = (cfg.get("source") or "today").lower()
        fmt = cfg.get("format")
        now = ctx.get("now") or datetime.utcnow()
        if source == "today":
            return now.strftime(fmt or _OUT_DATE_FORMAT)
        if source == "now":
            return now.strftime(fmt or f"{_OUT_DATE_FORMAT} %H:%M:%S")
        if source == "row_index":
            return ctx.get("row_index", 0)
        if source == "uuid":
            return str(uuid.uuid4())
        if source == "current_user":
            return ctx.get("current_user", "")
        return value

    if rt == "PREFIX":
        # Prepend a fixed string. Used e.g. to neutralise supplier emails ("xx" +
        # addr) so a test/migration load can't trigger real notifications. Skips
        # blanks and is idempotent (won't double-prefix).
        s = _to_str(value)
        if cfg.get("skip_blank", True) and s.strip() == "":
            return value
        pre = _to_str(cfg.get("prefix", ""))
        if pre and cfg.get("skip_if_present", True) and s.startswith(pre):
            return s
        return pre + s

    if rt == "SUFFIX":
        s = _to_str(value)
        if cfg.get("skip_blank", True) and s.strip() == "":
            return value
        suf = _to_str(cfg.get("suffix", ""))
        if suf and cfg.get("skip_if_present", True) and s.endswith(suf):
            return s
        return s + suf

    if rt == "SUFFIX_WHEN":
        # CW #19: append "_b" on a bill-to row and "_s" on a ship-to row. SUFFIX can
        # only append a FIXED string and CASE_WHEN can only REPLACE a value — neither
        # appends a chosen one, so this otherwise needs two chained rules per field
        # that the analyst then has to keep in step.
        #   {"branches": [{"if_column": "Default Billing", "op": "notblank",
        #                  "suffix": "_b"}],
        #    "default_suffix": "", "skip_blank": true, "skip_if_present": true}
        s = _to_str(value)
        if cfg.get("skip_blank", True) and s.strip() == "":
            return value
        suf = _to_str(cfg.get("default_suffix", ""))
        for br in (cfg.get("branches") or []):
            if _branch_holds(br, value, row):
                suf = _to_str(br.get("suffix", ""))
                break
        if not suf:
            return s
        # Idempotent: generation can run twice over the same frame, and a key that
        # gains a second "_b" on every pass is a new and silent data defect.
        if cfg.get("skip_if_present", True) and s.endswith(suf):
            return s
        return s + suf

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
