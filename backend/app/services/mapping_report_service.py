"""One report per conversion: what was mapped, by what, and what still fails.

Answers the questions an analyst asks after every mapping run — how many fields
were mapped and by WHICH layer, how many validations and cleansing rules passed
or failed, and whether any required field has no value. Those numbers exist
today but are scattered across the mapping canvas, the DQ report and the review
tabs, so nobody reads them together and a blocking gap is easy to miss.

The layer classifier mirrors ``classifyLayer`` in MappingReviewPage.tsx. Two
copies is a real cost, and the alternative — deriving the report in the browser —
is worse: this has to be quotable in a handover document and reproducible without
a session. When one changes, change the other; the ordering is what matters, not
the labels.

Pure (stdlib) so it can be exercised with plain dicts, no DB and no network.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

_NORM = re.compile(r"[^a-z0-9]+")

GOLD = "gold"
LEARNED = "learned"
WORKBOOK = "workbook"
MANUAL = "manual"
DETERMINISTIC = "deterministic"
DEFAULT = "default"
AI = "ai"
SUPPRESSED = "suppressed"
UNMAPPED = "unmapped"

LAYER_ORDER = (GOLD, LEARNED, WORKBOOK, MANUAL, DETERMINISTIC, DEFAULT, AI,
               SUPPRESSED, UNMAPPED)
LAYER_LABEL = {
    GOLD: "Gold standard", LEARNED: "Learning library",
    WORKBOOK: "Mapping workbook", MANUAL: "Analyst",
    DETERMINISTIC: "Deterministic matcher", DEFAULT: "Control default",
    AI: "AI suggestion", SUPPRESSED: "Deliberately blank", UNMAPPED: "Not mapped",
}
# Layers that produced a value without a human or a signed source behind them.
# Counted separately because "how much of this file did the model decide?" is the
# question a reviewer actually needs answered before sign-off.
UNATTESTED = (AI, DETERMINISTIC)


def _n(s: Any) -> str:
    return _NORM.sub("", str(s).lower()) if s is not None else ""


def classify_layer(mapping: Optional[dict], field: dict,
                   effective_defaults: Optional[dict] = None,
                   has_rule: bool = False) -> str:
    """Which layer resolved this field. Mirrors the canvas classifier exactly.

    Order matters and is not alphabetical: an explicit analyst override outranks
    everything, a signed source outranks a learned one, and AI is last so it can
    never take credit for a field something better already resolved.
    """
    eff = effective_defaults or {}
    m = mapping or {}
    dv = (m.get("default_value")
          or eff.get(_n(field.get("field_name")))
          or eff.get(str(field.get("field_name", "")).strip().lower()))
    if m.get("status") == "not_applicable":
        return SUPPRESSED
    if not m.get("source_column"):
        return DEFAULT if dv else UNMAPPED
    reason = str(m.get("reason") or "").lower()
    if m.get("status") == "overridden":
        return MANUAL
    if 'from "gold' in reason or "value-set" in reason or "example" in reason:
        return GOLD
    if "mapping workbook" in reason or "mapping document" in reason:
        return WORKBOOK
    if any(w in reason for w in ("learning library", "learned",
                                 "metadata catalog", "knowledge")):
        return LEARNED
    if has_rule or (m.get("suggested_transformation") or {}).get("rule_type"):
        return MANUAL
    return DETERMINISTIC if (m.get("confidence") or 0) >= 0.6 else AI


def summarize_layers(fields: list[dict], mappings: list[dict],
                     effective_defaults: Optional[dict] = None,
                     rule_target_ids: Optional[Iterable] = None) -> dict:
    """Count every target field by the layer that resolved it."""
    rule_ids = {str(x) for x in (rule_target_ids or [])}
    by_target = {str(m.get("target_field_id")): m for m in mappings
                 if m.get("target_field_id") is not None}
    counts = {k: 0 for k in LAYER_ORDER}
    required_unmapped: list[str] = []
    for f in fields:
        fid = str(f.get("id"))
        m = by_target.get(fid)
        layer = classify_layer(m, f, effective_defaults, fid in rule_ids)
        counts[layer] += 1
        if f.get("required") and layer == UNMAPPED:
            required_unmapped.append(str(f.get("field_name")))
    total = len(fields)
    mapped = total - counts[UNMAPPED] - counts[SUPPRESSED]
    return {
        "total_fields": total,
        "mapped": mapped,
        "by_layer": [{"layer": k, "label": LAYER_LABEL[k], "count": counts[k]}
                     for k in LAYER_ORDER if counts[k]],
        # Not a quality judgement — a disclosure. A reviewer signing off wants to
        # know how much of the file nothing human or signed stands behind.
        "unattested": sum(counts[k] for k in UNATTESTED),
        "required_unmapped": sorted(required_unmapped),
    }


def _split_issues(issues: Iterable[dict]) -> dict:
    """Split a DQ issue list into pass/fail buckets by severity."""
    errors = warnings = infos = 0
    by_type: dict[str, int] = {}
    for i in issues or []:
        sev = str(i.get("severity") or "").lower()
        if sev == "error":
            errors += 1
        elif sev == "warning":
            warnings += 1
        else:
            infos += 1
        t = str(i.get("issue_type") or "other")
        by_type[t] = by_type.get(t, 0) + i.get("impacted_count", 1) or 1
    return {"errors": errors, "warnings": warnings, "info": infos,
            "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])[:12])}


def build_report(*, conversion: dict, fields: list[dict], mappings: list[dict],
                 dq_report: Optional[dict] = None,
                 required_result: Optional[dict] = None,
                 effective_defaults: Optional[dict] = None,
                 rule_target_ids: Optional[Iterable] = None,
                 custom_rules: Optional[list[dict]] = None) -> dict:
    """Assemble the post-mapping report from parts the caller already has."""
    layers = summarize_layers(fields, mappings, effective_defaults, rule_target_ids)
    dq = dq_report or {}
    issues = dq.get("issues") or dq.get("top_issues") or []
    val = _split_issues(issues)

    fixes = dq.get("cleansing_fixes") or []
    cleansing = {
        "rules_fired": len(fixes),
        "values_changed": int(sum(f.get("count", 0) for f in fixes)),
        "fields_touched": len({f.get("field") for f in fixes if f.get("field")}),
        "by_rule": sorted(
            [{"rule": r, "count": c} for r, c in
             _tally(fixes, "rule", "count").items()],
            key=lambda x: -x["count"]),
    }

    req = required_result or {}
    # The single number that decides whether this file can be handed over. A
    # required field with no value rejects EVERY row, so it outranks any count
    # of warnings elsewhere in the report.
    blocked = bool(req.get("blocked"))

    return {
        "conversion_id": conversion.get("id"),
        "target_object": conversion.get("target_object"),
        "generated_at": conversion.get("generated_at"),
        "mapping": layers,
        "validation": {
            "checked": len(issues),
            "failed": val["errors"],
            "warnings": val["warnings"],
            "passed": max(0, len(issues) - val["errors"] - val["warnings"]),
            "by_type": val["by_type"],
            "hard_error_count": dq.get("hard_error_count", val["errors"]),
        },
        "cleansing": cleansing,
        "rules": {
            "configured": len(custom_rules or []),
            "applied": cleansing["rules_fired"],
        },
        "required_fields": {
            "checked": req.get("required_total", 0),
            "failed": req.get("failed_count", 0),
            "partial": req.get("partial_count", 0),
            "passed": max(0, req.get("required_total", 0)
                          - req.get("failed_count", 0)
                          - req.get("partial_count", 0)),
            "failures": req.get("failures", []),
            "partials": req.get("partials", []),
        },
        "blocked": blocked,
        "headline": _headline(blocked, req, layers, val),
    }


def _tally(rows: Iterable[dict], key: str, count_key: str) -> dict:
    out: dict[str, int] = {}
    for r in rows or []:
        k = str(r.get(key) or "other")
        out[k] = out.get(k, 0) + int(r.get(count_key, 0) or 0)
    return out


def _headline(blocked: bool, req: dict, layers: dict, val: dict) -> str:
    if blocked:
        n = req.get("failed_count", 0)
        return (f"Not ready — {n} required field(s) have no value. "
                "Oracle rejects every row without them.")
    parts = [f"{layers['mapped']} of {layers['total_fields']} fields mapped"]
    if layers["unattested"]:
        parts.append(f"{layers['unattested']} resolved by the matcher or AI alone")
    if val["errors"]:
        parts.append(f"{val['errors']} validation error(s)")
    if req.get("partial_count"):
        parts.append(f"{req['partial_count']} required field(s) partially filled")
    return " · ".join(parts) + "."
