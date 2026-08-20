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

STORE / POLICY SPLIT (Phase 3, slice 1)
---------------------------------------
This module is now the *store* half of the overlay: it reads the JSON directive
files and builds the caches, and it is where the enforcement point lives. The
pure *decision* — which directive applies to a (object, field, source), and what
columns/configs a set of directives implies — moved to
``app.domain.directives.policy``, which takes the caches as arguments and has no
I/O. The public functions below are thin facades: they ensure the caches are
loaded, then hand them to the policy. External signatures are unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.directives import policy as _policy
from app.domain.directives.policy import norm as _n, label as _label

_DATA = Path(__file__).resolve().parent.parent / "data"
_FILE = _DATA / "supplier_strategy_defaults.json"
# Later analyst documents, in the ``action: blank|constant|rule`` shape. They are
# overlays for the same reason the corrections file is: a learning reaches the
# MAPPINGS, and a mapping is not what the control-default pass and the sequence
# pass read — Batch ID kept shipping 900001 through a perfectly good suppression
# learning until it was enforced here too.
#
# ``derive`` rows in these files are column mappings, not overlays, and are
# deliberately skipped: discovery stays with mapping, this is the guarantee.
# Ordered oldest first for readability only — precedence is by _effective_date,
# never by position. A later file restating a field simply carries a later date.
_EXTRA_FILES = (
    "supplier_corrections_30jul.json",
    "customer_mapping_03aug.json",
    "supplier_corrections_04aug.json",
    # Answers the _open_question 04-Aug left behind: the fax branch of Delivery
    # Method is FAX, not EMAIL. Oracle's own template settles it — Remittance
    # E-mail reads "Value must be provided when Delivery Method is EMAIL or
    # EMAILPDF", and the fax branch fires exactly when that column is blank.
    "supplier_corrections_05aug.json",
)
_cache: dict | None = None
_blank_cache: dict | None = None
# Directives an analyst marked ``applies_to_all_sheets``. Keyed by the normalised
# object they were written against, and matched by PREFIX, so a rule filed under
# "Supplier" reaches Supplier Site / Address / Site Assignment / Contact / Bank —
# and stops there. See _all_sheets_note below for why this exists.
_wild_cache: dict | None = None
_wild_blank_cache: dict | None = None


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


def _parse_date(v) -> "datetime | None":
    """YYYY-MM-DD from a rule file, or None when the file does not say."""
    s = str(v or "").strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


def _load() -> dict:
    """{normalised target_object: {normalised field: directive}}

    The store: read the strategy file and the analyst correction overlays, and
    build the four caches the policy reads — exact / wild (all-sheets) directive
    maps and their blank-field sets. Cached at module scope; built once.
    """
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
    for extra in _EXTRA_FILES:
        try:
            more = json.loads((_DATA / extra).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        _more_asof = _parse_date(more.get("_effective_date"))
        for r in (more.get("rules") or []):
            a = (r.get("action") or "").strip()
            _all = bool(r.get(_all_sheets_note()))
            # A row the analyst narrowed to particular INTERFACE SHEETS cannot be
            # honoured here. This overlay is keyed by object and looks a field up
            # by name alone — one Customer conversion is one object name for all
            # 19 sheets — so applying "Insert Update Indicator = I, profiles sheet
            # only" would put an I on all nineteen. Those rows are left to the
            # store, which does resolve per sheet. Skipping is the honest half of
            # the guarantee: over-applying a scoped instruction is not enforcement.
            if r.get("sheets") or r.get("exclude_sheets"):
                continue
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
        # Which legacy source this directive is scoped to, or None for every
        # source. NextPower Supplier has two sources — netsuite and arena_ebos —
        # and a rule the analyst wrote against the NetSuite extract (Alternate
        # Name dedup, Taxpayer ID by country) must not reshape the arena_ebos one.
        d["source_erp"] = r.get("source_erp")
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


def directive_for(target_object: str | None, field_name: str | None,
                  source_erp: str | None = None) -> dict | None:
    """The write-time directive for one target field, or None.

    ``source_erp`` narrows to source-scoped directives: a NetSuite-only rule is
    invisible to an arena_ebos conversion, which then keeps its own mapping.
    Precedence between an exact and an all-sheets rule (sheet-specific wins a tie,
    but not against a newer bundle-wide instruction) lives in the policy.
    """
    _load()
    return _policy.select_directive(_cache, _wild_cache, target_object,
                                    field_name, source_erp)


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
    return _policy.blank_fields_for(_blank_cache, _wild_blank_cache,
                                    _wild_cache, target_object)


def referenced_columns(target_object: str | None) -> set[str]:
    """Every SOURCE column this object's overlay rules read.

    The generator prunes the source frame to the columns something claims, and
    builds the per-row dict from the same set. Overlay rules were claiming
    nothing, so a rule deriving from an unmapped column evaluated against blanks
    and returned its default — Supplier Site is CONCAT("Country Code", "City"),
    both columns are in the extract, and the column shipped empty on all 8,561
    rows because neither reached the row.
    """
    _load()
    return _policy.referenced_columns_for(_cache, _wild_cache, target_object)


def rule_configs_of_type(target_object: str | None, rule_type: str) -> list[dict]:
    """Overlay rule configs of one type for this object — the generator needs them
    to build whatever index that rule reads."""
    _load()
    return _policy.configs_of_type(_cache, _wild_cache, target_object, rule_type)


def self_lookup_configs(target_object: str | None) -> list[dict]:
    """SELF_LOOKUP configs this object's overlay contributes, so the generator can
    build the row index they need. Parent Supplier is the only one today."""
    return rule_configs_of_type(target_object, "SELF_LOOKUP")


def apply_frame_rules(df, target_object: str | None, source_erp: str | None = None):
    """Rules that compare one OUTPUT column against another, applied to the
    finished frame.

    ``BLANK_IF_EQUALS`` could not work row-locally: the per-row context passed to
    the engine holds SOURCE columns, but the rule is configured against a TARGET
    field ("Alternate Name" vs "Supplier Name"). The lookup missed every time and
    all 3,407 duplicate alternate names survived the 28-Jul run. Here both sides
    are output columns, so the comparison is the one the analyst actually asked
    for. Comparison is case- and whitespace-insensitive.

    ``source_erp`` skips a source-scoped rule on a non-matching source: the
    Alternate Name dedup is NetSuite-only, so an arena_ebos supplier keeps its
    Alternate Name = name.
    """
    _load()
    return _policy.apply_frame_rules(df, _cache, target_object, source_erp)


def sheets_to_drop() -> set[str]:
    """Interface sheets that must not appear in a generated workbook at all.

    Analyst, 03-Aug: the Supplier Site workbook was shipping a
    `Third_Party_Pay_Relationships` tab. That is its own interface —
    `supplier_fbdi_file_names.json` maps it to `PozSupThirdPartyInt` — so it
    belongs in its own file, not as a tab inside Supplier Site.

    The list has been sitting in `supplier_strategy_defaults.json` under
    `blank_sheets` since it was captured, read by nothing, with its own status
    field admitting as much. This is the reader that makes it real; the shape of
    defect the guide calls "shipped and inert" is data that says something and
    code that never asks.

    Normalised names, so `Third_Party_Pay_Relationships`,
    `third party pay relationships` and `ThirdPartyPayRelationships` are one
    sheet.
    """
    out: set[str] = set()
    for path in _DATA.glob("*_strategy_defaults.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                       # noqa: BLE001
            continue
        for name in ((doc.get("blank_sheets") or {}).get("sheets") or []):
            if str(name).strip():
                out.add(_n(name))
    return out
