"""Directive selection policy — the pure decision layer of the strategy overlay
(Phase 3, slice 1). A directive is a write-time instruction (blank / constant / rule)
the analyst filed against a target field; the *store* (app.services.strategy_overlay)
reads the JSON files and builds the caches, and this module decides, purely, WHICH
directive applies to a given (object, field, source) and what columns/configs a set of
directives implies. No I/O, no file paths, no caching — every function takes the loaded
directive caches as arguments, so it is unit-testable without the Beanie/Mongo stack or
the data directory.

Cache shapes (built by the store):
  exact_cache / wild_cache : {norm(object): {norm(field): directive}}
  blank_cache / wild_blank : {norm(object): {label(field), ...}}
A directive is {"blank": True} | {"rule": {...}} | {"constant": str, ...} plus
"as_of" (datetime|None) and "source_erp" (str|None).
"""
from __future__ import annotations

import re
from typing import Any

from app.domain.precedence.policy import wide_directive_wins
from app.domain.rules.columns import rule_referenced_columns


def norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def label(s: Any) -> str:
    """The control-default key spelling: lower-case, trailing '*' stripped.
    ``_apply_control_defaults`` keys its suppression set this way, so a directive
    can only reach it in the same shape."""
    return str(s or "").strip().lower().rstrip("*").strip()


def src_ok(d: dict, source_erp: str | None) -> bool:
    """Does a directive apply for this source system? A directive with no
    ``source_erp`` applies to EVERY source; a tagged one applies only when the
    conversion's source matches; an unknown caller source (None) still applies."""
    want = d.get("source_erp")
    if not want or source_erp is None:
        return True
    return norm(want) == norm(source_erp)


def prefix_hits(target_object: str | None, wild_cache: dict | None) -> list[str]:
    """Normalised keys of the all-sheets rule sets this object inherits — a rule
    filed under "Supplier" covers every Supplier* sheet and nothing else."""
    o = norm(target_object)
    if not o:
        return []
    return [k for k in (wild_cache or {}) if o.startswith(k)]


def select_directive(exact_cache: dict | None, wild_cache: dict | None,
                     target_object: str | None, field_name: str | None,
                     source_erp: str | None = None) -> dict | None:
    """The write-time directive for one target field, or None.

    ``source_erp`` narrows to source-scoped directives: a NetSuite-only rule is
    invisible to an arena_ebos conversion, which then keeps its own mapping.
    """
    if not target_object or not field_name:
        return None
    fld = norm(field_name)
    exact = (exact_cache or {}).get(norm(target_object), {}).get(fld)
    if exact is not None and not src_ok(exact, source_erp):
        exact = None
    wide = None
    for k in prefix_hits(target_object, wild_cache):
        cand = (wild_cache or {}).get(k, {}).get(fld)
        if cand is not None and src_ok(cand, source_erp):
            wide = cand
            break
    if exact is None:
        return wide
    if wide is None or wide is exact:
        return exact
    # Both apply. A sheet-specific rule is more precise, so it wins a tie — but NOT
    # when the bundle-wide rule is NEWER. Analyst, 30-Jul: "whichever is latest".
    if wide_directive_wins(exact.get("as_of"), wide.get("as_of")):
        return wide
    return exact


def blank_fields_for(blank_cache: dict | None, wild_blank_cache: dict | None,
                     wild_cache: dict | None, target_object: str | None) -> set[str]:
    """Control-default keys for fields the strategy says must ship BLANK — the
    object's own set plus anything marked ``blank on ALL sheets`` for its bundle."""
    out = set((blank_cache or {}).get(norm(target_object), set()))
    for k in prefix_hits(target_object, wild_cache):
        out |= set((wild_blank_cache or {}).get(k, set()))
    return out


def referenced_columns_for(exact_cache: dict | None, wild_cache: dict | None,
                           target_object: str | None) -> set[str]:
    """Every SOURCE column this object's overlay rules read, so the generator keeps
    them through source-column pruning. A frame rule compares two OUTPUT columns and
    deliberately contributes nothing here."""
    rules = (exact_cache or {}).get(norm(target_object), {})
    wild: dict = {}
    for k in prefix_hits(target_object, wild_cache):
        wild.update((wild_cache or {}).get(k, {}))
    cols: set[str] = set()
    for d in list(wild.values()) + list(rules.values()):
        r = d.get("rule")
        if r:
            cols |= rule_referenced_columns([r])
    return {c for c in cols if str(c or "").strip()}


def configs_of_type(exact_cache: dict | None, wild_cache: dict | None,
                    target_object: str | None, rule_type: str) -> list[dict]:
    """Overlay rule configs of one type for this object — the generator needs them
    to build whatever index that rule reads (SELF_LOOKUP, SEQUENCE, …)."""
    rules = (exact_cache or {}).get(norm(target_object), {})
    merged: dict = {}
    for k in prefix_hits(target_object, wild_cache):
        merged.update((wild_cache or {}).get(k, {}))
    merged.update(rules)
    out = []
    for d in merged.values():
        r = d.get("rule") or {}
        if (r.get("rule_type") or "").upper() == rule_type.upper():
            out.append(r.get("config") or {})
    return out


def apply_frame_rules(df, exact_cache: dict | None, target_object: str | None,
                      source_erp: str | None = None):
    """BLANK_IF_EQUALS applied to the finished frame — both sides are OUTPUT columns,
    so the comparison the analyst asked for (Alternate Name vs Supplier Name) actually
    runs. Case/whitespace-insensitive; source-scoped rules skip a non-matching source."""
    rules = (exact_cache or {}).get(norm(target_object), {})
    if df is None or not len(df.columns) or not rules:
        return df
    by_norm = {}
    for c in df.columns:
        by_norm.setdefault(norm(c), c)
    for fld, d in rules.items():
        r = d.get("rule") or {}
        if r.get("rule_type") != "BLANK_IF_EQUALS":
            continue
        if not src_ok(d, source_erp):
            continue
        tgt = by_norm.get(fld)
        other = by_norm.get(norm((r.get("config") or {}).get("other_column")))
        if tgt is None or other is None or tgt == other:
            continue
        a = df[tgt].astype(str).str.strip().str.casefold()
        b = df[other].astype(str).str.strip().str.casefold()
        df.loc[(a == b) & (a != ""), tgt] = ""
    return df
