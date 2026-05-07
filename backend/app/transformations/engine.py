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
        out_fmt = cfg.get("output_format", "%Y/%m/%d")
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
        return sep.join(_to_str(row.get(c, "")) for c in cols)

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

    if rt == "COMPUTED":
        source = (cfg.get("source") or "today").lower()
        fmt = cfg.get("format")
        now = ctx.get("now") or datetime.utcnow()
        if source == "today":
            return now.strftime(fmt or "%Y/%m/%d")
        if source == "now":
            return now.strftime(fmt or "%Y/%m/%d %H:%M:%S")
        if source == "row_index":
            return ctx.get("row_index", 0)
        if source == "uuid":
            return str(uuid.uuid4())
        if source == "current_user":
            return ctx.get("current_user", "")
        return value

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
