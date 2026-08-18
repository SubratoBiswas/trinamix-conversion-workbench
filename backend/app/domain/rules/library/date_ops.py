"""Date / compute rule strategies migrated out of engine._apply_one_rule. All date-format
knowledge now lives in app.domain.dates.fbdi_date; these strategies compose it. Each
reproduces its former branch VERBATIM — the only changes are ``cfg`` -> the strategy's
``config`` parameter and an explicit ``ctx = ctx or {}`` that mirrors the coalesce
engine._apply_one_rule did before dispatch, so behaviour is byte-identical."""
from __future__ import annotations
import re
import uuid
from datetime import datetime
from typing import Any

from app.domain.text import to_str as _to_str, to_float as _to_float, is_blank as _is_blank
from app.domain.dates.fbdi_date import (
    OUT_DATE_FORMAT as _OUT_DATE_FORMAT,
    oracle_date_to_py as _oracle_date_to_py,
    parse_any_date as _parse_any_date,
    parse_with_formats, CONDITIONAL_FORMATS,
)


class FormatDateRule:
    rule_type = "FORMAT_DATE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
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
        dt = _parse_any_date(s, dayfirst=bool((ctx or {}).get("dayfirst")))
        return dt.strftime(to_fmt) if dt else value


class DateFormatRule:
    rule_type = "DATE_FORMAT"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
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


class ConditionalDateRule:
    rule_type = "CONDITIONAL_DATE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        ctx = ctx or {}
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
            dt = parse_with_formats(s, CONDITIONAL_FORMATS)
            return dt.strftime(_OUT_DATE_FORMAT) if dt else s

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


class ComputedRule:
    rule_type = "COMPUTED"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        ctx = ctx or {}
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
