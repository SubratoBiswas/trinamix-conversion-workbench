"""Fuzzy duplicate / entity resolution — find records that are the SAME real-world
entity despite non-identical keys or names.

The multi-source merge (``merge_dedupe``) collapses records on an EXACT business
key. This finds the ones it can't: the same supplier/customer/item appearing across
source systems as "Acme Inc", "Acme, Inc." and "ACME INCORPORATED", or with slightly
different numbers. It blocks candidates (so it stays near-linear, not O(n²)), scores
pairs on the object's identity fields with a fuzzy similarity, clusters them with
union-find, and returns each suspected-duplicate cluster with a confidence score and
the field-level evidence.

Kept dependency-light (pandas + stdlib ``difflib``/``re``) so the matching logic is
unit-testable with no DB, network, or model. An OPTIONAL AI adjudication pass
(``ai_adjudicate_clusters``) can confirm/deny borderline clusters via the configured
LLM; it degrades to the deterministic score when AI is unavailable.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

import pandas as pd

# ── Identity fields per object family: (header substrings, kind, weight) ──
# `kind` drives comparison behaviour: STRONG ids (taxid/duns/number) reward an
# EXACT match heavily; name/address/city use fuzzy string similarity.
_IDENTITY = {
    "supplier": [
        (["supplier name", "vendor name", "party name", "name"], "name", 0.55),
        (["taxpayer id", "tax registration", "tax id", "vat"], "taxid", 0.30),
        (["duns"], "taxid", 0.25),
        (["supplier number", "vendor number", "supplier id"], "number", 0.10),
        (["address line 1", "address line", "address"], "address", 0.20),
        (["city", "town"], "city", 0.10),
        (["postal code", "zip"], "postal", 0.10),
    ],
    "customer": [
        (["organization name", "customer name", "party name", "account name", "name"], "name", 0.55),
        (["taxpayer id", "tax registration", "tax id"], "taxid", 0.30),
        (["duns"], "taxid", 0.25),
        (["account number", "customer number", "party number"], "number", 0.10),
        (["address line 1", "address line", "address"], "address", 0.20),
        (["city", "town"], "city", 0.10),
    ],
    "item": [
        (["item description", "description", "item name", "long description"], "name", 0.5),
        (["item number", "item", "inventory item"], "number", 0.3),
        (["manufacturer part", "mpn", "supplier part"], "taxid", 0.3),
    ],
    "bom": [
        (["structure name", "assembly", "parent item", "item"], "name", 0.6),
        (["organization"], "city", 0.1),
    ],
}
# Object families whose rows are naturally many-per-entity (child interfaces) —
# skip entity resolution there (distinct rows are expected, not duplicates).
_CHILD_HINTS = ("site", "address", "contact", "assignment", "bank", "component",
                "revision", "category", "relationship", "association")

# Sorted-neighbourhood window for name groups too large to compare pairwise. 20 is
# generous for the failure this exists to catch — near-identical names sort adjacent,
# so real duplicates land within a few positions of each other, not twenty.
_WINDOW = 20


def _norm(s) -> str:
    if s is None:
        return ""
    t = str(s).strip().lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    # drop common company suffixes/noise so "Acme Inc" == "Acme, Inc."
    t = re.sub(r"\b(inc|incorporated|ltd|limited|llc|llp|corp|corporation|co|company|"
               r"gmbh|pvt|private|plc|sa|ag|bv|pte|the)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _norm_key(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s if s is not None else "").lower())


def _tokens(s: str) -> set:
    return {w for w in _norm(s).split(" ") if w}


def _str_sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na and not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    return round(0.5 * jac + 0.5 * seq, 4)


def detect_identity_fields(df: pd.DataFrame, target_object: Optional[str]) -> list[dict]:
    """Map the object's identity fields to actual DataFrame columns.
    Returns [{column, kind, weight}] ordered by weight desc (first name field is
    the blocking anchor)."""
    o = (target_object or "").strip().lower()
    fam = next((k for k in _IDENTITY if k in o or o in k), None)
    specs = _IDENTITY.get(fam or "", [])
    cols_norm = {c: _norm_key(c) for c in df.columns}
    out, used = [], set()
    for substrs, kind, weight in specs:
        for col, cn in cols_norm.items():
            if col in used:
                continue
            if any(_norm_key(s) in cn for s in substrs):
                out.append({"column": col, "kind": kind, "weight": weight})
                used.add(col)
                break
    return sorted(out, key=lambda x: -x["weight"])


class _UF:
    def __init__(self, n): self.p = list(range(n))
    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b): self.p[self.find(a)] = self.find(b)


def _pair_score(r1: dict, r2: dict, fields: list[dict],
                anchor: Optional[str] = None) -> tuple[float, dict]:
    """How likely these two rows are the same entity.

    Two rules here are load-bearing, and both exist because the obvious
    implementation gets this exactly backwards.

    **An identical name is a duplicate, full stop.** Four NextPower supplier rows
    read "Nanjing Roytek & 3X Motion Technologies Co., LTD" — byte-identical —
    under supplier numbers 1416, 3567106, 3567111 and 3792588, and the scan
    reported no duplicates at all. An exact name match short-circuits now.

    **A differing STRONG ID is not evidence of distinctness.** It used to score
    0.0 and keep its weight in the denominator, so a perfect 1.0 on the name was
    averaged down — 0.5/0.8 = 0.63 against a 0.86 threshold — and the pair was
    dropped. But two rows carrying different supplier numbers is the *definition*
    of the duplicate this function exists to find: the same company entered
    twice under two keys. The scorer was treating the very thing that makes it a
    duplicate as proof it was not one.

    So a strong id that AGREES is strong positive evidence and counts fully; one
    that DISAGREES abstains rather than voting against. Fuzzy fields still
    contribute weight × similarity in both directions — a name that half matches
    is genuinely weaker evidence, unlike a key that simply differs.

    The cost is a few more false pairs on genuinely different companies with
    near-identical names. That is the right trade: a suspect can be dismissed in
    one click, whereas a missed duplicate ships to Oracle and creates a second
    supplier record.
    """
    ev = {}

    # Exact name match — nothing else can talk us out of it.
    if anchor:
        a1, a2 = _norm(r1.get(anchor, "")), _norm(r2.get(anchor, ""))
        if a1 and a1 == a2:
            ev[anchor] = 1.0
            for f in fields:
                c, kind, w = f["column"], f["kind"], f["weight"]
                if c == anchor:
                    continue
                v1 = str(r1.get(c, "") or "").strip()
                v2 = str(r2.get(c, "") or "").strip()
                if not v1 or not v2:
                    continue
                s = (1.0 if (_norm_key(v1) and _norm_key(v1) == _norm_key(v2))
                     else 0.0) if kind in ("taxid", "number") else _str_sim(v1, v2)
                if s >= 0.5:
                    ev[c] = round(s, 3)
            return 1.0, ev

    total_w = 0.0
    acc = 0.0
    for f in fields:
        c, kind, w = f["column"], f["kind"], f["weight"]
        v1, v2 = str(r1.get(c, "") or "").strip(), str(r2.get(c, "") or "").strip()
        if not v1 or not v2:
            continue
        if kind in ("taxid", "number"):
            agree = bool(_norm_key(v1)) and _norm_key(v1) == _norm_key(v2)
            if not agree:
                # Abstain. A different key does not argue the entities apart.
                continue
            s = 1.0
        else:
            s = _str_sim(v1, v2)
        total_w += w
        acc += w * s
        if s >= 0.5:
            ev[c] = round(s, 3)
    if total_w == 0:
        return 0.0, ev
    return round(acc / total_w, 4), ev


def find_duplicate_clusters(
    df: pd.DataFrame,
    target_object: Optional[str],
    *,
    threshold: float = 0.86,
    max_rows: int = 8000,
    max_block: int = 400,
    max_clusters: int = 100,
) -> dict:
    """Return suspected-duplicate clusters (size ≥ 2) that share NO exact key but
    are likely the same entity. Result:
    ``{object, rows_scanned, identity_fields, clusters:[{confidence, fields, members:[{row, values}]}], truncated}``.
    Child/detail interfaces (many rows per entity) return no clusters."""
    o = (target_object or "").strip().lower()
    if any(h in o for h in _CHILD_HINTS):
        return {"object": target_object, "rows_scanned": 0, "identity_fields": [],
                "clusters": [], "note": "child interface — entity resolution skipped"}
    if df is None or df.empty:
        return {"object": target_object, "rows_scanned": 0, "identity_fields": [], "clusters": []}
    fields = detect_identity_fields(df, target_object)
    name_fields = [f for f in fields if f["kind"] == "name"]
    if not fields or not name_fields:
        return {"object": target_object, "rows_scanned": 0,
                "identity_fields": [f["column"] for f in fields], "clusters": [],
                "note": "no name/identity column detected"}
    anchor = name_fields[0]["column"]
    work = df.head(max_rows).reset_index(drop=True)
    recs = work.to_dict("records")
    n = len(recs)

    # Blocking: group by the first 4 alnum chars of the normalized anchor name.
    # Comparison is O(block²), so an oversized block cannot simply be compared.
    # It used to be DROPPED — silently, with truncated=False and no note — so on a
    # large extract "0 duplicates" could mean "thousands of rows were never
    # examined". Split it on a longer prefix instead, and count whatever is still
    # too big so the caller can say so out loud.
    blocks: dict[str, list[int]] = {}
    rows_without_anchor = 0
    for i, r in enumerate(recs):
        k = _norm_key(_norm(r.get(anchor, "")))[:4]
        if k:
            blocks.setdefault(k, []).append(i)
        else:
            rows_without_anchor += 1

    # An oversized block is handled by the SORTED-NEIGHBOURHOOD method rather than
    # dropped: sort its rows by normalised name and compare each against the next
    # ``window`` neighbours. Near-duplicate names sort adjacently, so this keeps most
    # of the recall at O(k · window) instead of O(k²) — 2,000 rows becomes 40,000
    # pair scores, not four million. Splitting on a longer prefix was the other
    # option and is worse: "ACME Holdings LLC" and "ACME Holdings Inc" diverge at
    # character 13 and would land in different blocks, never compared at all.
    pairs_to_score: list[tuple[int, int]] = []
    rows_compared = 0
    rows_unique_name = 0
    windowed_blocks, rows_windowed = 0, 0
    for idxs in blocks.values():
        if len(idxs) < 2:
            # Nothing shares this row's leading name characters, so there is no
            # candidate partner to compare it WITH. Counted separately from
            # rows_compared: otherwise "183 scanned, 29 compared" reads as though
            # 154 rows were skipped, when in fact they have no possible match.
            rows_unique_name += len(idxs)
            continue
        rows_compared += len(idxs)
        if len(idxs) <= max_block:
            for ai in range(len(idxs)):
                for bi in range(ai + 1, len(idxs)):
                    pairs_to_score.append((idxs[ai], idxs[bi]))
            continue
        windowed_blocks += 1
        rows_windowed += len(idxs)
        ordered = sorted(idxs, key=lambda i: _norm_key(_norm(recs[i].get(anchor, ""))))
        for pos, i in enumerate(ordered):
            for j in ordered[pos + 1:pos + 1 + _WINDOW]:
                pairs_to_score.append((i, j))

    uf = _UF(n)
    pair_conf: dict[tuple, tuple] = {}
    for i, j in pairs_to_score:
        score, ev = _pair_score(recs[i], recs[j], fields, anchor)
        if score >= threshold:
            uf.union(i, j)
            pair_conf[(min(i, j), max(i, j))] = (score, ev)

    # Assemble clusters from the union-find groups that actually had ≥1 qualifying pair.
    members_of: dict[int, list[int]] = {}
    involved = {x for pair in pair_conf for x in pair}
    for i in involved:
        members_of.setdefault(uf.find(i), []).append(i)

    show = [f["column"] for f in fields]
    clusters = []
    for root, members in members_of.items():
        if len(members) < 2:
            continue
        members = sorted(members)
        confs = [c for (a, b), (c, _) in pair_conf.items() if a in members and b in members]
        ev_cols = sorted({col for (a, b), (_, ev) in pair_conf.items()
                          if a in members and b in members for col in ev})
        clusters.append({
            "confidence": round(min(confs), 3) if confs else threshold,
            "fields": ev_cols or [anchor],
            "size": len(members),
            "members": [{"row": int(m), "values": {c: recs[m].get(c, "") for c in show}}
                        for m in members],
        })
    clusters.sort(key=lambda c: (-c["confidence"], -c["size"]))
    truncated = len(clusters) > max_clusters
    # Say what was NOT examined. "0 duplicates" and "0 duplicates among the rows we
    # could compare" are different answers, and only one of them is safe to act on.
    coverage_notes = []
    if rows_windowed:
        coverage_notes.append(
            f"{rows_windowed} row(s) in {windowed_blocks} large name group(s) were "
            f"compared against their {_WINDOW} nearest neighbours by name rather than "
            f"against every other row — a distant pair inside such a group can be "
            f"missed")
    if rows_without_anchor:
        coverage_notes.append(
            f"{rows_without_anchor} row(s) have no value in {anchor!r} and cannot be "
            f"matched by name")
    return {
        "object": target_object,
        "rows_scanned": n,
        "identity_fields": show,
        "anchor": anchor,
        "clusters": clusters[:max_clusters],
        "cluster_count": len(clusters),
        "duplicate_rows": sum(c["size"] for c in clusters),
        "truncated": truncated,
        # These three account for every scanned row, so the numbers can be checked
        # rather than taken on trust:
        #   rows_compared      — had at least one candidate partner and was compared
        #   rows_unique_name   — no other row shares its leading name characters
        #   rows_without_anchor— no name value at all, so unmatchable by name
        "rows_compared": rows_compared,
        "rows_unique_name": rows_unique_name,
        "rows_windowed": rows_windowed,
        "windowed_blocks": windowed_blocks,
        "rows_without_anchor": rows_without_anchor,
        "coverage_note": " · ".join(coverage_notes),
    }


# ───────────────────────── optional AI adjudication ─────────────────────────
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
