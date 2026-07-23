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


# Hard ceiling on pairs sent to the model in one request. A wide template (the
# Customer import is 1254 fields) has hundreds of low-confidence options; vetting
# them all is dozens of sequential batches and blows the ~100s gateway, which is
# exactly why the button failed. Cap to two batches' worth and PRIORITISE the pairs
# worth a human's attention, so the call always returns well inside the budget.
_MAX_AI_ITEMS = 60


async def vet_conversion_candidates(conversion, *, top_n: int = 4,
                                    only_uncertain: bool = True,
                                    target_field_ids: list[str] | None = None,
                                    max_items: int = _MAX_AI_ITEMS) -> dict:
    """Enrich a conversion's candidates with an AI verdict + reason.

    only_uncertain (default) keeps just the pairs worth a second opinion — an
    implausible flag, or a low-confidence option. target_field_ids limits the pass
    to specific fields (the rows the analyst is actually looking at), which is how
    the UI keeps a wide template fast. Whatever remains is ranked so the most
    doubtful pairs are vetted first, then capped at max_items.
    """
    from app.services.mapping_service import mapping_candidates

    wanted = set(target_field_ids or [])
    groups = await mapping_candidates(conversion, top_n=top_n)
    if wanted:
        groups = [g for g in groups if g["target_field_id"] in wanted]

    # collect candidate pairs with a priority: implausible (0) is most worth an
    # explanation, then a close-but-uncertain option (1); ties by lower confidence.
    pool: list[tuple[int, float, str, str, dict]] = []
    for g in groups:
        for c in g.get("candidates", []):
            implausible = not c.get("plausible", True)
            conf = c.get("confidence", 0) or 0
            uncertain = implausible or (0 < conf < 0.55)
            if only_uncertain and not uncertain:
                continue
            pri = 0 if implausible else 1
            pool.append((pri, conf, g["target_field_id"], g["target_field_name"], c))
    pool.sort(key=lambda t: (t[0], t[1]))          # implausible first, then lowest conf

    items: list[dict] = []
    index: dict[int, tuple[str, str]] = {}
    for nid, (_, _, tfid, tname, c) in enumerate(pool[:max_items], start=1):
        index[nid] = (tfid, c["source_column"])
        items.append({
            "id": nid, "source_column": c["source_column"],
            "sample_values": c.get("sample_values") or [],
            "target_field": tname, "target_desc": "",
        })

    verdicts = await vet_with_ai(items)
    enriched: dict[str, dict] = {}
    for nid2, (tfid, src) in index.items():
        v = verdicts.get(nid2)
        if v:
            enriched.setdefault(tfid, {})[src] = v
    return {"groups": groups, "ai": enriched, "vetted": len(verdicts),
            "sent": len(items), "eligible": len(pool),
            "capped": len(pool) > max_items}
