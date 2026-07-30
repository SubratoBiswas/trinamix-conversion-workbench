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
from datetime import datetime
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent.parent / "data"
_FILE = _DATA / "supplier_strategy_defaults.json"
_cache: dict | None = None
_blank_cache: dict | None = None
# Directives an analyst marked ``applies_to_all_sheets``. Keyed by the normalised
# object they were written against, and matched by PREFIX, so a rule filed under
# "Supplier" reaches Supplier Site / Address / Site Assignment / Contact / Bank —
# and stops there. See _all_sheets_note below for why this exists.
_wild_cache: dict | None = None
_wild_blank_cache: dict | None = None


def _n(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _label(s: Any) -> str:
    """The control-default key spelling: lower-case, trailing '*' stripped.
    ``_apply_control_defaults`` keys its suppression set this way, so a directive
    can only reach it in the same shape."""
    return str(s or "").strip().lower().rstrip("*").strip()


# ``applies_to_all_sheets`` was already being written into the corrections file by
# the analyst — and NOTHING read it. It was dead data, so two rules that say "all
# sheets" were silently applied to one sheet only:
#
#   * Batch ID — "Blank on ALL sheets. A batch identifier the loader assigns is not
#     ours to invent." Registered under Supplier alone, so Supplier Site, Address,
#     Site Assignment, Contact and Bank all kept shipping 900001, and kept SAYING
#     900001 on screen.
#   * Delivery Method — the CASE_WHEN never reached Supplier Site, which is where
#     the column actually lives.
#
# Matching is by PREFIX of the object name rather than a bare wildcard: a rule
# filed under "Supplier" covers every Supplier* sheet and nothing else. A true
# wildcard would have quietly blanked Customer's Batch ID too, which no one asked
# for — the analyst was talking about the supplier bundle.
def _all_sheets_note() -> str:
    return "applies_to_all_sheets"


def _load() -> dict:
    """{normalised target_object: {normalised field: directive}}"""
    global _cache, _blank_cache, _wild_cache, _wild_blank_cache
    if _cache is not None:
        return _cache
    out: dict[str, dict[str, dict]] = {}
    blanks: dict[str, set[str]] = {}
    wild: dict[str, dict[str, dict]] = {}
    wild_blanks: dict[str, set[str]] = {}
    try:
        doc = json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing file just disables the overlay
        _cache, _blank_cache = {}, {}
        _wild_cache, _wild_blank_cache = {}, {}
        return _cache
    _base_asof = _parse_date(doc.get("_effective_date"))
    rules = [dict(r, _asof=_base_asof) for r in (doc.get("rules") or [])]
    rules += [dict(r, _asof=_base_asof)
              for r in ((doc.get("analyst_rules") or {}).get("rules") or [])]
    # The analyst's later correction files are overlays too, and the BLANK ones have to
    # be, or nothing stops the control defaults refilling them. Batch ID is the proof:
    # the analyst said blank it on every sheet, and it kept shipping 900001 because
    # _CONTROL_DEFAULTS carries "batch id": "900001" and only skips a column named in
    # THIS blank set. A suppress_field learning reaches the mappings; it does not reach
    # the control-default pass, so a field with no mapping row at all — which is exactly
    # what an unmapped Batch ID is — was refilled every time.
    for extra in ("supplier_corrections_30jul.json",):
        try:
            more = json.loads((_DATA / extra).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        _more_asof = _parse_date(more.get("_effective_date"))
        for r in (more.get("rules") or []):
            a = (r.get("action") or "").strip()
            _all = bool(r.get(_all_sheets_note()))
            if a == "blank":
                rules.append({"target_object": r.get("target_object"),
                              "target_field": r.get("target_field"), "suppress": True,
                              "all_sheets": _all, "_asof": _more_asof})
            elif a == "constant":
                rules.append({"target_object": r.get("target_object"),
                              "target_field": r.get("target_field"),
                              "constant": r.get("value"), "all_sheets": _all,
                              "_asof": _more_asof})
            elif a == "rule" and r.get("rule_type"):
                rules.append({"target_object": r.get("target_object"),
                              "target_field": r.get("target_field"),
                              "rule_type": r["rule_type"],
                              "rule_config": r.get("rule_config") or {},
                              "all_sheets": _all, "_asof": _more_asof})
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
        # When this instruction was given. The analyst's precedence rule is
        # "analyst manual change OR the mapping file, WHICHEVER IS LATEST", so the
        # generator has to be able to compare the two — a directive with no date
        # loses to any human approval, which is the old behaviour.
        d["as_of"] = r.get("_asof")
        out.setdefault(obj, {})[fld] = d
        if d.get("blank"):
            blanks.setdefault(obj, set()).add(_label(r.get("target_field")))
        if r.get("all_sheets"):
            wild.setdefault(obj, {})[fld] = d
            if d.get("blank"):
                wild_blanks.setdefault(obj, set()).add(_label(r.get("target_field")))
    _cache, _blank_cache = out, blanks
    _wild_cache, _wild_blank_cache = wild, wild_blanks
    return _cache


def _prefix_hits(target_object: str | None) -> list[str]:
    """Normalised keys of the all-sheets rule sets this object inherits."""
    o = _n(target_object)
    if not o:
        return []
    return [k for k in (_wild_cache or {}) if o.startswith(k)]


def _parse_date(v) -> "datetime | None":
    """YYYY-MM-DD from a rule file, or None when the file does not say."""
    s = str(v or "").strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


def directive_for(target_object: str | None, field_name: str | None) -> dict | None:
    """The write-time directive for one target field, or None."""
    if not target_object or not field_name:
        return None
    fld = _n(field_name)
    exact = _load().get(_n(target_object), {}).get(fld)
    wide = None
    for k in _prefix_hits(target_object):
        wide = (_wild_cache or {}).get(k, {}).get(fld)
        if wide is not None:
            break
    if exact is None:
        return wide
    if wide is None or wide is exact:
        return exact
    # Both apply. A sheet-specific rule is more precise, so it wins a tie — but
    # NOT when the bundle-wide rule is NEWER. Analyst, 30-Jul: "whichever is
    # latest". Delivery Method is exactly this case: the 13-Jul strategy carries a
    # Supplier Site rule reading "Remittance E-Mail", and the 30-Jul correction
    # says apply the EMAIL/FAX rule to all sheets. Preferring precision alone let
    # the older, narrower rule shadow the newer instruction, and the column shipped
    # empty on all 8,561 site rows.
    ea, wa = exact.get("as_of"), wide.get("as_of")
    if wa is not None and (ea is None or wa > ea):
        return wide
    return exact


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
    out = set((_blank_cache or {}).get(_n(target_object), set()))
    # Plus anything the analyst marked "blank on ALL sheets" for this bundle.
    for k in _prefix_hits(target_object):
        out |= set((_wild_blank_cache or {}).get(k, set()))
    return out


def referenced_columns(target_object: str | None) -> set[str]:
    """Every SOURCE column this object's overlay rules read.

    The generator prunes the source frame to the columns something claims, and
    builds the per-row dict from the same set. Overlay rules were claiming
    nothing, so a rule deriving from an unmapped column evaluated against blanks
    and returned its default — Supplier Site is CONCAT("Country Code", "City"),
    both columns are in the extract, and the column shipped empty on all 8,561
    rows because neither reached the row.
    """
    from app.services.output_service import _rule_referenced_columns
    rules = _load().get(_n(target_object), {})
    wild: dict = {}
    for k in _prefix_hits(target_object):
        wild.update((_wild_cache or {}).get(k, {}))
    cols: set[str] = set()
    for d in list(wild.values()) + list(rules.values()):
        r = d.get("rule")
        if r:
            cols |= _rule_referenced_columns([r])
        # A frame rule compares against another OUTPUT column, not a source one,
        # so it deliberately contributes nothing here.
    return {c for c in cols if str(c or "").strip()}


def rule_configs_of_type(target_object: str | None, rule_type: str) -> list[dict]:
    """Overlay rule configs of one type for this object — the generator needs them
    to build whatever index that rule reads."""
    rules = _load().get(_n(target_object), {})
    merged: dict = {}
    for k in _prefix_hits(target_object):
        merged.update((_wild_cache or {}).get(k, {}))
    merged.update(rules)
    out = []
    for d in merged.values():
        r = d.get("rule") or {}
        if (r.get("rule_type") or "").upper() == rule_type.upper():
            out.append(r.get("config") or {})
    return out


def self_lookup_configs(target_object: str | None) -> list[dict]:
    """SELF_LOOKUP configs this object's overlay contributes, so the generator can
    build the row index they need. Parent Supplier is the only one today."""
    rules = _load().get(_n(target_object), {})
    merged: dict = {}
    for k in _prefix_hits(target_object):
        merged.update((_wild_cache or {}).get(k, {}))
    merged.update(rules)
    out = []
    for d in merged.values():
        r = d.get("rule") or {}
        if (r.get("rule_type") or "").upper() == "SELF_LOOKUP":
            out.append(r.get("config") or {})
    return out


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
