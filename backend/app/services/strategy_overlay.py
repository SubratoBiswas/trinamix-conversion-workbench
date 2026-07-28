"""Write-time enforcement of the NextPower supplier conversion rules.

WHY THIS EXISTS
---------------
The strategy + analyst rules were originally seeded as client-scoped LearnedMappings.
Testing the generated FBDI proved that path does not reach the output: on both an
existing conversion AND a brand-new one, Business Relationship still emitted
PROSPECTIVE, Tax Organization Type still emitted raw source values ("Business",
"Coops"), Supplier Type regressed to raw values, and every "keep blank" rule was
ignored. The only rule that DID hold was the attribute-column block — because that
one is enforced at WRITE time, inside ``_transform_frame``, after mapping.

So the rules are applied here, at the same point, where nothing downstream can
undo them: a constant is a constant, a suppression is blank, a transform runs.
Mapping/learning remains the discovery mechanism; this is the guarantee.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent.parent / "data"
_FILE = _DATA / "supplier_strategy_defaults.json"
_cache: dict | None = None
_blank_cache: dict | None = None


def _n(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _label(s: Any) -> str:
    """The control-default key spelling: lower-case, trailing '*' stripped.
    ``_apply_control_defaults`` keys its suppression set this way, so a directive
    can only reach it in the same shape."""
    return str(s or "").strip().lower().rstrip("*").strip()


def _load() -> dict:
    """{normalised target_object: {normalised field: directive}}"""
    global _cache, _blank_cache
    if _cache is not None:
        return _cache
    out: dict[str, dict[str, dict]] = {}
    blanks: dict[str, set[str]] = {}
    try:
        doc = json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing file just disables the overlay
        _cache, _blank_cache = {}, {}
        return _cache
    rules = list(doc.get("rules") or [])
    rules += list((doc.get("analyst_rules") or {}).get("rules") or [])
    for r in rules:
        obj, fld = _n(r.get("target_object")), _n(r.get("target_field"))
        if not obj or not fld or "*" in str(r.get("target_field") or ""):
            continue                      # wildcards handled by the attribute block
        d: dict = {}
        if r.get("suppress"):
            d["blank"] = True
        elif r.get("rule_type"):
            d["rule"] = {"rule_type": r["rule_type"], "config": r.get("rule_config") or {}}
        elif r.get("constant") is not None:
            d["constant"] = str(r["constant"])
            d["fill_blank_only"] = bool(r.get("fill_blank_only"))
        else:
            continue                      # 'derive' rows are mappings, not overlays
        out.setdefault(obj, {})[fld] = d
        if d.get("blank"):
            blanks.setdefault(obj, set()).add(_label(r.get("target_field")))
    _cache, _blank_cache = out, blanks
    return _cache


def directive_for(target_object: str | None, field_name: str | None) -> dict | None:
    """The write-time directive for one target field, or None."""
    if not target_object or not field_name:
        return None
    return _load().get(_n(target_object), {}).get(_n(field_name))


def blank_fields(target_object: str | None) -> set[str]:
    """Control-default keys for fields the strategy says must ship BLANK.

    Blanking inside ``_transform_frame`` alone was not enough. Two later stages
    re-populate a column that the overlay emptied:

      * ``_SEQ_FIELDS`` — Customer Number is auto-numbered, so a blanked column
        came back as 100000, 100001, 100002 …
      * ``_CONTROL_DEFAULTS`` — "fill a wholly empty column" is exactly what a
        successfully blanked column looks like, so RFQ Or Bidding came back "Y".

    Both were observed in the live 28-Jul run. Feeding these keys into
    ``_apply_control_defaults(suppressed=…)`` closes the loop: the field is
    skipped there entirely, so nothing downstream can refill it.
    """
    _load()
    return set((_blank_cache or {}).get(_n(target_object), set()))


def apply_frame_rules(df, target_object: str | None):
    """Rules that compare one OUTPUT column against another, applied to the
    finished frame.

    ``BLANK_IF_EQUALS`` could not work row-locally: the per-row context passed to
    the engine holds SOURCE columns, but the rule is configured against a TARGET
    field ("Alternate Name" vs "Supplier Name"). The lookup missed every time and
    all 3,407 duplicate alternate names survived the 28-Jul run. Here both sides
    are output columns, so the comparison is the one the analyst actually asked
    for. Comparison is case- and whitespace-insensitive.
    """
    rules = _load().get(_n(target_object), {})
    if df is None or not len(df.columns) or not rules:
        return df
    by_norm = {}
    for c in df.columns:
        by_norm.setdefault(_n(c), c)
    for fld, d in rules.items():
        r = d.get("rule") or {}
        if r.get("rule_type") != "BLANK_IF_EQUALS":
            continue
        tgt = by_norm.get(fld)
        other = by_norm.get(_n((r.get("config") or {}).get("other_column")))
        if tgt is None or other is None or tgt == other:
            continue
        a = df[tgt].astype(str).str.strip().str.casefold()
        b = df[other].astype(str).str.strip().str.casefold()
        df.loc[(a == b) & (a != ""), tgt] = ""
    return df
