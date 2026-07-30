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


def _is_blank(v: Any) -> bool:
    return v is None or _to_str(v).strip() == ""


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
}


def apply_rule(
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
        repl = cfg.get("replace", "")
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
        mapping = {
            k: v for k, v in cfg.items() if k not in ("case_insensitive", "default")
        }
        if case_insensitive:
            for k, v in mapping.items():
                if isinstance(k, str) and k.lower() == s.lower():
                    return v
        else:
            if s in mapping:
                return mapping[s]
        return default if default is not None else value

    if rt == "DATE_FORMAT":
        in_fmt = cfg.get("input_format", "%m/%d/%Y")
        out_fmt = cfg.get("output_format", "%Y%m%d")
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
        cols = cfg.get("columns", [])
        if not row:
            return value
        parts = [_to_str(row.get(c, "")) for c in cols]
        # A CONCAT whose every input is missing must not emit the separator alone.
        # Supplier Site was configured as CONCAT("Country Code", "-", "City"); neither
        # column exists in the NetSuite extract, so all 8,561 rows shipped the literal
        # "-" — a required, must-be-unique key filled with a guaranteed-invalid,
        # guaranteed-duplicate value. Falling back to the incoming value makes the
        # misconfiguration visible instead of manufacturing a bad key.
        if not any(p.strip() for p in parts):
            return value
        return sep.join(parts)

    if rt == "SPLIT":
        sep = cfg.get("separator", " ")
        idx = int(cfg.get("index", 0))
        parts = _to_str(value).split(sep)
        return parts[idx] if 0 <= idx < len(parts) else value

    if rt == "COALESCE":
        cols = cfg.get("columns", [])
        if row is not None:
            for c in cols:
                v = row.get(c)
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
        return then_v if _to_str(row.get(col, "")) == _to_str(eq) else else_v

    if rt == "CASE_WHEN":
        # Multi-branch CASE/SWITCH with comparison ops. Each branch:
        #   {"if_column": "x", "op": "eq|gt|...", "value": "...", "then": "..."}
        # ``if_column`` defaults to the cell value when omitted.
        branches = cfg.get("branches", []) or []
        default = cfg.get("default", value)
        for br in branches:
            op = (br.get("op") or "eq").lower()
            cmp = _COMPARISON_OPS.get(op)
            if not cmp:
                continue
            col = br.get("if_column")
            left = (
                row.get(col) if (col and row is not None) else value
            )
            try:
                if cmp(left, br.get("value")):
                    return br.get("then", default)
            except Exception:
                continue
        return default

    if rt == "MAP_BOOLEAN":
        # Normalise a boolean-ish source value to a fixed pair of output codes.
        # config: {"true_values":[...], "false_values":[...],
        #          "true_output":"Y", "false_output":"N", "default":""}.
        # Comparison is case-insensitive & trimmed; blank source -> default.
        s = _to_str(value).strip()
        if s == "":
            return cfg.get("default", "")
        low = s.lower()
        trues = [str(x).strip().lower() for x in (cfg.get("true_values") or ["yes", "y", "1", "true"])]
        falses = [str(x).strip().lower() for x in (cfg.get("false_values") or ["no", "n", "0", "false"])]
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
                    return datetime.strptime(s, fmt_in).strftime("%Y%m%d")
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
                return now.strftime("%Y%m%d")
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
            return now.strftime(fmt or "%Y%m%d")
        if source == "now":
            return now.strftime(fmt or "%Y%m%d %H:%M:%S")
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
            cmp = _COMPARISON_OPS.get((br.get("op") or "eq").lower())
            if not cmp:
                continue
            col = br.get("if_column")
            left = row.get(col) if (col and row is not None) else value
            try:
                if cmp(left, br.get("value")):
                    suf = _to_str(br.get("suffix", ""))
                    break
            except Exception:                                   # noqa: BLE001
                continue
        if not suf:
            return s
        # Idempotent: generation can run twice over the same frame, and a key that
        # gains a second "_b" on every pass is a new and silent data defect.
        if cfg.get("skip_if_present", True) and s.endswith(suf):
            return s
        return s + suf

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
        want = _to_str(row.get(cfg_key, "") if row else value).strip()
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
        idx = int(ctx.get("row_index", 0) or 0)
        n = int(cfg.get("start", 1) or 1) + idx
        prefix = _to_str(cfg.get("prefix", ""))
        width = int(cfg.get("width", 6) or 6)
        suffix = ""
        variant = cfg.get("variant") or {}
        if variant:
            cmp = _COMPARISON_OPS.get((variant.get("op") or "eq").lower())
            col = variant.get("if_column")
            left = row.get(col) if (col and row is not None) else value
            try:
                if cmp and cmp(left, variant.get("value")):
                    if variant.get("width"):
                        width = int(variant["width"])
                    suffix = _to_str(variant.get("suffix", "")).replace(
                        "{n}", str(variant.get("counter", 1)))
            except Exception:                                   # noqa: BLE001
                pass
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
        # 1) pull an extension off the end, if any.
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
