"""Project-level orchestration: module auto-population, load-order derivation."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.conversion import Conversion
from app.models.dependency import Dependency
from app.models.fbdi import FBDITemplate
from app.models.project import Project


# ─── Module → (business_object, tier, planned_load_order) ────────────────────
MODULE_OBJECT_MAP: dict[str, list[dict[str, Any]]] = {
    "SCM": [
        {"business_object": "UOM",            "name": "UOM Master",             "tier": "T0", "order": 10},
        {"business_object": "Inventory Org",  "name": "Inventory Organization", "tier": "T0", "order": 15},
        {"business_object": "Item Class",     "name": "Item Class Setup",       "tier": "T0", "order": 20},
        {"business_object": "Item",           "name": "Item Master Conversion", "tier": "T1", "order": 30},
        {"business_object": "On-Hand Balance","name": "On-Hand Balance Load",   "tier": "T2", "order": 70},
        {"business_object": "BOM",            "name": "BOM Conversion",         "tier": "T2", "order": 60},
    ],
    "OM": [
        {"business_object": "Customer",       "name": "Customer Master",        "tier": "T1", "order": 40},
        {"business_object": "Sales Order",    "name": "Sales Order Backlog",    "tier": "T2", "order": 80},
    ],
    "PO": [
        {"business_object": "Supplier",       "name": "Supplier Master",        "tier": "T1", "order": 50},
        {"business_object": "Purchase Order", "name": "Open Purchase Orders",   "tier": "T2", "order": 90},
    ],
    "HCM": [
        {"business_object": "Legal Entity",   "name": "Legal Entity Setup",     "tier": "T0", "order": 5},
        {"business_object": "Business Unit",  "name": "Business Unit Setup",    "tier": "T0", "order": 6},
        {"business_object": "Employee",       "name": "Employee Master",        "tier": "T1", "order": 40},
        {"business_object": "Assignment",     "name": "Employee Assignments",   "tier": "T2", "order": 50},
    ],
    "GL": [
        {"business_object": "Chart of Accounts", "name": "Chart of Accounts",  "tier": "T0", "order": 5},
        {"business_object": "Cost Center",        "name": "Cost Centers",       "tier": "T0", "order": 6},
        {"business_object": "Journal",            "name": "Opening Balances",   "tier": "T1", "order": 40},
    ],
    "Planning": [
        {"business_object": "Item",           "name": "Item Master Conversion", "tier": "T1", "order": 30},
        {"business_object": "On-Hand Balance","name": "On-Hand Balance Load",   "tier": "T2", "order": 50},
        {"business_object": "Sales Forecast", "name": "Sales Forecasts",        "tier": "T2", "order": 60},
    ],
}

MODULE_ALIASES: dict[str, list[str]] = {
    "SCM + OM":        ["SCM", "OM"],
    "SCM + OM + PO":   ["SCM", "OM", "PO"],
    "Full Suite":      ["SCM", "OM", "PO", "HCM", "GL"],
}


async def auto_populate_conversions(
    project: Project,
    modules: list[str],
    created_by: str,
) -> list[Conversion]:
    """Create Conversion placeholders for every object implied by modules.

    Idempotent: skips any target_object already present in the project.
    Returns the list of newly-created Conversion documents.
    """
    existing_convs = await Conversion.find(
        Conversion.project_id == project.id
    ).to_list()
    existing_objects = {c.target_object for c in existing_convs if c.target_object}

    # Expand aliases
    expanded: list[str] = []
    for m in modules:
        if m in MODULE_ALIASES:
            expanded.extend(MODULE_ALIASES[m])
        else:
            expanded.append(m)

    # De-duplicate while preserving order
    seen: set[str] = set()
    module_list: list[str] = []
    for m in expanded:
        if m not in seen:
            seen.add(m)
            module_list.append(m)

    # Index templates by business_object
    templates = await FBDITemplate.find_all().to_list()
    tpl_by_obj: dict[str, FBDITemplate] = {}
    for tpl in templates:
        obj = tpl.business_object or ""
        if obj and obj not in tpl_by_obj:
            tpl_by_obj[obj] = tpl

    created: list[Conversion] = []
    for module in module_list:
        objects = MODULE_OBJECT_MAP.get(module, [])
        for obj_def in objects:
            obj_name = obj_def["business_object"]
            if obj_name in existing_objects:
                continue
            existing_objects.add(obj_name)
            tpl = tpl_by_obj.get(obj_name)
            conv = Conversion(
                project_id=project.id,
                name=obj_def["name"],
                target_object=obj_name,
                template_id=tpl.id if tpl else None,
                planned_load_order=obj_def["order"],
                status="planning",
                created_by=created_by,
            )
            await conv.insert()
            created.append(conv)

    return created


async def derive_load_order(project: Project) -> list[dict[str, Any]]:
    """Compute recommended planned_load_order for every Conversion in the project.

    Uses topological sort (Kahn's algorithm) over the Dependency table.
    Returns ordered list with {conversion_id, name, target_object, load_order}.
    """
    conversions = await Conversion.find(
        Conversion.project_id == project.id
    ).to_list()
    if not conversions:
        return []

    objects_in_project = {c.target_object for c in conversions if c.target_object}
    deps = await Dependency.find(
        Dependency.relationship_type == "prerequisite"
    ).to_list()

    prereqs: dict[str, set[str]] = {obj: set() for obj in objects_in_project}
    for d in deps:
        src = d.source_object
        tgt = d.target_object
        if src in objects_in_project and tgt in objects_in_project:
            prereqs[tgt].add(src)

    # Infer FK-based prerequisites from FBDI field names
    from app.services.learning_service import REFERENCE_KEY_FIELDS
    for c in conversions:
        if not c.template_id:
            continue
        from app.models.fbdi import FBDIField
        fields = await FBDIField.find(FBDIField.template_id == c.template_id).to_list()
        field_names = {f.field_name for f in fields}
        for master_obj, key_fields in REFERENCE_KEY_FIELDS.items():
            if c.target_object == master_obj:
                continue
            if any(kf in field_names for kf in key_fields):
                if master_obj in objects_in_project and c.target_object:
                    prereqs[c.target_object].add(master_obj)

    # Kahn's topological sort
    remaining = {obj: set(ps) for obj, ps in prereqs.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(obj for obj, ps in remaining.items() if not ps)
        if not ready:
            order.extend(sorted(remaining.keys()))
            break
        for obj in ready:
            order.append(obj)
            del remaining[obj]
            for ps in remaining.values():
                ps.discard(obj)

    conv_by_obj: dict[str, Conversion] = {
        c.target_object: c for c in conversions if c.target_object
    }

    step = 10
    result: list[dict[str, Any]] = []
    now = datetime.utcnow()
    for i, obj in enumerate(order):
        conv = conv_by_obj.get(obj)
        if not conv:
            continue
        new_order = (i + 1) * step
        await conv.set({"planned_load_order": new_order, "updated_at": now})
        result.append({
            "conversion_id": str(conv.id),
            "name": conv.name,
            "target_object": obj,
            "load_order": new_order,
            "prerequisites": sorted(prereqs.get(obj, set())),
        })
    return result
