"""AI enrichment for mapping candidates.

The deterministic semantic guard (app.ai.semantic_guard) already demotes nonsense
and gives a one-line reason. This adds a natural-language, model-written verdict on
top — "employee_id holds staff IDs like E1001; a Phone field expects a dialable
number, so this is not a real mapping" — for the fields an analyst is actually
reviewing.

It is deliberately NOT run over a whole 1250-field template on every generate: that
is exactly the load that trips the gateway. It is invoked per sheet, on demand, over
only the candidates the guard could not settle confidently, and the result is
cached on the conversion so a re-open is free.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _prompt(items: list[dict]) -> str:
    lines = []
    for it in items:
        lines.append(
            f'- id {it["id"]}: source column "{it["source_column"]}" '
            f'(samples: {", ".join(it.get("sample_values") or []) or "none"}) '
            f'-> Oracle field "{it["target_field"]}"'
            + (f' ({it["target_desc"]})' if it.get("target_desc") else ""))
    body = "\n".join(lines)
    return (
        "You are validating proposed data-migration column mappings. For EACH pair, "
        "decide whether the source column could genuinely populate that Oracle field, "
        "judging by what the data MEANS — not just similar words. A staff identifier "
        "must not feed a phone number; a date must not feed an amount.\n\n"
        f"{body}\n\n"
        'Reply with ONLY a JSON array, one object per id: '
        '[{"id":n,"verdict":"plausible|unlikely|wrong","reason":"one short sentence"}]. '
        "Keep reasons under 18 words and concrete."
    )


async def vet_with_ai(items: list[dict]) -> dict[int, dict]:
    """items: [{id, source_column, sample_values, target_field, target_desc}].
    Returns {id: {verdict, reason}}. Empty dict if AI is unavailable — callers
    fall back to the deterministic guard, so this never blocks a review."""
    key = (settings.ANTHROPIC_API_KEY or "").strip()
    if not key or not items:
        return {}
    out: dict[int, dict] = {}
    # batch to keep each request small and within the gateway budget
    for i in range(0, len(items), 40):
        chunk = items[i:i + 40]
        try:
            async with httpx.AsyncClient(timeout=55.0) as cx:
                r = await cx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": settings.ANTHROPIC_MODEL, "max_tokens": 1500,
                          "messages": [{"role": "user", "content": _prompt(chunk)}]},
                )
                r.raise_for_status()
                txt = "".join(b.get("text", "") for b in r.json().get("content", [])
                              if b.get("type") == "text")
            m = re.search(r"\[.*\]", txt, re.S)
            for row in (json.loads(m.group(0)) if m else []):
                if isinstance(row, dict) and "id" in row:
                    out[int(row["id"])] = {
                        "verdict": str(row.get("verdict", "")).lower(),
                        "reason": str(row.get("reason", "")).strip(),
                    }
        except Exception as exc:                                # noqa: BLE001
            logger.warning("candidate vetting: AI batch failed: %s", exc)
            continue
    return out


async def vet_conversion_candidates(conversion, *, top_n: int = 4,
                                    only_uncertain: bool = True) -> dict:
    """Enrich a conversion's candidates with an AI verdict + reason.

    only_uncertain (default) sends the model just the pairs worth a second opinion:
    an implausible flag from the guard, or a chosen mapping with a close runner-up.
    That keeps the call small and the token spend proportional to real doubt.
    """
    from app.services.mapping_service import mapping_candidates

    groups = await mapping_candidates(conversion, top_n=top_n)
    items: list[dict] = []
    index: dict[int, tuple[str, str]] = {}   # id -> (target_field_id, source_column)
    nid = 0
    for g in groups:
        for c in g.get("candidates", []):
            uncertain = (not c.get("plausible", True)) or (0 < c.get("confidence", 0) < 0.55)
            if only_uncertain and not uncertain:
                continue
            nid += 1
            index[nid] = (g["target_field_id"], c["source_column"])
            items.append({
                "id": nid, "source_column": c["source_column"],
                "sample_values": c.get("sample_values") or [],
                "target_field": g["target_field_name"], "target_desc": "",
            })
    verdicts = await vet_with_ai(items)
    enriched: dict[str, dict] = {}
    for nid2, (tfid, src) in index.items():
        v = verdicts.get(nid2)
        if v:
            enriched.setdefault(tfid, {})[src] = v
    return {"groups": groups, "ai": enriched, "vetted": len(verdicts),
            "sent": len(items)}
