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
# The PHYSICAL column name, when the workbook carries one alongside the label.
#
# Debayon Mallik, 31-Jul: "for mapping we must consider the Source Table Column name
# (the last column of the mapping file) as the source columns." A mapping workbook is
# written by a functional analyst, who names columns the way the legacy UI shows them
# — "1099 Eligible", "Permanent Account Number (PAN)", "CNPJ/CPF" — while the extract
# is a database dump whose headers are federal_reportable, pan, cnpj. Binding on the
# label produces a mapping that reads as mapped on screen and writes an EMPTY column,
# which is this tool's most expensive failure precisely because nothing looks wrong.
# Against the NextPower supplier workbook and the real NetSuite extract the label
# bound 50 of 55 rows; the physical name bound 55 of 55.
#
# It is a PER-ROW preference, not a per-sheet one. The same workbook's SyteLine rows
# leave this column empty, because there the label already IS the physical name
# (vend_num, addr##1, terms_code) — switching the whole sheet to the technical column
# would have dropped all 40 of them.
_SRC_PHYSICAL = ("source table column name", "source table column", "source table field",
                 "physical column name", "physical column", "table column name",
                 "database column", "db column", "technical column name",
                 "technical column", "extract column name", "extract column")


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
            "source_system": _find(headers, _SYS), "notes": _find(headers, _NOTE),
            "source_physical": _find(headers, _SRC_PHYSICAL)}
    # A physical-name column that resolved to the SAME column as the label is not a
    # second column; dropping it here keeps the row loop's "prefer physical" rule from
    # becoming a no-op that still claims to have applied.
    if cols["source_physical"] is not None and cols["source_physical"] in (
            cols["source_field"], cols["target_field"]):
        cols["source_physical"] = None
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
    sys_seen: set[str] = set()
    sys_conflicts: list[str] = []
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

        # Which legacy system this sheet maps FROM. Detected rather than taken
        # from the upload form, because mapping workbooks differ: some name it in
        # a column, some only in the source column's header, some carry several
        # systems side by side — and a single dropdown cannot describe that.
        from app.services.mapping_source_detect import resolve_source_systems
        sysinfo = await resolve_source_systems(
            headers, grid[h_idx + 1:],
            source_column=cols["source_field"],
            system_column=cols["source_system"],
            rows=grid[h_idx + 1:],
            sheet_name=str(sheet_name), file_name=file_name,
            declared=source_system,
        )
        proposal.detected_source_systems[str(sheet_name)] = sysinfo
        sheet_system = sysinfo.get("primary")
        if sysinfo.get("systems"):
            sys_seen.update(sysinfo["systems"])
        if sysinfo.get("declared_conflicts"):
            sys_conflicts.append(
                f"{sheet_name}: file says "
                f"{', '.join(sysinfo['systems'])}, upload declared "
                f"{sysinfo['declared']}")
        if sysinfo.get("note"):
            notes.append(f"{sheet_name}: {sysinfo['note']}")

        def cell(row: list[Any], i: Optional[int]) -> str:
            return "" if i is None or i >= len(row) else _txt(row[i])

        physical_used = 0
        for raw in grid[h_idx + 1:]:
            label = cell(raw, cols["source_field"])
            # PREFER THE PHYSICAL NAME, ROW BY ROW. Where the workbook gives one it is
            # the name the extract actually has; where the cell is blank the label is
            # already physical (every SyteLine row in the NextPower workbook), so the
            # fallback is not a degradation, it is the other half of the same rule.
            physical = cell(raw, cols["source_physical"])
            src = physical or label
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
            if physical:
                physical_used += 1
            proposal.rows.append(ProposedMapping(
                row_no=row_no, target_object=obj.strip(), target_field=tgt,
                source_field=cands[0], source_alternatives=cands[1:],
                # The analyst's own wording for the column is kept when it differs, so
                # the proposal screen can still show "1099 Eligible" next to
                # federal_reportable rather than a name nobody in the room recognises.
                source_raw=(src if src != cands[0] else (label if physical else None)),
                fbdi_sheet=cell(raw, cols["fbdi_sheet"]) or None,
                # Row value, then the sheet's detected system, then the form.
                # Detection sits ahead of the form because a per-sheet answer is
                # more specific than one dropdown applied to the whole workbook.
                source_system=(cell(raw, cols["source_system"])
                               or sheet_system or source_system),
                notes=cell(raw, cols["notes"]) or None,
            ))
        if physical_used:
            notes.append(
                f"{sheet_name}: {physical_used} row(s) bound to "
                f"'{headers[cols['source_physical']]}' — the physical column name — "
                f"rather than the analyst's label.")

    proposal.layout_method = ("mixed" if len(methods) > 1 else (methods.pop() if methods else "failed"))
    proposal.layout_note = " · ".join(notes[:8])
    proposal.source_systems = sorted(sys_seen)
    # An uploader's choice is honoured, but a disagreement with the file is
    # surfaced rather than silently accepted — filing a workbook under the wrong
    # system poisons the cross-system learning key.
    proposal.source_system_conflict = " · ".join(sys_conflicts[:4]) or None
    if not proposal.source_system and sys_seen:
        proposal.source_system = sorted(sys_seen)[0]
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
        # One more dated entry. Applying a proposal is an explicit user action,
        # so a decision the analyst retired is revived in place rather than
        # duplicated beside its own tombstone.
        from app.services.mapping_store import record_learning
        await record_learning(
            kind="column_mapping", category="Column Mapping Alias",
            original_value=src, resolved_value=r.target_field,
            target_object=r.target_object, target_field=r.target_field,
            rule_type=r.rule_type, rule_config=cfg,
            client_id=proposal.client_id, source_erp=r.source_system,
            captured_from=captured, captured_by=applied_by, revive=True,
        )
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

    from datetime import datetime as _dt
    proposal.status = "applied"
    proposal.learnings_written = written
    proposal.conversions_touched = touched
    proposal.applied_by = applied_by
    proposal.applied_at = _dt.utcnow()
    await proposal.save()

    # This document now GOVERNS its modules. Everything the previous one asserted
    # and this one does not must stop applying, or the two files stay silently in
    # force together — see supersede_previous.
    sup = await supersede_previous(proposal, applied_by=applied_by)

    return {"learnings_written": written, "conversions_touched": touched,
            "objects": sorted(objects), **sup}


