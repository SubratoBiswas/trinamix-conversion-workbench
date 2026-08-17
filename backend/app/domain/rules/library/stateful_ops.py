"""Row-aware rule strategies migrated out of engine._apply_one_rule. Unlike the
string/value ops these read OTHER columns off the row (CONCAT/COALESCE/CONDITIONAL/
CASE_WHEN/BLANK_IF_EQUALS/SUFFIX_WHEN) or fold a fixed affix onto the cell
(PREFIX/SUFFIX). Each reproduces its former branch VERBATIM; the only change is
``cfg`` -> the strategy's ``config`` parameter. Dependencies are the domain helpers
the branches already used, imported here under their historical underscore names so
the bodies are copied unchanged."""
from __future__ import annotations
from typing import Any

from app.domain.text import to_str as _to_str, is_blank as _is_blank
from app.domain.rules.context import _resolve_column, _interpolate, _branch_holds


class ConcatRule:
    rule_type = "CONCAT"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
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


class CoalesceRule:
    rule_type = "COALESCE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        cols = cfg.get("columns", [])
        if row is not None:
            for c in cols:
                v = row.get(_resolve_column(c, row))
                if not _is_blank(v):
                    return v
        if not _is_blank(value):
            return value
        return cfg.get("default", "")


class BlankIfEqualsRule:
    rule_type = "BLANK_IF_EQUALS"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
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


class ConditionalRule:
    rule_type = "CONDITIONAL"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
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


class CaseWhenRule:
    rule_type = "CASE_WHEN"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
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


class PrefixRule:
    rule_type = "PREFIX"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
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


class SuffixRule:
    rule_type = "SUFFIX"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        s = _to_str(value)
        if cfg.get("skip_blank", True) and s.strip() == "":
            return value
        suf = _to_str(cfg.get("suffix", ""))
        if suf and cfg.get("skip_if_present", True) and s.endswith(suf):
            return s
        return s + suf


class SuffixWhenRule:
    rule_type = "SUFFIX_WHEN"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
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
