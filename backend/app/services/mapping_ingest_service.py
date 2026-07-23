"""Analyse an uploaded mapping document into a reviewable proposal.

Three things make real consultant mapping workbooks hard to read automatically, and
all three were observed in the NextPower documents:

  * the header row is not row 1 — there is a title or a file link above it;
  * every workbook names its columns differently ("SS Vendors Field Map",
    "Mapped Oracle Attribute", "Items Attributes");
  * one workbook holds a tab per source system, each with its own layout.

So layout detection runs deterministically first (fast, free, and right whenever the
headers use familiar words) and falls back to the LLM only for the sheets it could
not resolve. The model is asked ONLY to identify which column is which — never to
invent a mapping. Every mapping row still comes verbatim from the document.

Nothing here writes to the learning library. Analysis produces a MappingProposal for
a human to review; see apply_proposal() for the write path.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx
import pandas as pd

from app.config import settings
from app.models.learned import LearnedMapping
from app.models.mapping_proposal import MappingProposal, ProposedMapping

logger = logging.getLogger(__name__)

_NORM = re.compile(r"[^a-z0-9]+")
_n = lambda s: _NORM.sub("", str(s).lower()) if s is not None else ""

# Deterministic vocabulary. Kept generous because a wrong-but-confident match is
# worse than no match: picking the target column as the source silently imports
# every mapping backwards.
_SRC = ("source field", "source column", "source attribute", "legacy field",
        "legacy column", "from field", "from column", "source name", "input field",
        "external field", "field map", "source system column", "tracker attributes",
        "items attributes", "item attributes", "source")
_TGT = ("mapped oracle attribute", "oracle field", "oracle column", "oracle attribute",
        "target field", "target column", "fbdi field", "fbdi column", "fusion field",
        "target attribute", "destination field", "interface column", "mapped oracle")
_OBJ = ("target object", "business object", "entities", "entity", "fbdi object",
        "load object", "import object", "object")
_SHEET = ("fbdi work sheet", "fbdi sheet", "interface sheet", "target sheet",
          "worksheet", "interface table", "sheet")
_SYS = ("source system", "source erp", "source app", "system", "erp")
_NOTE = ("comments", "comment", "notes", "note", "remarks", "rationale")


def _find(headers: list[str], aliases: tuple[str, ...]) -> Optional[int]:
    """Longest alias wins, so 'mapped oracle attribute' beats a bare 'attribute'."""
    best, best_len = None, -1
    for i, h in enumerate(headers):
        hn = _n(h)
        if not hn:
            continue
        for a in aliases:
            an = _n(a)
            if hn == an and len(an) > best_len:
                best, best_len = i, len(an) + 100      # exact match outranks contains
            elif an in hn and len(an) > best_len:
                best, best_len = i, len(an)
    return best


_PAREN = re.compile(r"\([^)]*\)")
_INSTRUCTION = re.compile(r"\s*(?:->|→|=>)\s*.*$")


def _source_candidates(cell: str) -> list[str]:
    """A mapping cell is prose, not a column name.

    Analysts write "Country / Country Code", "Taxpayer ID / Tax ID / Tax Number",
    "Entity Type (transformation needed: e.g., Business → Corporation)". Compared
    literally against the library, every one of those reads as a contradiction — a
    first pass over the NextPower supplier document raised 57 conflicts, and all of
    them were this. So a cell is reduced to the column names it actually offers.

    Alternatives are separated by " / " WITH spaces. The bare slash is left alone
    because real source columns contain one — "Billing State/Province" is a single
    NetSuite column, not two candidates.
    """
    raw = (cell or "").strip()
    if not raw:
        return []
    s = _PAREN.sub(" ", raw)
    out: list[str] = []
    for part in re.split(r"\s+/\s+", s):
        p = _INSTRUCTION.sub("", part).strip(" .;:,")
        if p:
            out.append(p)
    return out or [raw]


def _txt(c: Any) -> str:
    """Cell to text. NaN must be treated as EMPTY: pandas fills short rows with NaN,
    and str(nan) is the non-empty string 'nan', which made every row look full-width
    and defeated header detection entirely — the title row above the real headers
    then won on the earliest-row tiebreak."""
    if c is None or c != c:            # c != c is True only for NaN
        return ""
    return str(c).strip()


def _header_row(grid: list[list[Any]], limit: int = 12) -> int:
    """The header row is the densest early row whose cells are short label-like text."""
    best, best_score = 0, -1.0
    for i, row in enumerate(grid[:limit]):
        cells = [t for t in (_txt(c) for c in row) if t]
        if len(cells) < 2:
            continue
        shortish = sum(1 for c in cells if len(c) <= 45)
        score = len(cells) * 2 + shortish - (i * 0.5)   # earlier rows preferred, mildly
        if score > best_score:
            best, best_score = i, score
    return best


async def _ai_layout(headers: list[str], sample: list[list[Any]]) -> dict:
    """Ask the model which column is which. It never proposes a mapping."""
    key = (settings.ANTHROPIC_API_KEY or "").strip()
    if not key:
        return {}
    preview = [headers] + [[("" if c is None else str(c))[:40] for c in r] for r in sample[:6]]
    prompt = (
        "This is a data-migration MAPPING WORKBOOK: each row says which legacy source "
        "column feeds which Oracle Fusion FBDI target field.\n\n"
        "Identify which COLUMN INDEX (0-based) holds each role. Use null when a role is "
        "absent. Do not invent mappings — only classify the columns.\n\n"
        "Roles: source_field (the legacy/source column name), target_field (the Oracle/"
        "FBDI field name), target_object (Supplier/Item/Customer...), fbdi_sheet "
        "(interface table), source_system, notes.\n\n"
        f"Header row: {json.dumps(headers)}\n"
        f"First rows: {json.dumps(preview[1:])}\n\n"
        'Reply with ONLY JSON: {"source_field":n,"target_field":n,"target_object":n,'
        '"fbdi_sheet":n,"source_system":n,"notes":n,"confidence":"high|medium|low",'
        '"note":"one short sentence"}'
    )
    try:
        async with httpx.AsyncClient(timeout=45.0) as cx:
            r = await cx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": settings.ANTHROPIC_MODEL, "max_tokens": 600,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            r.raise_for_status()
            txt = "".join(b.get("text", "") for b in r.json().get("content", [])
                          if b.get("type") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("mapping-ingest: AI layout detection failed: %s", exc)
        return {}


async def _resolve_layout(headers: list[str], sample: list[list[Any]]) -> tuple[dict, str, str]:
    """Deterministic first; the model only sees sheets that could not be resolved."""
    cols = {"source_field": _find(headers, _SRC), "target_field": _find(headers, _TGT),
            "target_object": _find(headers, _OBJ), "fbdi_sheet": _find(headers, _SHEET),
            "source_system": _find(headers, _SYS), "notes": _find(headers, _NOTE)}
    ok = (cols["source_field"] is not None and cols["target_field"] is not None
          and cols["source_field"] != cols["target_field"])
    if ok:
        return cols, "deterministic", "Columns recognised from their headers."
    ai = await _ai_layout(headers, sample)
    if ai:
        for k in list(cols):
            v = ai.get(k)
            if isinstance(v, int) and 0 <= v < len(headers):
                cols[k] = v
        ok = (cols["source_field"] is not None and cols["target_field"] is not None
              and cols["source_field"] != cols["target_field"])
        if ok:
            return (cols, "ai",
                    f"Headers were unfamiliar, so the layout was identified by AI "
                    f"({ai.get('confidence', 'unknown')} confidence). {ai.get('note', '')}".strip())
    return cols, "failed", "Could not identify a source column and a target column."


async def analyze_mapping_file(
    path: str, *, file_name: str, client_id=None, default_object: Optional[str] = None,
    source_system: Optional[str] = None, uploaded_by: str = "",
) -> MappingProposal:
    """Parse every sheet, classify each row against the library, save a proposal."""
    try:
        book = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    except Exception:
        book = {"csv": pd.read_csv(path, header=None, dtype=object,
                                   sep=None, engine="python", on_bad_lines="skip")}

    proposal = MappingProposal(
        file_name=file_name, client_id=client_id, target_object=default_object,
        source_system=source_system, uploaded_by=uploaded_by,
    )
    seen: set[tuple[str, str, str]] = set()
    methods: set[str] = set()
    notes: list[str] = []
    row_no = 0

    for sheet_name, df in book.items():
        grid = df.values.tolist()
        if not grid:
            continue
        h_idx = _header_row(grid)
        headers = [_txt(c) for c in grid[h_idx]]
        cols, method, note = await _resolve_layout(headers, grid[h_idx + 1:])
        if method == "failed":
            proposal.count_skipped += max(0, len(grid) - h_idx - 1)
            notes.append(f"{sheet_name}: {note}")
            continue
        methods.add(method)
        notes.append(f"{sheet_name}: {note}")
        proposal.detected_columns[str(sheet_name)] = {
            k: (headers[v] if isinstance(v, int) and v < len(headers) else None)
            for k, v in cols.items()
        }

        def cell(row: list[Any], i: Optional[int]) -> str:
            return "" if i is None or i >= len(row) else _txt(row[i])

        for raw in grid[h_idx + 1:]:
            src = cell(raw, cols["source_field"])
            tgt = cell(raw, cols["target_field"])
            if not src or not tgt:
                proposal.count_skipped += 1
                continue
            obj = cell(raw, cols["target_object"]) or default_object or ""
            if not obj:
                proposal.count_skipped += 1
                continue
            cands = _source_candidates(src)
            if not cands:
                proposal.count_skipped += 1
                continue
            key = (_n(obj), _n(tgt), _n(cands[0]))
            if key in seen:
                continue
            seen.add(key)
            row_no += 1
            proposal.rows.append(ProposedMapping(
                row_no=row_no, target_object=obj.strip(), target_field=tgt,
                source_field=cands[0], source_alternatives=cands[1:],
                source_raw=(src if src != cands[0] else None),
                fbdi_sheet=cell(raw, cols["fbdi_sheet"]) or None,
                source_system=cell(raw, cols["source_system"]) or source_system,
                notes=cell(raw, cols["notes"]) or None,
            ))

    proposal.layout_method = ("mixed" if len(methods) > 1 else (methods.pop() if methods else "failed"))
    proposal.layout_note = " · ".join(notes[:8])
    await _classify(proposal)
    await proposal.insert()
    return proposal


async def _classify(proposal: MappingProposal) -> None:
    """Compare each proposed row with what the library already believes, and
    cross-reference any previous gold output for the same field."""
    objects = {r.target_object for r in proposal.rows}
    from app.services.client_service import scope_query
    scope = await scope_query(proposal.client_id)
    existing = await LearnedMapping.find({
        "kind": "column_mapping", "target_object": {"$in": list(objects)}, **scope
    }).to_list()

    by_target: dict[tuple[str, str], list[LearnedMapping]] = {}
    for lm in existing:
        if lm.target_field:
            by_target.setdefault((_n(lm.target_object), _n(lm.target_field)), []).append(lm)

    # Previous gold output for these objects: mappings and constant defaults the tool
    # derived from an uploaded known-good file (captured_from mentions "gold" or the
    # kind is example_default / reference_standard). Lets the reviewer see what the
    # last real load actually used for each field alongside the document's proposal.
    gold_by_target: dict[tuple[str, str], LearnedMapping] = {}
    try:
        golds = await LearnedMapping.find({
            "target_object": {"$in": list(objects)},
            "kind": {"$in": ["column_mapping", "example_default", "reference_standard"]},
            **scope,
        }).to_list()
        for lm in golds:
            cf = (lm.captured_from or "").lower()
            is_gold = ("gold" in cf or "example_output" in cf or lm.kind in ("example_default", "reference_standard"))
            if is_gold and lm.target_field:
                gold_by_target.setdefault((_n(lm.target_object), _n(lm.target_field)), lm)
    except Exception:  # noqa: BLE001
        gold_by_target = {}

    def _gold(r) -> None:
        g = gold_by_target.get((_n(r.target_object), _n(r.target_field)))
        if g is not None:
            r.gold_source = g.original_value if g.kind == "column_mapping" else (g.resolved_value or "(constant)")
            r.gold_note = f"gold: {g.captured_from}" if g.captured_from else "from a previous gold output"

    for r in proposal.rows:
        _gold(r)
        cands = by_target.get((_n(r.target_object), _n(r.target_field)), [])
        if cands:
            r.is_learnt = True
            r.learnt_from = cands[0].captured_from
        if not cands:
            r.status = "new"
            proposal.count_new += 1
            continue
        # Match on ANY column the document offers for this field, not just the first.
        # "Country / Country Code" against a library that says "Country" is agreement,
        # not a contradiction — and treating it as one buried the real conflicts.
        offered = {_n(x) for x in ([r.source_field] + list(r.source_alternatives)) if x}
        same = next((c for c in cands if _n(c.original_value) in offered), None)
        if same is not None:
            # Keep the library's wording so applying an equivalent row is a no-op
            # rather than a rewrite that churns captured_from on every re-upload.
            r.source_field = same.original_value
            if (same.rule_type or None) == (r.rule_type or None):
                r.status = "unchanged"
                proposal.count_unchanged += 1
            else:
                r.status = "conflict"
                r.current_source_field = same.original_value
                r.current_rule_type = same.rule_type
                r.current_captured_from = same.captured_from
                r.existing_learning_id = same.id
                r.conflict_reason = (
                    f"Same source column, but the library applies rule "
                    f"{same.rule_type or 'none'} and the document implies {r.rule_type or 'none'}.")
                proposal.count_conflict += 1
            continue
        # a different source column already feeds this target field — a real contradiction
        prev = cands[0]
        r.status = "conflict"
        r.current_source_field = prev.original_value
        r.current_rule_type = prev.rule_type
        r.current_captured_from = prev.captured_from
        r.existing_learning_id = prev.id
        r.conflict_reason = (
            f'"{r.target_field}" is currently mapped from "{prev.original_value}"'
            f'{" (from " + prev.captured_from + ")" if prev.captured_from else ""}; '
            f'this document maps it from "{r.source_field}".')
        proposal.count_conflict += 1


async def apply_proposal(proposal: MappingProposal, *, applied_by: str = "") -> dict:
    """Write the approved rows into the library, then push them onto existing work.

    Approval semantics:
      * "new" and "unchanged" rows apply unless explicitly rejected — a reviewer
        should not have to tick 200 uncontroversial rows to accept a document;
      * a "conflict" row applies ONLY on an explicit approval, because it
        overwrites something the tool already believed.

    An approved conflict REPLACES the existing learning in place rather than adding
    a second one, otherwise both would survive and the mapper would arbitrate
    between them later — which is the ambiguity the review step exists to remove.
    """
    from app.models.conversion import Conversion
    from app.models.mapping import MappingSuggestion
    from app.services.learning_service import apply_learned_to_conversion

    written = 0
    objects: set[str] = set()
    for r in proposal.rows:
        if r.decision == "rejected":
            continue
        if r.status == "conflict" and r.decision != "approved":
            continue          # unreviewed contradictions are never applied silently
        if r.status == "unchanged" and r.decision != "rejected":
            objects.add(r.target_object)
            continue          # already correct in the library; nothing to write

        # A manual override wins over the document's proposed source, and its reason
        # is recorded in captured_from so the audit trail shows WHY a human changed it.
        src = r.override_source or r.source_field
        captured = (f"manual override: {r.override_reason}"
                    if r.override_source and r.override_reason
                    else f"mapping document: {proposal.file_name}")
        cfg = r.rule_config if r.rule_type else {
            "source_column": src, "fbdi_sheet": r.fbdi_sheet,
            "confidence": "High", "notes": r.notes,
        }
        existing = None
        if r.existing_learning_id:
            existing = await LearnedMapping.get(r.existing_learning_id)
        if existing is not None:
            await existing.set({
                "original_value": src, "rule_type": r.rule_type,
                "rule_config": cfg, "source_erp": r.source_system,
                "captured_from": captured, "captured_by": applied_by,
            })
        else:
            await LearnedMapping(
                kind="column_mapping", category="Column Mapping Alias",
                original_value=src, resolved_value=r.target_field,
                target_object=r.target_object, target_field=r.target_field,
                rule_type=r.rule_type, rule_config=cfg,
                client_id=proposal.client_id, is_global=proposal.client_id is None,
                source_erp=r.source_system,
                captured_from=captured, captured_by=applied_by,
            ).insert()
        written += 1
        objects.add(r.target_object)

    # Push onto EXISTING conversions for the same client + objects, so the document
    # corrects work already in flight rather than only future conversions. force=True
    # lets it re-decide rows the learning engine itself approved earlier; a human's
    # own "overridden"/"rejected" choices are still respected inside that call.
    touched = 0
    if objects:
        from app.services.client_service import client_id_for_conversion
        for conv in await Conversion.find_all().to_list():
            try:
                if proposal.client_id is not None:
                    if await client_id_for_conversion(conv) != proposal.client_id:
                        continue
                maps = await MappingSuggestion.find(
                    MappingSuggestion.conversion_id == conv.id).to_list()
                if not maps:
                    continue
                if await apply_learned_to_conversion(conv, maps, force=True):
                    touched += 1
            except Exception:                                   # noqa: BLE001
                continue        # one bad conversion must not abort the rollout

    proposal.status = "applied"
    proposal.learnings_written = written
    proposal.conversions_touched = touched
    proposal.applied_by = applied_by
    from datetime import datetime as _dt
    proposal.applied_at = _dt.utcnow()
    await proposal.save()
    return {"learnings_written": written, "conversions_touched": touched,
            "objects": sorted(objects)}


async def vet_proposal_with_ai(proposal: MappingProposal, *, only_no_verdict: bool = True) -> dict:
    """Ask the model to judge each row's proposed mapping.

    For a plain row: does the source column plausibly feed the target field?
    For a CONFLICT: which of the document's source vs the library's current source
    is the more likely mapping? The verdict + a short reason is stored on the row so
    a return visit does not re-spend tokens. Rows already carrying a verdict are
    skipped unless only_no_verdict is False.
    """
    import json as _json
    import re as _re

    import httpx as _httpx

    from app.config import settings as _settings

    key = (_settings.ANTHROPIC_API_KEY or "").strip()
    todo = [r for r in proposal.rows
            if (r.status in ("conflict", "new"))
            and not (only_no_verdict and r.ai_verdict)]
    if not key or not todo:
        return {"vetted": 0, "sent": len(todo)}

    def _line(i: int, r) -> str:
        base = (f'{i}. Object "{r.target_object}", Oracle field "{r.target_field}". '
                f'Document maps it from source column "{r.source_field}".')
        if r.status == "conflict" and r.current_source_field:
            base += f' The library currently maps it from "{r.current_source_field}".'
        if r.gold_source:
            base += f' A previous gold load used "{r.gold_source}".'
        return base

    vetted = 0
    for chunk_start in range(0, len(todo), 30):
        chunk = todo[chunk_start:chunk_start + 30]
        prompt = (
            "You are validating data-migration mappings from a legacy HR/ERP/PLM system "
            "into Oracle Fusion. For EACH item decide, by what the data MEANS, whether the "
            "document's proposed source column genuinely populates that Oracle field. For a "
            "conflict, also say which source (document or library) is the better mapping.\n\n"
            + "\n".join(_line(i, r) for i, r in enumerate(chunk)) +
            '\n\nReply ONLY with a JSON array, one object per item index: '
            '[{"i":0,"verdict":"plausible|unlikely|wrong","recommends":"document|library|neither",'
            '"reason":"one short sentence"}]. Keep reasons under 18 words.')
        try:
            async with _httpx.AsyncClient(timeout=55.0) as cx:
                resp = await cx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": _settings.ANTHROPIC_MODEL, "max_tokens": 1500,
                          "messages": [{"role": "user", "content": prompt}]})
                resp.raise_for_status()
                txt = "".join(b.get("text", "") for b in resp.json().get("content", [])
                              if b.get("type") == "text")
            m = _re.search(r"\[.*\]", txt, _re.S)
            for row in (_json.loads(m.group(0)) if m else []):
                idx = row.get("i")
                if isinstance(idx, int) and 0 <= idx < len(chunk):
                    tr = chunk[idx]
                    tr.ai_verdict = str(row.get("verdict", "")).lower() or None
                    tr.ai_recommends = str(row.get("recommends", "")).lower() or None
                    tr.ai_reason = str(row.get("reason", "")).strip() or None
                    vetted += 1
        except Exception:                                       # noqa: BLE001
            continue
    await proposal.save()
    return {"vetted": vetted, "sent": len(todo)}
