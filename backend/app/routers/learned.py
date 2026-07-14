"""Learning library endpoints - registry of human-approved mappings/rules."""
from collections import Counter
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.models.conversion import Conversion
from app.models.learned import LearnedMapping
from app.models.user import User
from app.schemas.learned import (
    LearnedMappingCreate, LearnedMappingOut, LearnedMappingUpdate, LearningStats,
)
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/learned-mappings", tags=["learning"])


DEFAULT_CATEGORIES = [
    "Column Mapping Alias",
    "SKU / Item Format Alias",
    "Customer Alias",
    "Supplier Alias",
    "UOM Conversion Rule",
    "Status Value Mapping",
    "Date Format Rule",
    "Currency Mapping",
    "Organization Code Mapping",
    "Branch Code Mapping",
]


def _serialize(item: LearnedMapping) -> dict:
    d = item.model_dump()
    d["id"] = str(item.id)
    d["project_id"] = str(item.project_id) if item.project_id else None
    d["originated_in_project_id"] = (
        str(item.originated_in_project_id) if item.originated_in_project_id else None
    )
    return d


@router.post("", response_model=LearnedMappingOut)
async def create_learned(
    payload: LearnedMappingCreate,
    user: User = Depends(get_current_user),
):
    item = LearnedMapping(**payload.model_dump(), captured_by=user.email)
    await item.insert()
    return _serialize(item)


@router.get("", response_model=list[LearnedMappingOut])
async def list_learned(
    kind: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    target_object: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Free-text match on field / value"),
    limit: int = Query(0, ge=0, le=5000),
    _: User = Depends(get_current_user),
):
    filters = []
    if kind:
        filters.append(LearnedMapping.kind == kind)
    if category:
        filters.append(LearnedMapping.category == category)
    if project_id:
        filters.append(LearnedMapping.project_id == PydanticObjectId(project_id))
    if target_object:
        filters.append(LearnedMapping.target_object == target_object)
    query = LearnedMapping.find(*filters)
    items = await query.sort("-captured_at").to_list()

    if q:
        needle = q.strip().lower()
        items = [
            i for i in items
            if needle in (i.target_field or "").lower()
            or needle in (i.original_value or "").lower()
            or needle in (i.resolved_value or "").lower()
        ]
    if limit:
        items = items[:limit]
    return [_serialize(item) for item in items]


# Friendly names for the internal `kind` values, so the UI never has to know them.
_KIND_LABELS = {
    "column_mapping": "Column mappings",
    "example_default": "Default values",
    "suppress_field": "Left blank on purpose",
    "crosswalk": "Value crosswalks",
    "reference_standard": "Reference standards",
    "file_classification": "File classification",
}


# A curated set of common Oracle Fusion Cloud FBDI import objects, grouped by
# module. Used to populate the object picker so a consultant can key a mapping to
# an object even before its template is loaded. It's suggestion-only — the field
# stays free text — so breadth here is pure upside.
_CANONICAL_OBJECTS: dict[str, list[str]] = {
    "Procurement": [
        "Supplier", "Supplier Address", "Supplier Site", "Supplier Site Assignment",
        "Supplier Contacts", "Supplier Bank Account", "Purchase Order",
        "Purchase Agreement", "Requisition", "Receipt",
    ],
    "Inventory / SCM": [
        "Item", "Item Category", "Item Category Assignment", "Item Cross Reference",
        "Item Structure", "Item Organization Assignment", "Item Cost",
        "Unit of Measure", "Inventory Organization", "Subinventory", "Locator",
        "On-hand Balance", "Cycle Count",
    ],
    "Order Management": [
        "Sales Order", "Price List", "Pricing Charge",
    ],
    "Receivables": [
        "Customer", "Customer Account", "Customer Site", "Customer Contact",
        "Customer Account Site", "AR Invoice", "AR Receipt", "AutoInvoice",
    ],
    "Payables": [
        "Payables Invoice", "Payment Term", "Payment", "Tax Rate",
    ],
    "General Ledger": [
        "Journal", "GL Balance", "Chart of Accounts Value", "Budget",
        "Account Combination",
    ],
    "Assets": [
        "Fixed Asset", "Asset Category",
    ],
    "Cash Management": [
        "Bank", "Bank Branch", "Bank Account",
    ],
    "Projects": [
        "Project", "Project Task", "Project Budget", "Project Expenditure",
    ],
    "HCM": [
        "Worker", "Assignment", "Department", "Job", "Position", "Location", "Grade",
    ],
}


