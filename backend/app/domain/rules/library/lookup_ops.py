"""Index-backed lookup rule strategies migrated out of engine._apply_one_rule (Batch B,
the final batch). SELF_LOOKUP / CROSS_CONVERSION_LOOKUP / GROUP_FIRST_FLAG / SEQUENCE /
CROSSWALK_LOOKUP each read a per-generation index the caller builds once and hands in via
``ctx`` (self_index, cross_index, group_first_index, sequence_index, crosswalks) — a
per-row scan would be O(n^2). Each reproduces its former branch VERBATIM; the only changes
are ``cfg`` -> ``config`` and an explicit ``ctx = ctx or {}`` that mirrors the coalesce
engine._apply_one_rule did before dispatch, so behaviour is byte-identical."""
from __future__ import annotations
import re
from typing import Any

from app.domain.text import to_str as _to_str, is_blank as _is_blank
from app.domain.rules.context import _resolve_column, _row_value_ci, _branch_holds


class SelfLookupRule:
    rule_type = "SELF_LOOKUP"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        ctx = ctx or {}
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


class CrossConversionLookupRule:
    rule_type = "CROSS_CONVERSION_LOOKUP"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        ctx = ctx or {}
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


class GroupFirstFlagRule:
    rule_type = "GROUP_FIRST_FLAG"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        ctx = ctx or {}
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


class SequenceRule:
    rule_type = "SEQUENCE"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        ctx = ctx or {}
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


class CrosswalkLookupRule:
    rule_type = "CROSSWALK_LOOKUP"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        ctx = ctx or {}
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
