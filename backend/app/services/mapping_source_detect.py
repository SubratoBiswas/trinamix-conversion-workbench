"""Work out which SOURCE SYSTEM a mapping document maps from.

Mapping workbooks arrive in whatever shape the client's analyst built. One names
the source system in a dedicated column, another encodes it in the source column
header ("NetSuite Column", "SyteLine Field"), a third only says it in the sheet
name or file name, and a fourth carries SEVERAL systems side by side — the real
``Oracle-NetSuite-SyteLine`` sheet has 164 rows and 92 columns with an Oracle
field, a NetSuite column and an eBOS column on every row.

Asking the uploader to pick one from a dropdown loses that. A single-value form
field cannot describe a three-source workbook, and picking wrong imports every
mapping under the wrong system, which silently poisons the cross-system learning
key.

So detection is layered, cheapest and most certain first:

  1. an explicit source-system COLUMN (the values are the answer)
  2. the source column's own HEADER ("NetSuite Column" -> netsuite)
  3. any other header mentioning a known system (catches side-by-side layouts)
  4. the SHEET name, then the FILE name
  5. the model, only when the first four found nothing

Every hit records where it came from, so a reviewer can tell "the header said so"
from "the model guessed". Pure except for the optional AI step: stdlib only, no
DB, no network in the deterministic path.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Optional

from app.source_systems import PRIORITY_SOURCES, SOURCE_SYSTEMS, VALID_CODES

logger = logging.getLogger(__name__)

_NORM = re.compile(r"[^a-z0-9]+")


def _n(s: Any) -> str:
    return _NORM.sub("", str(s).lower()) if s is not None else ""


# Spellings seen in real client workbooks, beyond the catalogue's display names.
# eBOS is the client's own legacy system and maps to `custom`; it appears as a
# column header far more often than the word "custom" ever will.
_ALIASES: dict[str, tuple[str, ...]] = {
    "netsuite": ("netsuite", "net suite", "ns", "oracle netsuite"),
    "syteline": ("syteline", "site line", "infor syteline", "infor", "sl"),
    "oracle_ebs": ("oracleebs", "ebs", "e business suite", "ebusiness suite",
                   "oracle apps", "oracle11i", "r12"),
    "arena": ("arena", "arena plm"),
    "sap_ecc": ("sapecc", "ecc", "sap r3", "sapr3"),
    "sap_s4": ("saps4", "s4hana", "s4 hana", "sap s4"),
    "workday": ("workday", "wd"),
    "jde": ("jde", "jdedwards", "jd edwards", "edwards"),
    "custom": ("ebos", "e bos", "legacy", "inhouse", "in house", "homegrown"),
}
# Two-letter aliases only ever match a WHOLE header token — "ns" inside
# "transactions" is not NetSuite, and that kind of hit is worse than no hit.
_SHORT = {"ns", "sl", "wd"}

# Never treat these as a source system: they are the TARGET, and matching them
# would import every mapping backwards.
_TARGET_WORDS = ("oracle fusion", "fusion", "fbdi", "target", "oracle field",
                 "oracle attribute", "oracle column")


def _match_text(text: str) -> list[str]:
    """Codes mentioned in a piece of text, most specific alias first."""
    t = _n(text)
    if not t:
        return []
    # A header naming the TARGET must not be read as a source. "Oracle EBS" is a
    # real source, so only reject when the text is target-ish AND not EBS.
    tw = str(text).strip().lower()
    if any(w in tw for w in _TARGET_WORDS) and not any(
            a in t for a in _ALIASES["oracle_ebs"]):
        return []
    hits: list[tuple[int, str]] = []
    for code, aliases in _ALIASES.items():
        for a in aliases:
            an = _n(a)
            if not an:
                continue
            if an in _SHORT:
                if an == t:                       # whole-header match only
                    hits.append((len(an), code))
            elif an in t:
                hits.append((len(an), code))
    seen, out = set(), []
    for _, code in sorted(hits, key=lambda kv: -kv[0]):
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def _hit(code, method, evidence, confidence, column=None):
    return {"system": code, "method": method, "evidence": evidence,
            "confidence": confidence, "column_index": column}


def detect_source_systems(
    headers: list[str],
    *,
    source_column: Optional[int] = None,
    system_column: Optional[int] = None,
    rows: Optional[list[list[Any]]] = None,
    sheet_name: str = "",
    file_name: str = "",
) -> list[dict]:
    """Every source system this sheet appears to map from, best evidence first.

    Returns [] when nothing is recognised — the caller then decides whether to
    ask the model. A list rather than one value because a workbook genuinely can
    map from several systems at once.
    """
    found: list[dict] = []
    seen: set[str] = set()

    def add(h):
        if h["system"] in VALID_CODES and h["system"] not in seen:
            seen.add(h["system"])
            found.append(h)

    # 1. An explicit source-system column — its VALUES are the answer, so this
    #    beats every inference below.
    if system_column is not None and rows:
        vals: dict[str, int] = {}
        for r in rows:
            if system_column < len(r):
                for code in _match_text(r[system_column]):
                    vals[code] = vals.get(code, 0) + 1
        for code, n in sorted(vals.items(), key=lambda kv: -kv[1]):
            add(_hit(code, "system_column", f"{n} row(s) name it", "high",
                     system_column))

    # 2. The source column's own header.
    if source_column is not None and 0 <= source_column < len(headers):
        h = headers[source_column]
        for code in _match_text(h):
            add(_hit(code, "source_header", f"source column header {h!r}", "high",
                     source_column))

    # 3. Any other header naming a system — this is what catches the side-by-side
    #    layouts a single dropdown cannot describe.
    for i, h in enumerate(headers):
        if i == source_column:
            continue
        for code in _match_text(h):
            add(_hit(code, "other_header", f"column header {str(h).strip()!r}",
                     "medium", i))

    # 4. Sheet name, then file name. Weaker: a name can lag what is inside.
    for code in _match_text(sheet_name):
        add(_hit(code, "sheet_name", f"sheet {sheet_name!r}", "medium"))
    for code in _match_text(file_name):
        add(_hit(code, "file_name", f"file {file_name!r}", "low"))

    return found


async def ai_detect_source_system(
    headers: list[str], sample: list[list[Any]], *, sheet_name: str = "",
    file_name: str = "",
) -> Optional[dict]:
    """Ask the model, only when the deterministic pass found nothing.

    Constrained to the catalogue: a free-text answer would create a source-system
    value nothing else in the tool recognises, which is worse than no answer.
    """
    from app.config import settings
    key = (settings.ANTHROPIC_API_KEY or "").strip()
    if not key:
        return None
    import httpx

    codes = [s.code for s in SOURCE_SYSTEMS]
    preview = [[("" if c is None else str(c))[:40] for c in r] for r in sample[:8]]
    prompt = (
        "This is a data-migration MAPPING WORKBOOK: each row says which LEGACY "
        "SOURCE column feeds which Oracle Fusion target field.\n\n"
        "Identify the legacy SOURCE system(s) it maps FROM. The target is always "
        "Oracle Fusion — never answer with Fusion or FBDI.\n\n"
        f"File: {file_name!r}   Sheet: {sheet_name!r}\n"
        f"Headers: {json.dumps(headers)}\n"
        f"Rows: {json.dumps(preview)}\n\n"
        f"Answer ONLY with codes from this list: {json.dumps(codes)}. "
        "Use 'custom' for a client-specific legacy system such as eBOS. "
        "If you cannot tell, return an empty systems list.\n"
        'Reply with ONLY JSON: {"systems":["code",...],'
        '"confidence":"high|medium|low","note":"one short sentence"}'
    )
    try:
        async with httpx.AsyncClient(timeout=45.0) as cx:
            r = await cx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": settings.ANTHROPIC_MODEL, "max_tokens": 400,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            r.raise_for_status()
            txt = "".join(b.get("text", "") for b in r.json().get("content", [])
                          if b.get("type") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        systems = [c for c in (data.get("systems") or []) if c in VALID_CODES]
        if not systems:
            return None
        return {"systems": systems,
                "confidence": data.get("confidence", "low"),
                "note": data.get("note", "")}
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("mapping-ingest: AI source-system detection failed: %s", exc)
        return None


async def resolve_source_systems(
    headers: list[str], sample: list[list[Any]], *,
    source_column: Optional[int] = None, system_column: Optional[int] = None,
    rows: Optional[list[list[Any]]] = None, sheet_name: str = "",
    file_name: str = "", declared: Optional[str] = None,
) -> dict:
    """Deterministic first, model only as a fallback, uploader always wins.

    ``declared`` is what the person chose in the form. It is returned as the
    answer when set — an explicit human choice should not be argued with — but
    detection still runs so the UI can flag a disagreement rather than bury it.
    """
    from app.source_systems import normalize_code

    hits = detect_source_systems(
        headers, source_column=source_column, system_column=system_column,
        rows=rows, sheet_name=sheet_name, file_name=file_name)
    method = "deterministic" if hits else "none"
    note = ""

    if not hits:
        ai = await ai_detect_source_system(
            headers, sample, sheet_name=sheet_name, file_name=file_name)
        if ai:
            method = "ai"
            note = ai.get("note", "")
            hits = [_hit(c, "ai", "identified by AI", ai.get("confidence", "low"))
                    for c in ai["systems"]]

    # Prefer the active client's systems when several are equally supported —
    # the same tie-break the dataset classifier uses.
    hits.sort(key=lambda h: (h["system"] not in PRIORITY_SOURCES,))

    declared_code = normalize_code(declared) if declared else None
    detected = [h["system"] for h in hits]
    return {
        "systems": detected,
        "primary": declared_code or (detected[0] if detected else None),
        "declared": declared_code,
        # A silent override is how a workbook ends up filed under the wrong
        # system; the UI shows this as a warning.
        "declared_conflicts": bool(declared_code and detected
                                   and declared_code not in detected),
        "method": "declared" if declared_code else method,
        "note": note,
        "hits": hits,
    }