@router.get("/known-objects")
async def known_objects(_: User = Depends(get_current_user)):
    """Objects a mapping row can be keyed to.

    The union of: the curated canonical Oracle FBDI object list, the business
    objects of every loaded template (authoritative — these are what the tool can
    actually target), and every object that already carries a learned rule. Sorted,
    de-duped case-insensitively, canonical/template casing wins over ad-hoc.
    """
    from app.models.fbdi import FBDITemplate

    # Preserve the first (canonical) casing seen for each normalized key.
    best: dict[str, str] = {}

    def add(name: str | None):
        if not name:
            return
        n = name.strip()
        if not n:
            return
        best.setdefault(n.lower(), n)

    for group in _CANONICAL_OBJECTS.values():
        for o in group:
            add(o)
    for t in await FBDITemplate.find_all().to_list():
        add(t.business_object)
    for o in await LearnedMapping.distinct("target_object"):
        add(o)

    grouped = {
        module: [o for o in objs if o.lower() in best]
        for module, objs in _CANONICAL_OBJECTS.items()
    }
    return {
        "objects": sorted(best.values(), key=str.lower),
        "grouped": grouped,
    }


@router.post("/import-mappings")
async def import_mappings(
    files: list[UploadFile] = File(...),
    default_object: str | None = Form(None),
    source_system: str | None = Form(None),
    user: User = Depends(get_current_user),
):
    """Import one or more source→target mapping workbooks.

    A mapping workbook explicitly states the crosswalk a consultant already worked
    out, so each row is stored as a reusable column_mapping and auto-applied on
    every future conversion of that object. ``default_object`` / ``source_system``
    are fallbacks used only where a row (or the sheet) doesn't carry its own.
    """
    import tempfile
    from pathlib import Path as _P

    from app.services.mapping_import_service import import_mapping_file

    allowed = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls")
    files = [f for f in files if f and f.filename]
    if not files:
        raise HTTPException(400, "No files uploaded.")

    results = []
    tot_new = tot_upd = tot_skip = 0
    for f in files:
        suffix = _P(f.filename).suffix.lower()
        if suffix not in allowed:
            results.append({"file_name": f.filename, "error": "Not a CSV or Excel file."})
            continue
        contents = await f.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name
        try:
            r = await import_mapping_file(
                tmp_path, file_type=suffix.lstrip("."),
                default_object=default_object or None,
                source_system=source_system or None,
                user_email=user.email,
            )
        finally:
            _P(tmp_path).unlink(missing_ok=True)
        r["file_name"] = f.filename
        if not r.get("error"):
            tot_new += r.get("imported", 0)
            tot_upd += r.get("updated", 0)
            tot_skip += r.get("skipped", 0)
        results.append(r)

    return {
        "files": results,
        "imported": tot_new,
        "updated": tot_upd,
        "skipped": tot_skip,
    }


