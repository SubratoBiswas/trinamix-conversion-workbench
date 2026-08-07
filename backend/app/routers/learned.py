"""Learning library endpoints - registry of human-approved mappings/rules."""
from collections import Counter
from datetime import datetime
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
    data = payload.model_dump()
    # A learning added by hand is effective NOW — the precedence is "the last mapping
    # with respect to date is final", and a row with no date falls back to captured_at,
    # which every startup seed re-stamps.
    data.setdefault("effective_date", datetime.utcnow())
    item = LearnedMapping(**data, captured_by=user.email)
    await item.insert()
    out = _serialize(item)
    try:
        out["propagation"] = await _reapply_learning(item, user.email)
    except Exception as exc:  # noqa: BLE001 — never fail the save on the fan-out
        out["propagation"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    return out


async def _reapply_learning(item: LearnedMapping, actor: str) -> dict:
    """Push a learning edited or added HERE onto the conversions it applies to.

    PATCH and POST were a bare ``await item.set(...)`` / ``insert()``: editing a rule
    in the Learning Centre reached NO conversion at all, not even a stale mark. The
    library and the files silently drifted apart, and the only way to find out was to
    regenerate and read the output.

    Propagation needs an origin conversion to resolve client and source scope from.
    Any conversion of the same object will do — they are the population the learning
    applies to — so the most recently updated one is used. With none, there is
    nothing to propagate to and that is reported rather than hidden.
    """
    from app.services.learning_service import (
        object_keys_for_object, propagate_learning_to_open_conversions)
    keys = set(object_keys_for_object(item.target_object))
    if not item.target_field:
        return {"conversions": 0, "mappings": 0, "note": "learning has no field"}
    # A CLIENT RULE (target_object=None) is not about one object, so there is no
    # object to match conversions on — any conversion will do as the origin, and the
    # propagation itself is what decides who it reaches. Without this branch
    # object_keys_for_object(None) returned [] and editing a client rule here
    # propagated to NOTHING while reporting "learning has no object/field", which is
    # both wrong and unhelpful.
    _all = [c for c in await Conversion.find_all().to_list() if c.template_id]
    convs = _all if item.target_object is None else [
        c for c in _all if (c.target_object or "") in keys]
    if not convs:
        return {"conversions": 0, "mappings": 0,
                "note": (f"no conversion currently targets {item.target_object!r}"
                         if item.target_object else "no conversion has a template yet")}
    convs.sort(key=lambda c: getattr(c, "updated_at", None) or datetime.min, reverse=True)
    # skip_origin=False: the edit came from the LIBRARY, not from one conversion's
    # screen, so there is no conversion that is "already correct" — the newest one is
    # borrowed purely to resolve client and source scope, and it must be updated too.
    return await propagate_learning_to_open_conversions(
        item, convs[0], captured_by=actor, skip_origin=False)


@router.get("", response_model=list[LearnedMappingOut])
async def list_learned(
    kind: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    target_object: Optional[str] = Query(None),
    client_id: Optional[str] = Query(None, description="Client scope: a client id, or 'global'"),
    q: Optional[str] = Query(None, description="Free-text match on field / value"),
    limit: int = Query(0, ge=0, le=5000),
    _: User = Depends(get_current_user),
):
    filters: list = []
    if kind:
        filters.append(LearnedMapping.kind == kind)
    if category:
        filters.append(LearnedMapping.category == category)
    if project_id:
        filters.append(LearnedMapping.project_id == PydanticObjectId(project_id))
    if target_object:
        # Every spelling this object answers to. Written under the template's
        # business_object and asked for here under the conversion's target_object,
        # compared with exact equality, a learning is filed where nobody looks — and
        # the generator uses the write key, so the value reaches the file while the
        # screen shows nothing.
        from app.services.learning_service import object_keys_with_client_rules
        # object_keys_WITH_CLIENT_RULES: a rule stored against the client rather than
        # one object (target_object=None) must still be listed when the Learning
        # Centre is filtered to an object, or the analyst's own saved rules vanish
        # from the screen that exists to show them.
        filters.append(
            {"target_object": {"$in": object_keys_with_client_rules(target_object)}})
    # Client scope: 'global' → only global rows; a client id → that client + global.
    if client_id == "global":
        filters.append({"is_global": True})
    elif client_id:
        try:
            filters.append({"$or": [
                {"is_global": True},
                {"client_id": PydanticObjectId(client_id)},
                # UNTAGGED rows. client_id_for_conversion falls back to the default
                # client and CAN return None, and the capture then writes
                # client_id=None with is_global=False — a row that matched NEITHER
                # branch and so was invisible the moment a client was selected. That
                # is "default values in the learning centre is not getting populated"
                # (CW #7): the learning existed, the list simply could not see it.
                #
                # scope_query already rescues exactly these rows for the defaults
                # layer; this endpoint hand-rolled its filter and did not. Two
                # readers of one collection disagreeing about what is in scope is
                # what made the row invisible on screen while it still applied to
                # the file.
                {"client_id": None},
            ]})
        except Exception:
            raise HTTPException(400, "Invalid client_id")
    query = LearnedMapping.find(*filters)
    items = await query.to_list()
    # Sorted by the SAME date the engine ranks on. captured_at is re-stamped by every
    # startup seed, so a seeded row with an ancient effective_date sorted to the top
    # of this list while ranking LAST in the engine — the list and the file disagreeing
    # about which instruction is current, which is the whole complaint.
    from app.services.learning_service import _effective_of
    items.sort(key=_effective_of, reverse=True)

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
async def learned_by_object(
    client_id: Optional[str] = Query(None, description="Client scope: a client id, or 'global'"),
    _: User = Depends(get_current_user),
):
    """What the tool has learned, grouped by the Oracle object it applies to.

    The flat registry is unusable once it passes a few hundred rows — and it gets
    there fast, because a single gold file can contribute hundreds of "leave this
    blank" rules. Nobody wants to scroll 978 rows to answer "what do we know about
    Supplier?". This is that answer. Optionally scoped to a client (+ global).
    """
    items = await LearnedMapping.find_all().to_list()
    if client_id == "global":
        items = [i for i in items if getattr(i, "is_global", False)]
    elif client_id:
        items = [i for i in items
                 if getattr(i, "is_global", False) or str(getattr(i, "client_id", "")) == client_id]

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

    # Honest, computed-from-data metrics only. (The old strip showed an "avg
    # confidence boost" that was just the hardcoded 0.26 default on every rule, and
    # an "analyst time saved" that was literally total × 4 minutes — both invented.)
    objects_covered = len({(i.target_object or "").strip() for i in items if i.target_object})

    # Rules that resolve with NO AI call: column mappings, constant defaults,
    # deliberate blanks, and value crosswalks. This is the real, defensible payoff —
    # every one of these is work the tool does deterministically instead of paying
    # for a model round trip.
    _NO_AI_KINDS = {"column_mapping", "example_default", "suppress_field", "crosswalk"}
    reusable_no_ai = sum(1 for i in items if (i.kind or "") in _NO_AI_KINDS)

    # A real counter: incremented each time a learned rule was auto-applied to a
    # conversion field (see apply_learned_to_conversion). Not "records fixed" — that
    # label was wrong — but genuine reuse.
    times_applied = sum(int(i.records_auto_fixed or 0) for i in items)

    by_cat = Counter(i.category for i in items)
    cat_rows = []
    for c in DEFAULT_CATEGORIES:
        cat_rows.append({"category": c, "count": by_cat.get(c, 0)})
    for c in by_cat:
        if c not in DEFAULT_CATEGORIES:
            cat_rows.append({"category": c, "count": by_cat[c]})

    by_src = Counter((i.captured_from or "manual").strip() or "manual" for i in items)
    src_rows = [{"source": s, "count": n} for s, n in by_src.most_common()]

    return {
        "total": total,
        "objects_covered": objects_covered,
        "reusable_no_ai": reusable_no_ai,
        "times_applied": times_applied,
        "by_category": cat_rows,
        "by_source": src_rows,
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


@router.post("/reseed-bom")
async def reseed_bom(_: User = Depends(get_current_user)):
    """Re-run the BOM (Item Structure) seed on demand and return its counts.

    The BOM rows were undated and never overrode the old source-scoped/constant rows,
    so Structure Item Name shipped blank and Organization Code shipped the NXT_ITEM_ORG
    constant instead of the input value. The seed now purges its own rows and re-seeds
    them DATED. Exposed here so 'did it land?' is answerable without a redeploy —
    returns purged / seeded / constants_seeded.
    """
    from app.services.catalog_seed_service import seed_bom_field_mappings
    return await seed_bom_field_mappings()


@router.post("/reseed-supplier")
async def reseed_supplier(_: User = Depends(get_current_user)):
    """Force the supplier field + transform seeds on demand (idempotent, additive,
    GLOBAL). Lets us apply the analyst supplier rules without waiting for the
    startup task, and confirm they landed."""
    from app.services.catalog_seed_service import (
        seed_supplier_field_mappings, seed_supplier_transform_mappings,
    )
    fields = await seed_supplier_field_mappings()
    transforms = await seed_supplier_transform_mappings()
    return {"field_mappings": fields, "transforms": transforms}


@router.post("/reseed-employee-hdl")
async def reseed_employee_hdl(_: User = Depends(get_current_user)):
    """Reconcile the Employee HDL template with the schema, on demand.

    It only ran at startup and it SKIPPED any template that already had at least one
    sheet — so when hdl_schema grew from two objects to six, the deployed template
    stayed at two and the download shipped one workbook with the Worker tabs. That
    reads as a generation failure; the objects had never been created. Now it adds
    what is missing and reports which sheets it added, so the answer is visible
    instead of inferred from a file.
    """
    from app.services.hdl_seed_service import ensure_employee_hdl
    return await ensure_employee_hdl()


@router.post("/consolidate-employee-hdl")
async def consolidate_employee_hdl_endpoint(_: User = Depends(get_current_user)):
    """Point every Employee conversion at the one complete HDL template, and retire
    the duplicates.

    The generated workbook arrived as Worker_HCM.xlsx with two tabs — a TEMPLATE name,
    and that template has two sheets. So the conversion was never bound to the template
    the seeder repairs; two templates claimed the same object and nothing on screen said
    which one a conversion used. This rebinds and retires, and reports both by name,
    because silently re-pointing live conversions has to be auditable afterwards.

    Retires rather than deletes: outputs and mapping rows reference template_id, and
    removing the row would orphan every artifact ever generated from it.
    """
    from app.services.hdl_seed_service import consolidate_employee_hdl
    return await consolidate_employee_hdl()


@router.post("/reseed-supplier-source-mapping")
async def reseed_supplier_source_mapping(_: User = Depends(get_current_user)):
    """Re-run the supplier mapping-workbook seed on demand.

    It only ever ran at startup, which is the shape that makes "did the new workbook
    actually land?" unanswerable without a redeploy — and this edition rebinds seven
    fields, so the answer matters. The payload names the edition it read, how many
    rows it rewrote from the previous edition, and every row it declined to import
    with the reason, so a caller can see what was NOT done as well as what was.
    """
    from app.services.catalog_seed_service import seed_supplier_source_mapping
    return await seed_supplier_source_mapping()


@router.post("/reseed-supplier-corrections")
async def reseed_supplier_corrections(_: User = Depends(get_current_user)):
    """Re-run the 30-Jul analyst corrections on demand.

    These only ran at startup, which made every "did the correction actually land?"
    question cost a redeploy and a cold boot to answer — and when it turned out the
    blank enforcement was matching nothing on the live instance, there was no way
    to see that without one. Idempotent, tombstone-respecting, and it returns the
    per-field enforcement counts, so "it ran" and "it changed something" are
    distinguishable rather than both looking like silence.
    """
    from app.services.catalog_seed_service import seed_supplier_corrections_30jul
    return await seed_supplier_corrections_30jul()


@router.post("/reseed-hcm-mapping")
async def reseed_hcm_mapping(_: User = Depends(get_current_user)):
    """Re-run the HCM (Employee) green-row mapping seed on demand.

    Returns the counts so "it ran" and "it changed something" stay distinguishable.
    The rows land in the Learning Center like any other learning — same collection,
    same list — scoped to source system Workday and object Employee HDL.
    """
    from app.services.catalog_seed_service import seed_hcm_source_mapping
    return await seed_hcm_source_mapping()


@router.post("/reseed-customer-sheet-scope")
async def reseed_customer_sheet_scope(_: User = Depends(get_current_user)):
    """Re-run the Customer per-sheet scope on demand (CW_Issues 2 rows 13-15, 26)."""
    from app.services.catalog_seed_service import seed_customer_sheet_scope
    return await seed_customer_sheet_scope()


@router.post("/reseed-customer-mapping")
async def reseed_customer_mapping(_: User = Depends(get_current_user)):
    """Re-run the 03-Aug/06-Aug Customer mapping seed on demand and RETURN its result.

    The seed also runs at startup, but its outcome was invisible — a failure was
    swallowed by main.py's try/except, so "did the constants land?" cost a redeploy
    and a guess. Exposed here it returns the per-kind counts AND the `errors` list
    (the fields whose write raised), which is how the "derive/rule landed but every
    constant/blank was missing" fault is now diagnosed rather than inferred.
    Idempotent — an older statement never overwrites a newer one."""
    from app.services.catalog_seed_service import seed_customer_mapping_03aug
    return await seed_customer_mapping_03aug()


@router.post("/reseed-customer-mapping-06aug")
async def reseed_customer_mapping_06aug(_: User = Depends(get_current_user)):
    """Re-run the 06-Aug Customer yellow-column changes seed on demand + return counts."""
    from app.services.catalog_seed_service import seed_customer_mapping_06aug
    return await seed_customer_mapping_06aug()


@router.post("/reseed-customer-mapping-07aug")
async def reseed_customer_mapping_07aug(_: User = Depends(get_current_user)):
    """Re-run the 07-Aug Customer additions seed (Account Description, Identifying
    Address) on demand + return counts."""
    from app.services.catalog_seed_service import seed_customer_mapping_07aug
    return await seed_customer_mapping_07aug()


@router.post("/reseed-supplier-transforms")
async def reseed_supplier_transforms(_: User = Depends(get_current_user)):
    """Re-run the 06-Aug Supplier transforms seed (#1 Parent Supplier Name, #3 Supplier
    Site) on demand and return its counts + errors. Idempotent."""
    from app.services.catalog_seed_service import seed_supplier_transforms_06aug
    return await seed_supplier_transforms_06aug()


@router.post("/backfill-field-key")
async def backfill_field_key_endpoint(_: User = Depends(get_current_user)):
    """Stamp field_key on legacy rows so the resolver's index can be used.

    Also runs at startup; exposed so the one-time backfill can be forced (and its
    count read) without a redeploy. Idempotent — only rows missing the key are touched.
    Run this BEFORE reseed-customer-mapping on an already-running instance so the seed
    resolves through the index instead of scanning."""
    from app.services.mapping_store import backfill_field_key
    return await backfill_field_key()


@router.post("/backfill-dated-store")
async def backfill_dated_store(_: User = Depends(get_current_user)):
    """Carry every decision made before the store existed into it.

    Runs on every boot too. Exposed on demand so "did it land?" does not cost a
    redeploy. Idempotent — running it twice changes nothing the second time, and
    it never moves a date that is already set.
    """
    from app.services.mapping_store_backfill import backfill
    return await backfill()


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
    # find_one + include_deleted, not get(): get() delegates to find_one and this
    # model filters tombstones out of find, so a retired row is invisible to it.
    # Harmless here today, but the same blind spot two lines down in delete_learned
    # meant a retired learning could never be purged — so the model is simply never
    # read through get().
    _q = LearnedMapping.id == PydanticObjectId(learned_id)
    item = await LearnedMapping.find_one(_q, include_deleted=True)
    if not item:
        raise HTTPException(404, "Not found")
    updates = payload.model_dump(exclude_unset=True)
    if updates:
        # Editing it is stating it again, today.
        updates.setdefault("effective_date", datetime.utcnow())
        await item.set(updates)
        item = await LearnedMapping.find_one(_q, include_deleted=True)
    out = _serialize(item)
    if updates:
        try:
            out["propagation"] = await _reapply_learning(item, "learning-centre")
        except Exception as exc:  # noqa: BLE001
            out["propagation"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    return out


@router.delete("/{learned_id}")
async def delete_learned(
    learned_id: str,
    purge: bool = False,
    user: User = Depends(get_current_user),
):
    """Retire a learning (QA issue #5).

    A hard delete did not stick: the startup seeds, the auto-capture that runs
    after every Generate Output, and approve/override in Mapping Review all
    re-created the row, so deleted items reappeared. We now tombstone instead —
    the row stops applying and stops being listed, and nothing automatic can
    resurrect it. ``?purge=true`` still removes the document outright.
    """
    # Beanie's Document.get() delegates to find_one, and LearnedMapping.find injects
    # {'is_deleted': {'$ne': True}} — so get() is TOMBSTONE-BLIND. An already-retired
    # row therefore 404'd here, which meant ?purge=true could never remove one: the
    # only rows you could purge were the ones you had not deleted yet.
    item = await LearnedMapping.find_one(
        LearnedMapping.id == PydanticObjectId(learned_id), include_deleted=True)
    if not item:
        raise HTTPException(404, "Not found")
    # Retiring the rule is only half of it. Applying a learning WRITES a
    # MappingSuggestion (status approved, approved_by learning-engine), and
    # generation reads those, not the library — so a deleted learning kept
    # shipping through every conversion it had already touched. The tombstone
    # only ever stopped it being applied AGAIN.
    reverted = await _revert_applied_mappings(item, user.email)
    if purge:
        await item.delete()
        return {"deleted": learned_id, "purged": True, **reverted}
    await item.set({
        "is_deleted": True,
        "deleted_at": datetime.utcnow(),
        "deleted_by": user.email,
    })
    return {"deleted": learned_id, "purged": False, **reverted}


def _is_gold_derived_learning(m: LearnedMapping) -> bool:
    """Whether a learning came from a previously-uploaded GOLD EXAMPLE file (or the
    auto-capture that runs after Generate, an AI-inferred default, or a capture stamped
    with a specific conversion) — as opposed to an analyst MAPPING DOCUMENT or a HUMAN
    edit.

    Analyst, 07-Aug: the old gold files are stale; the mapping documents (uploaded in
    the tool or authored via Claude) and the user's own edits are the source of truth,
    so ignore the gold-derived records.

    KEPT (returns False):
      * anything a person authored — ``captured_by`` is set (an analyst or Claude),
        or the provenance is a manual edit ("(manual)");
      * an analyst mapping DOCUMENT — its ``captured_from`` is a plain label with no
        conversion/field markers, and it is re-seeded on startup anyway.

    RETIRED (returns True): "gold example", "auto-capture", "ai-inference", an
    "(input file)" capture, or a per-conversion capture whose ``captured_from`` carries
    a "<conversion> -- <field>" or "<source> -> <target>" marker.
    """
    if getattr(m, "captured_by", None):
        return False                        # a person (analyst or Claude) authored it
    cf = m.captured_from or ""
    low = cf.lower()
    if "(manual)" in low:
        return False                        # a human edit captured from a conversion
    return (
        "gold" in low
        or low == "auto-capture"
        or low == "ai-inference"
        or "(input file)" in low
        or " -- " in cf                     # "<conversion> -- <field>" gold/example capture
        or " → " in cf or " -> " in cf      # "<source> -> <target>" conversion capture
    )


@router.post("/purge-gold-examples")
async def purge_gold_examples(
    dry_run: bool = Query(True, description="Report what would be retired without changing anything"),
    target_object: Optional[str] = Query(None, description="Limit to one Oracle object; omit for all"),
    revert_applied: bool = Query(True, description="Also revert the mappings each learning had auto-applied"),
    user: User = Depends(get_current_user),
):
    """Retire every learning that came from a previously-uploaded GOLD example file
    (or auto-capture / AI inference / a specific conversion), leaving the analyst
    MAPPING DOCUMENTS and HUMAN edits as the only source of truth.

    ``dry_run`` (default true) reports what WOULD be retired without touching anything.
    A real run tombstones each row (restorable via /retired) and reverts the mappings
    the learning had auto-applied, so its value stops shipping in the next generate.
    The startup mapping-document seeds are untouched and continue to re-apply.
    """
    items = await LearnedMapping.find_all().to_list()
    if target_object:
        _o = target_object.strip().lower()
        items = [m for m in items if (m.target_object or "").strip().lower() == _o]
    gold = [m for m in items
            if not getattr(m, "is_deleted", False) and _is_gold_derived_learning(m)]

    by_object: dict = {}
    by_kind: dict = {}
    by_source: dict = {}
    for m in gold:
        o = m.target_object or "(none)"
        by_object[o] = by_object.get(o, 0) + 1
        by_kind[m.kind or "(none)"] = by_kind.get(m.kind or "(none)", 0) + 1
        by_source[m.captured_from or "(none)"] = by_source.get(m.captured_from or "(none)", 0) + 1
    report = {
        "scanned": len(items),
        "matched": len(gold),
        "by_object": dict(sorted(by_object.items(), key=lambda kv: -kv[1])),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "top_sources": dict(sorted(by_source.items(), key=lambda kv: -kv[1])[:20]),
        "samples": [{"object": m.target_object, "field": m.target_field, "kind": m.kind,
                     "value": (m.resolved_value or m.original_value or "")[:40],
                     "from": m.captured_from} for m in gold[:20]],
        "dry_run": dry_run,
    }
    if dry_run:
        return report

    now = datetime.utcnow()
    # 1) Tombstone every match, collecting the (client, object, field, source) triples
    #    whose auto-applied mappings must be reverted. Doing the revert per-row would
    #    call Conversion.find_all() 1,676 times; instead collect the triples and revert
    #    in ONE pass over the conversions below.
    retired = 0
    triples: set = set()              # (client_id|None, object_l, field_l, source_l)
    for m in gold:
        await m.set({"is_deleted": True, "deleted_at": now, "deleted_by": user.email})
        retired += 1
        o = (m.target_object or "").strip().lower()
        f = (m.target_field or "").strip().lower()
        s = (m.original_value or "").strip().lower()
        if o and f:
            triples.add((m.client_id, o, f, s))

    reverted = stale = 0
    if revert_applied and triples:
        from app.models.fbdi import FBDIField as _F
        from app.models.mapping import MappingSuggestion as _MS
        from app.models.output import ConvertedOutput as _CO
        from app.services.mapping_dedupe import stamp_edit
        from app.services.client_service import client_id_for_conversion
        objs_needed = {t[1] for t in triples}
        # A field is reverted if a tombstoned learning matches it — scoped to the
        # conversion's client OR global (client_id None), and matching the source
        # column when the learning named one (so retiring one rule on a field does not
        # revert a different rule on the same field). A learning that named no source
        # matches the field regardless of the mapping's source.
        keys_exact = triples                                          # (client, obj, field, source)
        keys_nosrc = {(c, o, f) for (c, o, f, s) in triples if s == ""}
        seen_conv: set = set()
        for conv in await Conversion.find_all().to_list():
            co = (conv.target_object or "").strip().lower()
            if co not in objs_needed:
                continue
            conv_client = await client_id_for_conversion(conv)
            fields = {f.id: (f.field_name or "").strip().lower()
                      for f in await _F.find(_F.template_id == conv.template_id).to_list()
                      } if conv.template_id else {}
            for mm in await _MS.find(_MS.conversion_id == conv.id).to_list():
                if mm.approved_by != "learning-engine":
                    continue
                fl = fields.get(mm.target_field_id, "")
                if not fl:
                    continue
                sl = (mm.source_column or "").strip().lower()
                hit = (
                    (conv_client, co, fl, sl) in keys_exact
                    or (None, co, fl, sl) in keys_exact
                    or (conv_client, co, fl) in keys_nosrc
                    or (None, co, fl) in keys_nosrc
                )
                if not hit:
                    continue
                await mm.set(stamp_edit({
                    "status": "suggested", "approved_by": None, "approved_at": None,
                    "review_required": 1,
                    "comment": f"Reverted — the gold learning behind this was retired by {user.email}."}))
                reverted += 1
                seen_conv.add(conv.id)
        for cid in seen_conv:
            for o in await _CO.find(_CO.conversion_id == cid).to_list():
                if o.status != "stale":
                    await o.set({"status": "stale",
                                 "stale_reason": "A learning it was built on was retired",
                                 "stale_since": now})
                    stale += 1
    report.update({"retired": retired, "mappings_reverted": reverted,
                   "outputs_marked_stale": stale})
    return report


async def _revert_applied_mappings(item: LearnedMapping, actor: str) -> dict:
    """Undo the mappings this learning wrote, and stale the outputs built on them.

    Only rows the LEARNING ENGINE approved are touched. A mapping an analyst
    approved or overrode is their decision even if a learning first proposed it,
    and silently reverting it would be worse than the bug being fixed.

    Reverted rows go back to ``suggested`` with review_required set, rather than
    being blanked: the source column may still be the right answer, and the
    analyst should see it flagged rather than find the field empty.
    """
    from app.models.conversion import Conversion
    from app.models.fbdi import FBDIField
    from app.models.mapping import MappingSuggestion
    from app.models.output import ConvertedOutput

    tgt = (item.target_field or "").strip().lower()
    src = (item.original_value or "").strip().lower()
    obj = (item.target_object or "").strip().lower()
    if not tgt or not obj:
        return {"mappings_reverted": 0, "outputs_marked_stale": 0}

    reverted, stale, seen = 0, 0, set()
    for conv in await Conversion.find_all().to_list():
        if (conv.target_object or "").strip().lower() != obj:
            continue
        if item.client_id is not None:
            from app.services.client_service import client_id_for_conversion
            if await client_id_for_conversion(conv) != item.client_id:
                continue
        fields = {f.id: f.field_name for f in await FBDIField.find(
            FBDIField.template_id == conv.template_id).to_list()} if conv.template_id else {}
        for m in await MappingSuggestion.find(
                MappingSuggestion.conversion_id == conv.id).to_list():
            if m.approved_by != "learning-engine":
                continue
            if (fields.get(m.target_field_id) or "").strip().lower() != tgt:
                continue
            # Match the source too when the learning names one, so retiring a
            # rule for a field does not revert a different rule on that field.
            if src and (m.source_column or "").strip().lower() != src:
                continue
            from app.services.mapping_dedupe import stamp_edit
            await m.set(stamp_edit({"status": "suggested", "approved_by": None,
                         "approved_at": None, "review_required": 1,
                         "comment": f"Reverted — the learning behind this was "
                                    f"retired by {actor}."}))
            reverted += 1
            seen.add(conv.id)

    for cid in seen:
        for o in await ConvertedOutput.find(ConvertedOutput.conversion_id == cid).to_list():
            if o.status != "stale":
                await o.set({"status": "stale",
                             "stale_reason": "A learning it was built on was retired",
                             "stale_since": datetime.utcnow()})
                stale += 1
    return {"mappings_reverted": reverted, "outputs_marked_stale": stale}


@router.post("/{learned_id}/restore")
async def restore_learned(
    learned_id: str,
    _: User = Depends(get_current_user),
):
    """Bring a retired learning back (undo of the delete above)."""
    item = await LearnedMapping.find_one(
        {"_id": PydanticObjectId(learned_id)}, include_deleted=True
    )
    if not item:
        raise HTTPException(404, "Not found")
    await item.set({"is_deleted": False, "deleted_at": None, "deleted_by": None})
    return _serialize(item)


@router.get("/retired/list")
async def list_retired(_: User = Depends(get_current_user)):
    """Learnings the user has retired — so a deletion is reviewable, not a
    black hole. Restore via POST /{id}/restore."""
    items = await LearnedMapping.find(
        {"is_deleted": True}, include_deleted=True
    ).sort("-deleted_at").limit(500).to_list()
    return [_serialize(i) for i in items]
