"""Load user decisions and apply them to a converted frame (Mongo-aware wrapper
around the pure ``decision_engine``).

Applied inside ``build_converted_dataframe`` immediately after
``_merge_dedupe_frames``, which is the ONE place every writer reads from — the
CSV bundle, the plain xlsx and the filled Oracle template all branch off that
frame, as do the merged/project-level zip downloads. Putting it anywhere later
would have meant remembering to repeat it per writer, and a decision that reaches
only some of the outputs is worse than no feature at all.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from app.models.row_decision import RowDecision
from app.services.decision_engine import (
    KEEP_ALL, apply_decisions, cluster_key, row_keys_for,
)

logger = logging.getLogger(__name__)


def identity_columns_for(df: pd.DataFrame, target_object: Optional[str]) -> list[str]:
    """The columns that define a row's identity, shared with the duplicate
    scanner so the review screen and the generator agree on what a row IS."""
    try:
        from app.services.entity_resolution import detect_identity_fields
        return [f["column"] for f in detect_identity_fields(df, target_object)]
    except Exception:  # noqa: BLE001 — identity detection is best-effort
        return []


async def load_decisions(conversion_id, scope: str = "duplicate") -> list[dict]:
    docs = await RowDecision.find(
        RowDecision.conversion_id == conversion_id,
        RowDecision.scope == scope,
    ).to_list()
    return [{"decision_key": d.decision_key, "verdict": d.verdict,
             "survivor_key": d.survivor_key, "member_keys": d.member_keys}
            for d in docs]


async def load_learned_keep_all(client_id, target_object: Optional[str]) -> set[str]:
    """Cluster keys the user has previously ruled "genuinely different" for this
    client + object, on ANY conversion.

    This is what stops the tool re-asking about the same look-alike suppliers on
    every new extract. Scoped to the client because "3X Motion Technologies Co.,
    LTD and 3X Motion Technologies Co., Ltd. are different legal entities" is a
    fact about their data, not a universal truth.
    """
    if not client_id:
        return set()
    docs = await RowDecision.find(
        RowDecision.client_id == client_id,
        RowDecision.scope == "duplicate",
        RowDecision.verdict == KEEP_ALL,
    ).to_list()
    o = (target_object or "").strip().lower()
    return {d.decision_key for d in docs
            if not o or not d.target_object
            or d.target_object.strip().lower() == o}


async def apply_conversion_decisions(df: pd.DataFrame, conversion,
                                     target_object: Optional[str]) -> pd.DataFrame:
    """Apply this conversion's saved duplicate decisions. Never raises: a decision
    layer that could break generation would be worse than one that no-ops."""
    try:
        decisions = await load_decisions(conversion.id)
        if not decisions or df is None or df.empty:
            return df
        idc = identity_columns_for(df, target_object)
        out, report = apply_decisions(df, idc, decisions)
        if report["applied"] or report["stale"]:
            logger.info(
                "conversion %s: applied %d duplicate decisions (%s) — %d rows -> %d, "
                "%d merged, %d stale",
                conversion.id, report["applied"], report["by_verdict"],
                report["rows_before"], report["rows_after"],
                report["rows_merged"], report["stale"])
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("conversion %s: decision application skipped: %s",
                       getattr(conversion, "id", "?"), exc)
        return df


def annotate_clusters(clusters: list[dict], df: pd.DataFrame,
                      identity_columns: list[str],
                      decided: dict[str, dict] | None = None,
                      learned_keep_all: set[str] | None = None) -> list[dict]:
    """Attach the stable keys (and any existing verdict) to scanner output.

    ``find_duplicate_clusters`` reports positional row indices; the UI needs the
    identity hashes so the decision it saves survives the next frame rebuild.
    """
    decided = decided or {}
    learned_keep_all = learned_keep_all or set()
    keys = row_keys_for(df, identity_columns)
    out = []
    for cl in clusters:
        mk = []
        for m in cl.get("members", []):
            r = m.get("row")
            k = keys[r] if isinstance(r, int) and 0 <= r < len(keys) else None
            m["key"] = k
            if k:
                mk.append(k)
        if not mk:
            continue
        ck = cluster_key(mk)
        cl["cluster_key"] = ck
        cl["member_keys"] = mk
        d = decided.get(ck)
        cl["decision"] = ({"verdict": d["verdict"], "survivor_key": d.get("survivor_key"),
                           "source": "conversion"} if d
                          else {"verdict": KEEP_ALL, "survivor_key": None,
                                "source": "learned"} if ck in learned_keep_all
                          else None)
        out.append(cl)
    return out
