"""One source dataset -> the full set of FBDI templates for a conversion object.

Given a conversion *object type* (Supplier / Item / Customer / ...), resolve the
ordered set of FBDI templates that object requires, create one auto-mapped
conversion per resolved template from a SINGLE source dataset, set the Fusion
load order, and chain the load-sequence dependencies. Templates that aren't
seeded in the tool yet are reported as ``missing`` so the user knows exactly
which Oracle templates still need to be uploaded.

This is the engine behind requirement #1: "one source data dump must generate
all related FBDI templates for that conversion object."
"""
from __future__ import annotations

from datetime import datetime

from beanie import PydanticObjectId

from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.dependency import Dependency
from app.models.fbdi import FBDITemplate


# Each step matches ONE FBDI template. A template matches a step when its
# name/business_object contains ALL of ``all`` and NONE of ``none`` (lowercased).
# ``object`` is the canonical target-object label used on the conversion + the
# load-sequence dependency graph. Steps are listed in Fusion load order.
OBJECT_TEMPLATE_CATALOG: dict[str, list[dict]] = {
    "supplier": [
        {"label": "Supplier Import",            "object": "Supplier Import",            "all": ["supplier"],                       "none": ["address", "site", "contact", "bank", "assignment"]},
        {"label": "Supplier Address",           "object": "Supplier Address",           "all": ["supplier", "address"],            "none": ["contact"]},
        {"label": "Supplier Site",              "object": "Supplier Site",              "all": ["supplier", "site"],               "none": ["assignment"]},
        {"label": "Supplier Site Assignment",   "object": "Supplier Site Assignment",   "all": ["supplier", "site", "assignment"], "none": []},
        {"label": "Supplier Contacts",          "object": "Supplier Contacts",          "all": ["supplier", "contact"],            "none": ["address"]},
        {"label": "Supplier Contact Addresses", "object": "Supplier Contact Addresses", "all": ["contact", "address"],             "none": []},
        {"label": "Supplier Banks",             "object": "Supplier Banks",             "all": ["bank"],                           "none": []},
    ],
    "customer": [
        {"label": "Customer Parties",       "object": "Customer Parties",       "all": ["part"],             "none": []},
        {"label": "Customer Addresses",     "object": "Customer Addresses",     "all": ["address"],          "none": ["contact"]},
        {"label": "Customer Accounts",      "object": "Customer Accounts",      "all": ["account"],          "none": ["site", "profile"]},
        {"label": "Customer Account Sites", "object": "Customer Account Sites", "all": ["account", "site"],  "none": ["use"]},
        {"label": "Customer Site Uses",     "object": "Customer Site Uses",     "all": ["site", "use"],      "none": []},
        {"label": "Customer Contacts",      "object": "Customer Contacts",      "all": ["contact"],          "none": []},
        {"label": "Customer Profiles",      "object": "Customer Profiles",      "all": ["profile"],          "none": []},
    ],
    "item": [
        {"label": "Item Import",           "object": "Item Import",           "all": ["item"],             "none": ["revision", "category", "cross", "relationship", "org"]},
        {"label": "Item Revisions",        "object": "Item Revisions",        "all": ["item", "revision"], "none": []},
        {"label": "Item Categories",       "object": "Item Categories",       "all": ["categor"],          "none": []},
        {"label": "Item Org Assignments",  "object": "Item Org Assignments",  "all": ["item", "org"],      "none": []},
        {"label": "Item Cross References", "object": "Item Cross References",  "all": ["cross"],            "none": []},
    ],
    "ap_invoice": [
        {"label": "AP Invoice Headers", "object": "AP Invoice Headers", "all": ["invoice"], "none": ["line"]},
        {"label": "AP Invoice Lines",   "object": "AP Invoice Lines",   "all": ["invoice", "line"], "none": []},
    ],
    "ar_invoice": [
        {"label": "AR Invoice Lines",         "object": "AR Invoice Lines",         "all": ["line"],         "none": ["distribution", "credit"]},
        {"label": "AR Invoice Distributions", "object": "AR Invoice Distributions", "all": ["distribution"], "none": []},
        {"label": "AR Sales Credits",         "object": "AR Sales Credits",         "all": ["credit"],       "none": []},
    ],
    "gl_journal": [
        {"label": "GL Journal Import", "object": "GL Journal Import", "all": ["journal"], "none": []},
    ],
}

# Free-text aliases the UI / detector might supply -> canonical catalog key.
OBJECT_ALIASES: dict[str, str] = {
    "supplier": "supplier", "suppliers": "supplier", "vendor": "supplier", "vendors": "supplier",
    "customer": "customer", "customers": "customer", "client": "customer", "clients": "customer",
    "item": "item", "items": "item", "product": "item", "products": "item", "material": "item",
    "ap invoice": "ap_invoice", "ap_invoice": "ap_invoice", "payables": "ap_invoice", "ap invoices": "ap_invoice",
    "ar invoice": "ar_invoice", "ar_invoice": "ar_invoice", "receivables": "ar_invoice", "ar invoices": "ar_invoice",
    "gl": "gl_journal", "gl journal": "gl_journal", "gl_journal": "gl_journal", "journal": "gl_journal",
}


