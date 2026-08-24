"""Entity resolution — the AI-adjudication adapter over the pure clustering policy.

The deterministic duplicate-clustering policy (identity-field detection, pair scoring,
union-find clustering) moved to ``app.domain.entity.resolution`` and is re-exported below, so
``find_duplicate_clusters`` / ``detect_identity_fields`` and every other name resolve at this
path unchanged. What stays here is the ONE impure piece: a best-effort AI adjudication pass
that calls the configured LLM (httpx + settings, imported lazily) to confirm/deny borderline
clusters, degrading to the deterministic score when AI is unavailable.
"""
from __future__ import annotations

from app.domain.entity import resolution as _resolution

globals().update({_k: _v for _k, _v in vars(_resolution).items() if not _k.startswith("__")})
del _resolution


async def ai_adjudicate_clusters(result: dict, low: float = 0.72, high: float = 0.93) -> dict:
    """Ask the configured LLM to confirm/deny BORDERLINE clusters (confidence in
    [low, high)); high-confidence clusters are left as deterministic. Best-effort:
    any failure leaves the clusters unchanged (``ai_used=False``). Adds ``verdict``
    ('same'|'different'|'unsure') and ``ai_used`` per adjudicated cluster."""
    result = dict(result)
    result["ai_used"] = False
    clusters = result.get("clusters") or []
    borderline = [c for c in clusters if low <= c.get("confidence", 0) < high]
    if not borderline:
        return result
    try:
        import json
        import httpx
        from app.config import settings
        provider = (settings.AI_PROVIDER or "none").lower()
        if provider not in ("anthropic", "openai"):
            return result
        payload = [{"id": i, "records": [m["values"] for m in c["members"][:6]]}
                   for i, c in enumerate(borderline)]
        prompt = (
            "You are a data-migration entity-resolution expert. For each GROUP of "
            f"records below (candidate duplicates of the same '{result.get('object')}'), "
            "decide whether they are the SAME real-world entity. Return ONLY a JSON "
            'array: [{"id":<id>,"verdict":"same|different|unsure","confidence":0..1,'
            '"reason":"short"}].\n\nGROUPS:\n' + json.dumps(payload, indent=1)
        )
        if provider == "anthropic":
            r = httpx.post("https://api.anthropic.com/v1/messages",
                           headers={"x-api-key": settings.ANTHROPIC_API_KEY,
                                    "anthropic-version": "2023-06-01",
                                    "content-type": "application/json"},
                           json={"model": settings.ANTHROPIC_MODEL or "claude-sonnet-4-6",
                                 "max_tokens": 1500,
                                 "messages": [{"role": "user", "content": prompt}]},
                           timeout=50.0)
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", [])
                           if b.get("type") == "text")
        else:
            r = httpx.post("https://api.openai.com/v1/chat/completions",
                           headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                                    "Content-Type": "application/json"},
                           json={"model": settings.OPENAI_MODEL,
                                 "messages": [{"role": "user", "content": prompt}]},
                           timeout=50.0)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        text = text.strip().strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        verdicts = {int(v["id"]): v for v in json.loads(text) if "id" in v}
        for i, c in enumerate(borderline):
            v = verdicts.get(i)
            if v:
                c["verdict"] = v.get("verdict", "unsure")
                c["ai_reason"] = v.get("reason", "")
                if isinstance(v.get("confidence"), (int, float)):
                    c["confidence"] = round(float(v["confidence"]), 3)
        result["ai_used"] = True
    except Exception:  # noqa: BLE001 — advisory; keep deterministic clusters
        return result
    return result
