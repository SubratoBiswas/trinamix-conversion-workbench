"""Import a source→target mapping workbook into the Mapping Knowledge Base.

A gold file makes the tool INFER the mapping from data. A mapping workbook STATES
it: "source column X feeds FBDI field Y for object Z". That's the strongest signal
there is — a human already did the mapping — so we take it at face value and store
each row as a reusable ``column_mapping`` LearnedMapping, exactly like the seeded
metadata catalog. Every future conversion of that object then auto-applies it.

Header handling is deliberately forgiving, because consultants' mapping sheets
never agree on column names. We resolve, by fuzzy header match:

  * source field   — the legacy column name            (required)
  * target field   — the FBDI column / attribute        (required)
  * target object  — Supplier / Customer / Item / …     (optional; can be inferred
                     from the FBDI field via the loaded templates, or forced with
                     a default_object)
  * source system  — NetSuite / SyteLine / …            (optional, stored as a tag)
  * notes          — free text                          (optional)

A row missing source or target is skipped and reported, never guessed.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.models.fbdi import FBDIField, FBDITemplate
from app.models.learned import LearnedMapping
from app.parsers import parse_tabular

logger = logging.getLogger(__name__)

_SOURCE_ALIASES = (
    "source field", "source column", "source attribute", "legacy field",
    "legacy column", "source", "from field", "from column", "source name",
    "input field", "external field",
)
_TARGET_ALIASES = (
    "target field", "target column", "fbdi field", "fbdi column", "oracle field",
    "oracle column", "fusion field", "target attribute", "target", "to field",
    "to column", "destination field", "interface column", "attribute",
)
_OBJECT_ALIASES = (
    "target object", "object", "business object", "entity", "fbdi object",
    "interface", "load object", "import object",
)
_SYSTEM_ALIASES = (
    "source system", "source erp", "system", "erp", "source app", "application",
)
_NOTE_ALIASES = ("notes", "note", "comment", "comments", "remarks", "description")
_SHEET_ALIASES = ("fbdi sheet", "sheet", "interface table", "target sheet", "worksheet")


def _norm_header(h: Any) -> str:
    return re.sub(r"[\s_]+", " ", str(h or "").strip().lower())


def _norm_key(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").strip().lower().strip("*"))


def _find_col(headers: list[str], aliases: tuple[str, ...]) -> Optional[int]:
    for i, h in enumerate(headers):        # exact first
        if h in aliases:
            return i
    for i, h in enumerate(headers):        # then contains
        if any(a in h for a in aliases):
            return i
    return None


# Any header that reads like it names a column of field names — used only as a
# positional fallback when the source or target alias didn't match (e.g.
# "NetSuite Field" / "Fusion Field", where the system name swallows the alias).
_FIELDISH = ("field", "column", "attribute", "name", "element")


def _fieldish_indices(headers: list[str]) -> list[int]:
    return [i for i, h in enumerate(headers) if any(w in h for w in _FIELDISH)]


def _resolve_src_tgt(headers: list[str]) -> tuple[Optional[int], Optional[int]]:
    """Locate the source and target columns, falling back to left/right position.

    Convention in every mapping workbook: source on the left, target on the right.
    So when an alias match is missing, take the appropriate field-ish column by
    position rather than giving up on an otherwise-usable sheet."""
    i_src = _find_col(headers, _SOURCE_ALIASES)
    i_tgt = _find_col(headers, _TARGET_ALIASES)
    if i_src is not None and i_tgt is not None:
        return i_src, i_tgt

    fieldish = _fieldish_indices(headers)
    if i_tgt is None and i_src is not None:
        # target = first field-ish column to the RIGHT of source.
        right = [i for i in fieldish if i > i_src]
        i_tgt = right[0] if right else None
    elif i_src is None and i_tgt is not None:
        # source = nearest field-ish column to the LEFT of target.
        left = [i for i in fieldish if i < i_tgt]
        i_src = left[-1] if left else None
    elif i_src is None and i_tgt is None and len(fieldish) >= 2:
        # neither matched but two field-ish columns exist → leftmost/rightmost.
        i_src, i_tgt = fieldish[0], fieldish[-1]
    return i_src, i_tgt


async def _object_by_field_index() -> dict[str, str]:
    """normalized FBDI field name → its object, from the loaded templates.

    Lets a mapping sheet that only names the target field (no object column) still
    be keyed correctly. Ambiguous fields (same name in two objects) are dropped, so
    we never key a row to the wrong object on a coincidence."""
    idx: dict[str, Optional[str]] = {}
    templates = {t.id: (t.business_object or t.name) for t in await FBDITemplate.find_all().to_list()}
    for f in await FBDIField.find_all().to_list():
        obj = templates.get(f.template_id)
        if not obj or not f.field_name:
            continue
        k = _norm_key(f.field_name)
        if k in idx and idx[k] != obj:
            idx[k] = None          # ambiguous → refuse to guess
        else:
            idx.setdefault(k, obj)
    return {k: v for k, v in idx.items() if v}


async def import_mapping_file(
    file_path: str, *, file_type: Optional[str] = None,
    default_object: Optional[str] = None, source_system: Optional[str] = None,
    user_email: str = "",
) -> dict:
    df = parse_tabular(file_path, file_type=file_type)
    if df is None or df.empty:
        return {"error": "That file has no rows."}

    headers = [_norm_header(c) for c in df.columns]
    i_src, i_tgt = _resolve_src_tgt(headers)
    if i_src is None or i_tgt is None or i_src == i_tgt:
        return {"error": (
            "Couldn't find the mapping columns. The file needs a source-field column "
            "and a target/FBDI-field column. Found: "
            + ", ".join(str(c) for c in df.columns[:12])
        )}
    i_obj = _find_col(headers, _OBJECT_ALIASES)
    i_sys = _find_col(headers, _SYSTEM_ALIASES)
    i_note = _find_col(headers, _NOTE_ALIASES)
    i_sheet = _find_col(headers, _SHEET_ALIASES)

    cols = list(df.columns)

    def cell(row: Any, idx: Optional[int]) -> str:
        if idx is None or idx >= len(cols):
            return ""
        v = row[cols[idx]]
        if v is None:
            return ""
        s = str(v).strip()
        return "" if s.lower() in ("nan", "none", "null") else s

    field_to_object = await _object_by_field_index() if i_obj is None and not default_object else {}

    imported = updated = skipped = 0
    unresolved_object: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    for _, row in df.iterrows():
        src = cell(row, i_src)
        tgt = cell(row, i_tgt)
        if not src or not tgt:
            skipped += 1
            continue

        obj = cell(row, i_obj) or default_object or field_to_object.get(_norm_key(tgt), "")
        if not obj:
            unresolved_object.append(f"{src} → {tgt}")
            skipped += 1
            continue

        key = (obj.lower(), _norm_key(tgt), _norm_key(src))
        if key in seen:
            continue
        seen.add(key)

        sys = cell(row, i_sys) or source_system or None
        note = cell(row, i_note) or None
        sheet = cell(row, i_sheet) or None

        # include_deleted=True: a retired row is invisible to a plain find_one, so
        # re-importing a workbook re-created every learning the analyst had deleted
        # since the last import. CW #5.
        existing = await LearnedMapping.find_one(
            LearnedMapping.kind == "column_mapping",
            LearnedMapping.target_object == obj,
            LearnedMapping.target_field == tgt,
            LearnedMapping.original_value == src,
            include_deleted=True,
        )
        _revive = bool(existing is not None and getattr(existing, "is_deleted", False))
        cfg = {"source_column": src, "fbdi_sheet": sheet, "note": note, "confidence": 0.95}
        if existing:
            # Refresh metadata but keep it as one rule — re-importing a corrected
            # sheet should update, not duplicate.
            # Uploading the workbook is an explicit user action, so a retired row
            # is revived IN PLACE rather than duplicated beside its tombstone.
            await existing.set({
                "resolved_value": tgt, "rule_config": cfg,
                "source_erp": sys, "captured_from": "mapping workbook",
                **({"is_deleted": False, "deleted_at": None, "deleted_by": None}
                   if _revive else {}),
            })
            updated += 1
            continue

        await LearnedMapping(
            kind="column_mapping",
            category="Column Mapping Alias",
            original_value=src,
            resolved_value=tgt,
            target_object=obj,
            target_field=tgt,
            rule_type=None,
            rule_config=cfg,
            source_erp=sys,
            captured_from="mapping workbook",
            captured_by=user_email,
        ).insert()
        imported += 1

    by_object: dict[str, int] = {}
    for o, _, _ in seen:
        by_object[o] = by_object.get(o, 0) + 1

    logger.info("mapping import: %d new, %d updated, %d skipped", imported, updated, skipped)
    return {
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "objects": sorted({o for o, _, _ in seen}),
        "unresolved_object": unresolved_object[:25],
        "columns_detected": {
            "source": cols[i_src], "target": cols[i_tgt],
            "object": cols[i_obj] if i_obj is not None else None,
            "source_system": cols[i_sys] if i_sys is not None else None,
        },
    }