def object_types() -> list[dict]:
    """Catalog summary for the UI picker: [{key, label, step_count, steps:[...]}]."""
    labels = {
        "supplier": "Supplier", "customer": "Customer", "item": "Item",
        "ap_invoice": "AP Invoices", "ar_invoice": "AR Invoices", "gl_journal": "GL Journals",
    }
    out = []
    for key, steps in OBJECT_TEMPLATE_CATALOG.items():
        out.append({
            "key": key,
            "label": labels.get(key, key.title()),
            "step_count": len(steps),
            "steps": [{"label": s["label"], "load_order": i} for i, s in enumerate(steps, start=1)],
        })
    return out


def resolve_object_key(object_type: str | None) -> str | None:
    if not object_type:
        return None
    k = object_type.strip().lower()
    if k in OBJECT_TEMPLATE_CATALOG:
        return k
    if k in OBJECT_ALIASES:
        return OBJECT_ALIASES[k]
    # substring inference (e.g. "Supplier Addresses Interface" -> supplier)
    for alias, key in OBJECT_ALIASES.items():
        if alias in k:
            return key
    return None


def _matches(template: FBDITemplate, step: dict) -> bool:
    hay = f"{template.name or ''} {template.business_object or ''}".lower()
    if any(kw not in hay for kw in step["all"]):
        return False
    if any(kw in hay for kw in step.get("none", [])):
        return False
    return True


def _find_template_for_step(step: dict, templates: list[FBDITemplate]) -> FBDITemplate | None:
    matches = [t for t in templates if _matches(t, step)]
    if not matches:
        return None
    # Prefer a fully-parsed real Oracle template with the most required fields.
    matches.sort(key=lambda t: ((t.status == "parsed"), (t.required_field_count or 0)), reverse=True)
    return matches[0]


async def generate_object_template_set(
    project_id: str, dataset_id: str, object_type: str,
) -> dict:
    """Fan a single dataset out to every FBDI template its object type needs."""
    key = resolve_object_key(object_type)
    if not key:
        return {"error": f"Unknown conversion object type: {object_type!r}",
                "created": [], "missing": [], "existing": []}

    dataset = await Dataset.get(PydanticObjectId(dataset_id))
    if not dataset:
        return {"error": "Dataset not found", "created": [], "missing": [], "existing": []}

    templates = await FBDITemplate.find_all().to_list()
    steps = OBJECT_TEMPLATE_CATALOG[key]

    existing_convs = await Conversion.find(
        Conversion.project_id == PydanticObjectId(project_id)
    ).to_list()
    existing_pairs = {
        (str(c.dataset_id), str(c.template_id))
        for c in existing_convs if c.dataset_id and c.template_id
    }

    created: list[dict] = []
    existing: list[dict] = []
    missing: list[dict] = []
    resolved_objects: list[str] = []
    now = datetime.utcnow()

    for i, step in enumerate(steps, start=1):
        tpl = _find_template_for_step(step, templates)
        if not tpl:
            missing.append({"label": step["label"], "load_order": i})
            continue
        resolved_objects.append(step["object"])
        if (str(dataset.id), str(tpl.id)) in existing_pairs:
            existing.append({"label": step["label"], "template": tpl.name, "load_order": i})
            continue
        conv = Conversion(
            project_id=PydanticObjectId(project_id),
            name=f"{dataset.name} → {step['label']}",
            dataset_id=dataset.id,
            template_id=tpl.id,
            target_object=step["object"],
            planned_load_order=i,
            source_type="dataset",
            output_mode="fbdi_download",
            status="draft",
            created_at=now,
            updated_at=now,
        )
        await conv.insert()
        created.append({
            "label": step["label"], "template": tpl.name,
            "conversion_id": str(conv.id), "load_order": i,
        })

    # Chain load-sequence dependency edges across resolved objects (idempotent).
    existing_deps = await Dependency.find(Dependency.relationship_type == "prerequisite").to_list()
    dep_pairs = {(d.source_object.lower(), d.target_object.lower()) for d in existing_deps}
    for src, tgt in zip(resolved_objects, resolved_objects[1:]):
        if (src.lower(), tgt.lower()) in dep_pairs:
            continue
        await Dependency(
            source_object=src, target_object=tgt,
            relationship_type="prerequisite",
            description=f"Load sequence: {src} → {tgt}",
        ).insert()
        dep_pairs.add((src.lower(), tgt.lower()))

    return {
        "object_type": key,
        "created": created,
        "existing": existing,
        "missing": missing,
        "resolved_count": len(resolved_objects),
        "total_steps": len(steps),
    }
