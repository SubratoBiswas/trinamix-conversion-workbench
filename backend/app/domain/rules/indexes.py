"""Pure index builders for the ctx-backed rule strategies (Phase 2, slice 1).

Relocated VERBATIM out of app.services.output_service. Each takes the WHOLE source
frame plus the relevant rule configs and returns the `{...}` index that a lookup rule
reads from ``ctx`` at apply time — the counterpart to the strategies in
``app.domain.rules.library.lookup_ops`` / ``geo_ops``:

  * build_self_index          -> ctx["self_index"]          (SELF_LOOKUP)
  * build_sequence_index      -> ctx["sequence_index"]      (SEQUENCE)
  * build_group_first_index   -> ctx["group_first_index"]   (GROUP_FIRST_FLAG)
  * build_city_country_index  -> ctx["city_country"]        (CITY_COUNTRY_KEY)
  * build_city_case_index     -> ctx["city_case"]           (CITY_COUNTRY_KEY)

Built once over the full extract (not per chunk), because a child row's parent — or a
key's other rows — usually sit in a different chunk. Pure: pandas + re only, no I/O, no
service imports. The ``_*_configs`` gatherers that FEED these (which pull from the
strategy overlay and the mappings) stay in the service layer; this module only holds the
pure data→index computation. output_service imports these back under their historical
underscore names, so its call sites are unchanged.
"""
from __future__ import annotations

import re

import pandas as pd


def build_sequence_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{normalised key column: {key value: 0-based ordinal}}`` over the WHOLE extract.

    Ordinals follow FIRST APPEARANCE, not sort order, so re-running over the same
    extract produces the same numbers — a party key that renumbers on every regenerate
    is worse than no key at all.
    """
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm: dict[str, str] = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    index: dict[str, dict[str, int]] = {}
    for cfg in configs:
        spec = cfg.get("key_column")
        names = spec if isinstance(spec, (list, tuple)) else [spec]
        for name in names:
            key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
            if not key or key in index:
                continue
            col = by_norm.get(key)
            if col is None:
                continue
            seen: dict[str, int] = {}
            for v in src[col].tolist():
                kv = "" if v is None else str(v).strip()
                if not kv or kv.lower() in ("nan", "none"):
                    continue
                if kv not in seen:
                    seen[kv] = len(seen)
                # NetSuite writes an id as "123" in one column and "123.0" in
                # another once pandas has seen a blank in it, so index both.
                if kv.endswith(".0"):
                    seen.setdefault(kv[:-2], seen[kv])
            index[key] = seen
    return index


def build_group_first_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{normalised key column: {key value: first 0-based row index}}`` over the WHOLE
    extract — the FIRST APPEARANCE of each key, so GROUP_FIRST_FLAG marks exactly one row
    per group and blanks the rest."""
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm: dict[str, str] = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    index: dict[str, dict[str, int]] = {}
    for cfg in configs:
        spec = cfg.get("key_column")
        names = spec if isinstance(spec, (list, tuple)) else [spec]
        for name in names:
            key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
            if not key or key in index:
                continue
            col = by_norm.get(key)
            if col is None:
                continue
            first: dict[str, int] = {}
            for i, v in enumerate(src[col].tolist()):
                kv = "" if v is None else str(v).strip()
                if not kv or kv.lower() in ("nan", "none"):
                    continue
                if kv not in first:
                    first[kv] = i
                    if kv.endswith(".0"):
                        first.setdefault(kv[:-2], i)
            index[key] = first
    return index


def build_city_country_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{normalised city: ISO2}`` learned from the extract's OWN rows. Majority wins on
    ambiguity ("New York" US 48 / CN 1)."""
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    def _col(name):
        return by_norm.get(re.sub(r"[^a-z0-9]", "", str(name or "").lower()))

    tally: dict[str, dict[str, int]] = {}
    for cfg in configs:
        def _pick(spec):
            for n in (spec if isinstance(spec, (list, tuple)) else [spec]):
                c = _col(n)
                if c is not None:
                    return c
            return None

        cc_col, city_col = _pick(cfg.get("country_column")), _pick(cfg.get("city_column"))
        if cc_col is None or city_col is None:
            continue
        for cc, city in zip(src[cc_col].tolist(), src[city_col].tolist()):
            cc = "" if cc is None else str(cc).strip()
            city = "" if city is None else str(city).strip()
            if not cc or not city:
                continue
            key = re.sub(r"[^a-z]", "", city.lower())
            if not key:
                continue
            tally.setdefault(key, {})
            tally[key][cc] = tally[key].get(cc, 0) + 1
    return {k: max(v.items(), key=lambda kv: kv[1])[0] for k, v in tally.items()}


def build_city_case_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{normalised city: the spelling this extract uses most}``. A non-ALL-CAPS spelling
    wins outright, ahead of frequency, so ``ABU DHABI`` yields ``Abu Dhabi``;
    ``str.title()`` is deliberately not used (it breaks "Rio de Janeiro", "McAllen")."""
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    def _pick(spec):
        for n in (spec if isinstance(spec, (list, tuple)) else [spec]):
            c = by_norm.get(re.sub(r"[^a-z0-9]", "", str(n or "").lower()))
            if c is not None:
                return c
        return None

    tally: dict[str, dict[str, int]] = {}
    for cfg in configs:
        col = _pick(cfg.get("city_column"))
        if col is None:
            continue
        for v in src[col].tolist():
            v = "" if v is None else str(v).strip()
            if not v:
                continue
            key = re.sub(r"[^a-z0-9]", "", v.lower())
            if not key:
                continue
            tally.setdefault(key, {})
            tally[key][v] = tally[key].get(v, 0) + 1

    def _best(counts):
        return max(counts.items(), key=lambda kv: (not kv[0].isupper(), kv[1]))[0]
    return {k: _best(v) for k, v in tally.items()}


def build_self_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{"Match->Value": {match_value: value_value}}`` over the WHOLE extract. First win
    on a duplicated key (so the result never depends on row order); columns matched case-
    and space-insensitively."""
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    def _col(name):
        return by_norm.get(re.sub(r"[^a-z0-9]", "", str(name or "").lower()))

    index: dict[str, dict[str, str]] = {}
    for cfg in configs:
        mk, vk = cfg.get("match_column"), cfg.get("value_column")
        key = f"{mk}->{vk}"
        if key in index:
            continue
        mc, vc = _col(mk), _col(vk)
        if mc is None or vc is None:
            continue
        pairs: dict[str, str] = {}
        for a, b in zip(src[mc].tolist(), src[vc].tolist()):
            ka = "" if a is None else str(a).strip()
            if not ka or ka.lower() in ("nan", "none"):
                continue
            # First win: a duplicated key is a data problem, and quietly taking the
            # last row's value would make the result depend on row order.
            pairs.setdefault(ka, "" if b is None else str(b).strip())
            # NetSuite writes ids as "123" in one column and "123.0" in another once
            # pandas has seen a blank, so index the integral spelling too.
            if ka.endswith(".0"):
                pairs.setdefault(ka[:-2], pairs[ka])
        index[key] = pairs
    return index
