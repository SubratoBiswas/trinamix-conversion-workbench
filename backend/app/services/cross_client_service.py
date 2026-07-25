"""Cross-client mapping / crosswalk auto-suggestion.

Learnings (mappings, rules, value crosswalks) are captured PER CLIENT. When a new
client's object is being mapped, this surfaces the decisions OTHER clients have
already approved for the SAME Fusion object — "3 other clients map this field from
`vendor_no`", "2 clients crosswalk 'Net 30' → 'PMT_NET30'" — with a confidence that
rises with the number of supporting clients and how often each was reused.

Tenant isolation is preserved: nothing is auto-applied and the current client's own
rows are excluded — these are advisory suggestions an analyst can accept, which then
become that client's own learning.

The aggregation (``aggregate_cross_client``) is a pure function so it is unit-testable
with no DB; ``suggest_for_object`` is the thin Beanie wrapper that feeds it.
"""
from __future__ import annotations

import re
from typing import Optional


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s if s is not None else "").strip().lower())


def _confidence(support: int, uses: int) -> float:
    base = {0: 0.0, 1: 0.6, 2: 0.75, 3: 0.85}.get(support, 0.92 if support >= 4 else 0.0)
    if base == 0.0:
        return 0.0
    return round(min(0.98, base + min(0.06, 0.01 * max(0, uses - support))), 3)


def aggregate_cross_client(rows: list[dict], exclude_client_id: Optional[str],
                           *, limit: int = 200) -> list[dict]:
    """Group client-scoped learnings into cross-client suggestions.

    ``rows`` items: ``{client_id, target_field, kind, rule_type, original_value,
    resolved_value, times_reused}``. A suggestion is emitted only when at least one
    client OTHER than ``exclude_client_id`` supports it. Returns suggestions sorted
    by confidence then support."""
    exc = str(exclude_client_id) if exclude_client_id else None
    groups: dict[tuple, dict] = {}
    for r in rows:
        cid = str(r.get("client_id")) if r.get("client_id") is not None else None
        key = (
            _norm(r.get("target_field")),
            (r.get("kind") or "").lower(),
            (r.get("rule_type") or "") or "",
            _norm(r.get("original_value")),
            _norm(r.get("resolved_value")),
        )
        g = groups.setdefault(key, {
            "target_field": r.get("target_field"),
            "kind": r.get("kind"),
            "rule_type": r.get("rule_type"),
            "original_value": r.get("original_value"),
            "resolved_value": r.get("resolved_value"),
            "clients": set(),
            "own": False,
            "uses": 0,
        })
        g["uses"] += int(r.get("times_reused") or 0) + 1
        if cid is not None and cid == exc:
            g["own"] = True
        elif cid is not None:
            g["clients"].add(cid)

    out: list[dict] = []
    for g in groups.values():
        support = len(g["clients"])
        if support < 1:
            continue  # no OTHER client supports this — not a cross-client signal
        out.append({
            "target_field": g["target_field"],
            "kind": g["kind"],
            "rule_type": g["rule_type"],
            "original_value": g["original_value"],
            "resolved_value": g["resolved_value"],
            "support_clients": support,
            "uses": g["uses"],
            "already_used_here": g["own"],
            "confidence": _confidence(support, g["uses"]),
        })
    out.sort(key=lambda s: (-s["confidence"], -s["support_clients"], -s["uses"]))
    return out[:limit]


async def suggest_for_object(target_object: str, exclude_client_id, *,
                             kinds: Optional[list[str]] = None, limit: int = 200) -> dict:
    """Fetch client-scoped learnings for ``target_object`` across ALL clients and
    aggregate them into cross-client suggestions (excluding the current client)."""
    from app.models.learned import LearnedMapping
    if not target_object:
        return {"target_object": target_object, "suggestions": [], "clients_seen": 0}
    o = _norm(target_object)
    q = {"target_object": {"$regex": f"^{re.escape(target_object)}$", "$options": "i"}}
    if kinds:
        q["kind"] = {"$in": kinds}
    docs = await LearnedMapping.find(q).limit(4000).to_list()
    rows = [{
        "client_id": d.client_id, "target_field": d.target_field, "kind": d.kind,
        "rule_type": d.rule_type, "original_value": d.original_value,
        "resolved_value": d.resolved_value, "times_reused": d.times_reused,
    } for d in docs if d.client_id is not None and not d.is_global]
    suggestions = aggregate_cross_client(rows, exclude_client_id, limit=limit)
    clients_seen = len({str(r["client_id"]) for r in rows
                        if str(r["client_id"]) != (str(exclude_client_id) if exclude_client_id else None)})
    return {"target_object": target_object, "suggestions": suggestions,
            "clients_seen": clients_seen, "learnings_scanned": len(rows)}