@router.get("/by-object")
async def learned_by_object(_: User = Depends(get_current_user)):
    """What the tool has learned, grouped by the Oracle object it applies to.

    The flat registry is unusable once it passes a few hundred rows — and it gets
    there fast, because a single gold file can contribute hundreds of "leave this
    blank" rules. Nobody wants to scroll 978 rows to answer "what do we know about
    Supplier?". This is that answer.
    """
    items = await LearnedMapping.find_all().to_list()

    groups: dict[str, dict] = {}
    for i in items:
        obj = (i.target_object or "").strip() or "Not tied to an object"
        g = groups.setdefault(obj, {
            "target_object": obj,
            "total": 0,
            "by_kind": {},
            "sources": set(),
            "last_captured": None,
        })
        g["total"] += 1
        k = i.kind or "other"
        g["by_kind"][k] = g["by_kind"].get(k, 0) + 1
        if i.captured_from:
            g["sources"].add(i.captured_from)
        if i.captured_at and (g["last_captured"] is None or i.captured_at > g["last_captured"]):
            g["last_captured"] = i.captured_at

    out = []
    for g in groups.values():
        out.append({
            "target_object": g["target_object"],
            "total": g["total"],
            "kinds": [
                {"kind": k, "label": _KIND_LABELS.get(k, k.replace("_", " ").title()), "count": c}
                for k, c in sorted(g["by_kind"].items(), key=lambda kv: -kv[1])
            ],
            "sources": sorted(g["sources"]),
            "last_captured": g["last_captured"],
        })
    # Real objects first, biggest first; the catch-all bucket always last.
    out.sort(key=lambda r: (r["target_object"] == "Not tied to an object", -r["total"]))
    return {"objects": out, "total": len(items)}


@router.get("/stats", response_model=LearningStats)
async def learning_stats(
    project_id: Optional[str] = Query(None),
    _: User = Depends(get_current_user),
):
    if project_id:
        items = await LearnedMapping.find(
            LearnedMapping.project_id == PydanticObjectId(project_id)
        ).to_list()
    else:
        items = await LearnedMapping.find_all().to_list()
    total = len(items)
    avg_boost = round(
        sum(i.confidence_boost or 0 for i in items) / total, 3
    ) if total else 0.0
    records_fixed = sum(int(i.records_auto_fixed or 0) for i in items)
    minutes_saved = total * 4

    by_cat = Counter(i.category for i in items)
    cat_rows = []
    for c in DEFAULT_CATEGORIES:
        cat_rows.append({"category": c, "count": by_cat.get(c, 0)})
    for c in by_cat:
        if c not in DEFAULT_CATEGORIES:
            cat_rows.append({"category": c, "count": by_cat[c]})

    return {
        "total": total,
        "avg_confidence_boost": avg_boost,
        "records_auto_fixed": records_fixed,
        "analyst_minutes_saved": minutes_saved,
        "by_category": cat_rows,
    }


@router.get("/reference-standards")
async def reference_standards(_: User = Depends(get_current_user)):
    """Per-object summary of the gold-derived reference standards stored in the
    DB. Once gold has been applied for an object type (e.g. Supplier Import),
    a reusable standard is on file and auto-applied to every future conversion
    of that object — so the UI can show it without the user re-uploading gold.
    Grouped by target_object across the reusable (global) learned rules."""
    items = await LearnedMapping.find(
        {"kind": {"$in": ["column_mapping", "example_default", "suppress_field"]},
         "target_object": {"$ne": None}}
    ).to_list()
    by_obj: dict[str, dict] = {}
    for it in items:
        obj = it.target_object
        row = by_obj.setdefault(obj, {
            "business_object": obj, "column_mappings": 0, "defaults": 0,
            "suppressions": 0, "captured_from": it.captured_from,
            "captured_at": it.captured_at,
        })
        if it.kind == "column_mapping":
            row["column_mappings"] += 1
        elif it.kind == "example_default":
            row["defaults"] += 1
        elif it.kind == "suppress_field":
            row["suppressions"] += 1
        if it.captured_at and (row["captured_at"] is None or it.captured_at > row["captured_at"]):
            row["captured_at"] = it.captured_at
            row["captured_from"] = it.captured_from
    rows = sorted(by_obj.values(), key=lambda r: r["business_object"])
    return {"reference_standards": rows}


