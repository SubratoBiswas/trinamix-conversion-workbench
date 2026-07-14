"""Import the customer's own Fusion lookup codes and make them authoritative.

The FBDI templates name ~45 lookup types (EGP_MATERIAL_PLANNING, EGP_SOURCE_TYPES,
…) without publishing their codes, because those codes are configurable per
instance. Until they're imported the tool refuses to guess — it passes those
columns through untouched and flags them.

This closes that gap. Point it at a Manage Standard Lookups export (Setup and
Maintenance → Manage Standard Lookups → export to Excel/CSV) and the codes become
real: they're stored as ``OracleLookup`` rows AND written onto every FBDIField
whose ``lookup_type`` matches, with ``verified=True``. Everything downstream —
the crosswalk recommender, the generate-time enforcement, the coded-value audit —
already reads ``allowed_values``, so the column flips from "unverified" to fully
mapped and validated with no other change.

Header handling is deliberately forgiving. A real Fusion export has TWO columns
called "Meaning" (one describing the lookup type, one describing the code), so we
take the one that sits after the lookup-code column; a hand-rolled three-column
sheet works too.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.models.fbdi import FBDIField, OracleLookup
from app.parsers import parse_tabular

logger = logging.getLogger(__name__)

_TYPE_ALIASES = ("lookup type", "lookuptype", "lookup_type", "type")
_CODE_ALIASES = ("lookup code", "lookupcode", "lookup_code", "code")
_MEANING_ALIASES = ("meaning", "lookup code meaning", "display value", "value", "label", "name")
_DESC_ALIASES = ("description", "lookup code description")
_ENABLED_ALIASES = ("enabled", "enabled flag", "active", "enabled_flag")

_TRUTHY = {"y", "yes", "true", "t", "1", "enabled", "active", "x"}


def _norm_header(h: Any) -> str:
    return re.sub(r"[\s_]+", " ", str(h or "").strip().lower())


def _find_col(headers: list[str], aliases: tuple[str, ...], after: int = -1) -> Optional[int]:
    """First column (at an index greater than ``after``) whose header matches."""
    for i, h in enumerate(headers):
        if i <= after:
            continue
        if h in aliases:
            return i
    # Fall back to a contains-match, so "Lookup Type Code" still resolves.
    for i, h in enumerate(headers):
        if i <= after:
            continue
        if any(a in h for a in aliases):
            return i
    return None


async def import_lookup_codes(
    file_path: str, *, file_type: Optional[str] = None, user_email: str = "",
) -> dict:
    df = parse_tabular(file_path, file_type=file_type)
    if df is None or df.empty:
        return {"error": "That file has no rows."}

    headers = [_norm_header(c) for c in df.columns]
    i_type = _find_col(headers, _TYPE_ALIASES)
    i_code = _find_col(headers, _CODE_ALIASES)
    if i_type is None or i_code is None:
        return {
            "error": (
                "Couldn't find the lookup columns. The file needs a lookup type column "
                "and a lookup code column — a Manage Standard Lookups export has both. "
                f"Found: {', '.join(str(c) for c in df.columns[:12])}"
            )
        }
    # The code-level meaning is the one AFTER the lookup code column; a Fusion
    # export repeats the header for the type-level meaning earlier in the row.
    i_meaning = _find_col(headers, _MEANING_ALIASES, after=i_code)
    if i_meaning is None:
        i_meaning = _find_col(headers, _MEANING_ALIASES)
    i_desc = _find_col(headers, _DESC_ALIASES, after=i_code)
    i_enabled = _find_col(headers, _ENABLED_ALIASES)

    cols = list(df.columns)

    def _cell(row: Any, idx: Optional[int]) -> str:
        if idx is None or idx >= len(cols):
            return ""
        v = row[cols[idx]]
        if v is None:
            return ""
        s = str(v).strip()
        return "" if s.lower() in ("nan", "none", "null") else s

    seen: set[tuple[str, str]] = set()
    parsed: list[dict] = []
    for _, row in df.iterrows():
        lt = _cell(row, i_type).upper()
        code = _cell(row, i_code)
        if not lt or not code:
            continue
        key = (lt, code)
        if key in seen:
            continue
        seen.add(key)
        enabled_raw = _cell(row, i_enabled)
        parsed.append({
            "lookup_type": lt,
            "code": code,
            "meaning": _cell(row, i_meaning) or None,
            "description": _cell(row, i_desc) or None,
            # A blank Enabled column means "not specified" — treat as enabled
            # rather than silently dropping every code in the file.
            "enabled": True if not enabled_raw else enabled_raw.lower() in _TRUTHY,
        })

    if not parsed:
        return {"error": "No lookup type / lookup code pairs found in that file."}

    # Replace each imported lookup type wholesale: a re-import should reflect the
    # instance as it is now, including codes that were disabled or removed.
    types = sorted({p["lookup_type"] for p in parsed})
    await OracleLookup.find({"lookup_type": {"$in": types}}).delete()
    await OracleLookup.insert_many([
        OracleLookup(**p, source="instance_import", imported_by=user_email) for p in parsed
    ])

    applied = await apply_lookups_to_fields(types)

    referenced = await _referenced_lookup_types()
    unused = [t for t in types if t not in referenced]
    still_missing = sorted(t for t in referenced if t not in set(types))

    logger.info(
        "lookup import: %d codes across %d types; %d template fields updated",
        len(parsed), len(types), applied["fields_updated"],
    )
    return {
        "codes_imported": len(parsed),
        "lookup_types": types,
        "fields_updated": applied["fields_updated"],
        "types_matched": applied["types_matched"],
        "types_not_used_by_any_template": unused,
        "types_still_missing": still_missing,
    }


async def apply_lookups_to_fields(lookup_types: list[str]) -> dict:
    """Write imported codes onto every FBDIField that names one of these lookups."""
    fields_updated = 0
    matched: list[str] = []

    for lt in lookup_types:
        rows = await OracleLookup.find(
            OracleLookup.lookup_type == lt, OracleLookup.enabled == True  # noqa: E712
        ).to_list()
        if not rows:
            continue
        allowed = [
            {
                "code": r.code,
                "meaning": r.meaning or "",
                "polarity": None,
                "source": "instance",
                "verified": True,
            }
            for r in rows
        ]
        fields = await FBDIField.find(FBDIField.lookup_type == lt).to_list()
        if fields:
            matched.append(lt)
        for f in fields:
            f.allowed_values = allowed
            f.validation_notes = (
                f"Accepted codes imported from your Fusion instance "
                f"(lookup type {lt}, {len(allowed)} active codes)."
            )
            await f.save()
            fields_updated += 1

    return {"fields_updated": fields_updated, "types_matched": matched}


async def _referenced_lookup_types() -> set[str]:
    """Every lookup type the loaded templates actually depend on."""
    out: set[str] = set()
    for f in await FBDIField.find(FBDIField.lookup_type != None).to_list():  # noqa: E711
        if f.lookup_type:
            out.add(f.lookup_type.strip().upper())
    return out


async def lookup_status() -> dict:
    """Coverage report: which lookup types the templates need, and which we have."""
    referenced = await _referenced_lookup_types()

    have: dict[str, int] = {}
    for r in await OracleLookup.find(OracleLookup.enabled == True).to_list():  # noqa: E712
        have[r.lookup_type] = have.get(r.lookup_type, 0) + 1

    rows = []
    for lt in sorted(referenced):
        n_fields = await FBDIField.find(FBDIField.lookup_type == lt).count()
        rows.append({
            "lookup_type": lt,
            "codes": have.get(lt, 0),
            "columns_using_it": n_fields,
            "status": "imported" if have.get(lt) else "missing",
        })

    imported = sum(1 for r in rows if r["status"] == "imported")
    return {
        "lookup_types": rows,
        "summary": {
            "referenced": len(rows),
            "imported": imported,
            "missing": len(rows) - imported,
            "total_codes": sum(have.values()),
        },
    }
