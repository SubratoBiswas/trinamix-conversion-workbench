"""Apply user duplicate/cleansing decisions to a converted frame.

Pure and dependency-light (pandas only) so the rules can be unit tested without
the Beanie/Mongo stack — the same reason ``merge_dedupe`` was extracted.

The contract: given a frame and a set of decisions keyed by identity hash, return
a new frame plus a report of what was applied. Every writer path (CSV bundle,
xlsx, filled Oracle template) reads the frame this returns, so a decision made in
the review screen cannot fail to reach the file.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Optional

import pandas as pd

KEEP_SURVIVOR = "keep_survivor"
MERGE = "merge"
KEEP_ALL = "keep_all"
EXCLUDE = "exclude"

_BLANKS = {"", "nan", "none", "null", "na", "<na>"}

# Separates the identity hash from the per-row disambiguator in a row key.
# Absent from hex digests, so `split` cannot cut a legacy key in the wrong place.
_UID_SEP = "-"


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str("" if v is None else v)).strip().casefold()


def _is_blank(v: Any) -> bool:
    return _norm(v) in _BLANKS


def row_key(values: Iterable[Any]) -> str:
    """Stable identity hash for one row.

    Normalised (case/whitespace-insensitive) so cosmetic differences between two
    generations of the same record do not produce different keys — the review
    screen and the generator must agree on identity or a decision lands on the
    wrong row.
    """
    joined = "\x1f".join(_norm(v) for v in values)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:20]


def identity_part(key: Optional[str]) -> str:
    """The identity-hash prefix of a row key.

    Keys saved before per-row disambiguation existed carry no separator and are
    returned unchanged, so an older decision still resolves (against every row
    sharing that identity — the pre-fix behaviour).
    """
    return (key or "").split(_UID_SEP, 1)[0]


def row_keys_for(df: pd.DataFrame, identity_columns: list[str],
                 *, unique: bool = True) -> list[str]:
    """One key per row, of the form ``<identity>-<content><occurrence>``.

    WHY THE SUFFIX. The identity hash alone covers only the identity columns, so
    nine supplier rows whose names differ solely by trailing punctuation collapse
    to three keys. The review screen binds its survivor radio to this key, so
    nominating one row highlighted all of its identity-twins and "keep survivor"
    could not express WHICH physical row to keep — ``apply_decisions`` fell back
    to whichever happened to sit first in the frame.

    The suffix restores per-row addressability without reintroducing positional
    fragility: ``content`` hashes the WHOLE row, so it is still derived purely
    from the data and survives reordering. ``occurrence`` disambiguates rows that
    are byte-identical across every column — and for those, which one survives is
    immaterial by definition, so an ordinal that shifts with row count is safe.

    ``unique=False`` returns bare identity hashes (what ``cluster_key`` needs).
    """
    cols = [c for c in identity_columns if c in df.columns] or list(df.columns)
    if df.empty:
        return []
    sub = df[cols].astype(str)
    idents = [row_key(t) for t in sub.itertuples(index=False, name=None)]
    if not unique:
        return idents

    whole = df.astype(str)
    seen: dict[tuple[str, str], int] = {}
    out: list[str] = []
    for ident, t in zip(idents, whole.itertuples(index=False, name=None)):
        content = row_key(t)[:8]
        n = seen.get((ident, content), 0)
        seen[(ident, content)] = n + 1
        out.append(f"{ident}{_UID_SEP}{content}{n}")
    return out


def cluster_key(member_keys: Iterable[str]) -> str:
    """Identity of a duplicate CLUSTER: order-independent over its members.

    Sorted before hashing because cluster membership order depends on the block
    iteration order inside ``find_duplicate_clusters``, which is not stable across
    runs. Without the sort the same cluster would key differently on a re-scan and
    the user's decision would appear to vanish.

    Reduced to identity parts first, so adding the per-row suffix above did NOT
    change any cluster key — decisions and cross-conversion ``keep_all`` learnings
    saved before that change still match.
    """
    idents = sorted(identity_part(k) for k in member_keys)
    return hashlib.sha1("|".join(idents).encode("utf-8")).hexdigest()[:20]


def golden_record(rows: list[dict], columns: list[str]) -> dict:
    """First NON-BLANK value per column, in the given row order.

    Same survivorship rule ``merge_dedupe._survive`` applies across sources, but
    here the order is the user's (survivor first) rather than source priority.
    Merging is the reason a cluster decision beats a plain row checkbox: the five
    '3X Motion Technologies' rows each populate different columns, and keeping any
    single one loses the others' data.
    """
    out: dict = {}
    for c in columns:
        val = ""
        for r in rows:
            v = r.get(c, "")
            if not _is_blank(v):
                val = v
                break
        out[c] = val
    return out


def apply_decisions(df: pd.DataFrame, identity_columns: list[str],
                    decisions: list[dict]) -> tuple[pd.DataFrame, dict]:
    """Apply duplicate decisions to ``df``.

    ``decisions``: [{decision_key, verdict, survivor_key?, member_keys[]}]

    Rows are matched by identity hash, never by position. A decision whose members
    are no longer present is reported as ``stale`` rather than silently ignored —
    that is the signal the underlying data changed since the review.
    """
    report = {"applied": 0, "stale": 0, "rows_before": int(len(df)),
              "rows_removed": 0, "rows_merged": 0, "by_verdict": {}}
    if df.empty or not decisions:
        report["rows_after"] = int(len(df))
        return df, report

    keys = row_keys_for(df, identity_columns)
    pos_by_key: dict[str, list[int]] = {}
    pos_by_ident: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        pos_by_key.setdefault(k, []).append(i)
        pos_by_ident.setdefault(identity_part(k), []).append(i)

    def positions_for(k: str) -> list[int]:
        """Rows a stored key refers to.

        Exact match first. Otherwise fall back to the identity prefix, which
        covers two cases: a decision saved before per-row keys existed, and a row
        whose non-identity columns changed since the review (its content hash
        moves, its identity does not). Falling back is deliberately coarse — it
        re-selects every identity-twin — but losing the decision entirely would
        silently ship rows the analyst already ruled on.
        """
        if k in pos_by_key:
            return pos_by_key[k]
        return pos_by_ident.get(identity_part(k), [])

    columns = list(df.columns)
    records = df.to_dict("records")
    drop: set[int] = set()
    replace: dict[int, dict] = {}

    for d in decisions:
        verdict = (d.get("verdict") or "").strip()
        members = [k for k in (d.get("member_keys") or []) if positions_for(k)]
        if not members or verdict not in {KEEP_SURVIVOR, MERGE, KEEP_ALL, EXCLUDE}:
            report["stale"] += 1
            continue
        # A key can still match several rows (byte-identical twins, or a legacy
        # identity-only key); treat them all as cluster members, otherwise an
        # exact twin would survive a merge.
        positions: list[int] = []
        for k in members:
            positions.extend(positions_for(k))
        positions = sorted(set(positions))

        report["by_verdict"][verdict] = report["by_verdict"].get(verdict, 0) + 1
        if verdict == KEEP_ALL:
            report["applied"] += 1
            continue
        if verdict == EXCLUDE:
            drop.update(positions)
            report["applied"] += 1
            continue

        surv = d.get("survivor_key")
        surv_pos = (positions_for(surv) or [None])[0] if surv else None
        if surv_pos is None or surv_pos not in positions:
            surv_pos = positions[0]      # nothing nominated — keep the first
        if verdict == MERGE:
            ordered = [records[surv_pos]] + [records[p] for p in positions if p != surv_pos]
            replace[surv_pos] = golden_record(ordered, columns)
            report["rows_merged"] += 1
        drop.update(p for p in positions if p != surv_pos)
        report["applied"] += 1

    for pos, rec in replace.items():
        records[pos] = rec
    kept = [records[i] for i in range(len(records)) if i not in drop]
    out = pd.DataFrame(kept, columns=columns) if kept else df.iloc[0:0].copy()
    report["rows_removed"] = len(drop)
    report["rows_after"] = int(len(out))
    return out.reset_index(drop=True), report