@router.get("/knowledge-bank/stats")
async def knowledge_bank_stats(_: User = Depends(get_current_user)):
    items = await LearnedMapping.find_all().to_list()
    by_erp: Counter = Counter()
    for item in items:
        erp = getattr(item, "source_erp", None) or getattr(item, "captured_from", None) or "unknown"
        by_erp[erp] += 1
    return [{"source_erp": erp, "count": cnt} for erp, cnt in by_erp.most_common()]


@router.get("/catalog-status")
async def catalog_status(_: User = Depends(get_current_user)):
    """Counts of the seeded metadata-catalog mappings (source→FBDI column rules),
    grouped by source system and target object — lets the UI confirm the Mapping
    Knowledge Base is populated."""
    items = await LearnedMapping.find(
        LearnedMapping.captured_from == "metadata catalog"
    ).to_list()
    by_src: Counter = Counter()
    by_obj: Counter = Counter()
    rows = []
    for it in items:
        by_src[it.source_erp or "unknown"] += 1
        by_obj[it.target_object or "unknown"] += 1
        rows.append({
            "source_system": it.source_erp, "target_object": it.target_object,
            "source_field": it.original_value, "fbdi_column": it.target_field,
            "fbdi_sheet": (it.rule_config or {}).get("fbdi_sheet"),
        })
    return {
        "total": len(items),
        "by_source_system": [{"source_system": k, "count": v} for k, v in by_src.most_common()],
        "by_target_object": [{"target_object": k, "count": v} for k, v in by_obj.most_common()],
        "rows": sorted(rows, key=lambda r: (r["source_system"] or "", r["target_object"] or "", r["fbdi_column"] or "")),
    }


@router.post("/reseed-catalog")
async def reseed_catalog(_: User = Depends(get_current_user)):
    """Re-run the metadata-catalog seed on demand (idempotent, additive)."""
    from app.services.catalog_seed_service import seed_mapping_catalog
    return await seed_mapping_catalog()


@router.post("/backfill-projects")
async def backfill_project_ids(_: User = Depends(get_current_user)):
    """
    One-time migration: stamp project_id on learned mappings that are missing it.
    Infers project from the conversion name embedded in captured_from.
    """
    items = await LearnedMapping.find(
        LearnedMapping.project_id == None  # noqa: E711
    ).to_list()

    # Build a lookup: conversion name -> project_id
    all_convs = await Conversion.find_all().to_list()
    name_to_project: dict[str, PydanticObjectId] = {
        c.name: c.project_id for c in all_convs if c.project_id
    }

    updated = 0
    skipped = 0
    for lm in items:
        if not lm.captured_from:
            skipped += 1
            continue
        # captured_from format: "ConversionName -- field_name" or "ConversionName -- field_name (manual)"
        conv_name = lm.captured_from.split(" -- ")[0].strip()
        pid = name_to_project.get(conv_name)
        if pid:
            await lm.set({"project_id": pid})
            updated += 1
        else:
            skipped += 1

    return {
        "total_without_project": len(items),
        "updated": updated,
        "skipped_no_match": skipped,
    }


@router.patch("/{learned_id}", response_model=LearnedMappingOut)
async def update_learned(
    learned_id: str,
    payload: LearnedMappingUpdate,
    _: User = Depends(get_current_user),
):
    item = await LearnedMapping.get(PydanticObjectId(learned_id))
    if not item:
        raise HTTPException(404, "Not found")
    updates = payload.model_dump(exclude_unset=True)
    if updates:
        await item.set(updates)
        item = await LearnedMapping.get(PydanticObjectId(learned_id))
    return _serialize(item)


@router.delete("/{learned_id}")
async def delete_learned(
    learned_id: str,
    _: User = Depends(get_current_user),
):
    item = await LearnedMapping.get(PydanticObjectId(learned_id))
    if not item:
        raise HTTPException(404, "Not found")
    await item.delete()
    return {"deleted": learned_id}