async def _asserted_pairs(proposal: MappingProposal) -> set[tuple[str, str]]:
    """(target_object, target_field) this document actually put into force."""
    out: set[tuple[str, str]] = set()
    for r in proposal.rows:
        if r.decision == "rejected":
            continue
        if r.status == "conflict" and r.decision != "approved":
            continue
        if r.target_object and r.target_field:
            out.add((str(r.target_object).strip().lower(),
                     str(r.target_field).strip().lower()))
    return out


async def supersede_previous(proposal: MappingProposal, *,
                             applied_by: str = "") -> dict:
    """Retire everything the older mapping documents for these modules asserted.

    The rule the analysts asked for: for one client and one module, only the
    NEWEST mapping document counts. Without this, applying a v2 file leaves every
    v1 mapping it no longer mentions quietly in force — a field dropped or
    re-pointed in v2 keeps its v1 behaviour, and nothing on screen says so. That
    is the worst kind of stale rule, because the tool looks like it was updated.

    Three effects, all reversible:
      * older applied proposals for the same client + module become "superseded";
      * learnings captured from those files that this file does NOT re-assert are
        TOMBSTONED (soft, so `/learned-mappings/{id}/restore` can undo it);
      * outputs generated for the affected conversions are marked stale rather
        than deleted — the handed-over file may still need to be inspected.
    """
    from datetime import datetime as _dt

    from app.models.conversion import Conversion
    from app.models.output import ConvertedOutput

    objects = {str(r.target_object).strip().lower()
               for r in proposal.rows if r.target_object}
    if not objects:
        return {"superseded": 0, "retired_learnings": 0, "outputs_marked_stale": 0}

    # Same client (or both global) and applied strictly EARLIER. Comparing on
    # applied_at rather than uploaded_at because governance follows what was put
    # into force, not what happened to be uploaded first.
    q = {"status": "applied", "_id": {"$ne": proposal.id},
         "client_id": proposal.client_id}
    olders = [p for p in await MappingProposal.find(q).to_list()
              if (p.applied_at or p.uploaded_at) < (proposal.applied_at or _dt.utcnow())
              and {str(r.target_object).strip().lower()
                   for r in p.rows if r.target_object} & objects]
    if not olders:
        return {"superseded": 0, "retired_learnings": 0, "outputs_marked_stale": 0}

    keep = await _asserted_pairs(proposal)
    retired = 0
    for old in olders:
        # Only learnings this file does not re-state. A pair present in BOTH was
        # already updated in place by apply_proposal, so retiring it here would
        # delete the new value.
        stale_pairs = {
            (str(r.target_object).strip().lower(), str(r.target_field).strip().lower())
            for r in old.rows
            if r.target_object and r.target_field
            and (str(r.target_object).strip().lower(),
                 str(r.target_field).strip().lower()) not in keep
        }
        if stale_pairs:
            marker = f"mapping document: {old.file_name}"
            candidates = await LearnedMapping.find(
                LearnedMapping.client_id == proposal.client_id,
                LearnedMapping.kind == "column_mapping",
            ).to_list()
            for lm in candidates:
                if (lm.captured_from or "") != marker:
                    continue
                pair = (str(lm.target_object or "").strip().lower(),
                        str(lm.target_field or "").strip().lower())
                if pair in stale_pairs:
                    await lm.set({"is_deleted": True, "deleted_at": _dt.utcnow(),
                                  "deleted_by": applied_by
                                  or f"superseded by {proposal.file_name}"})
                    retired += 1

        old.status = "superseded"
        old.superseded_by = proposal.id
        old.superseded_by_file = proposal.file_name
        old.superseded_at = _dt.utcnow()
        await old.save()

    # Every generated file for an affected conversion now predates the governing
    # document. Flagged, never deleted.
    stale = 0
    reason = f"Superseded by mapping document {proposal.file_name}"
    for conv in await Conversion.find_all().to_list():
        if str(conv.target_object or "").strip().lower() not in objects:
            continue
        try:
            from app.services.client_service import client_id_for_conversion
            if proposal.client_id is not None:
                if await client_id_for_conversion(conv) != proposal.client_id:
                    continue
        except Exception:                                       # noqa: BLE001
            continue
        outs = await ConvertedOutput.find(
            ConvertedOutput.conversion_id == conv.id).to_list()
        for o in outs:
            if o.status != "stale":
                await o.set({"status": "stale", "stale_reason": reason,
                             "stale_since": _dt.utcnow()})
                stale += 1

    proposal.supersedes = [p.id for p in olders]
    proposal.retired_learnings = retired
    proposal.outputs_marked_stale = stale
    await proposal.save()
    return {"superseded": len(olders), "retired_learnings": retired,
            "outputs_marked_stale": stale}


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
