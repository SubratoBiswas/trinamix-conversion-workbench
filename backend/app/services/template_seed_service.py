"""Auto-seed the bundled Oracle FBDI templates into the tool on startup.

The templates in ``app/data/fbdi_templates`` are parsed through the SAME pipeline
as a manual upload (``parse_fbdi_template`` → FBDITemplate + FBDISheet +
FBDIField, plus a durable copy of the bytes in Mongo), so a seeded template
behaves identically to one uploaded through the Templates page: it can be picked
as a conversion target, mapped, and generated.

Idempotent + non-destructive: a template is skipped when one with the same
business object (or the same name) already exists — so the supplier templates
already loaded by the user are never duplicated, and re-deploys are a no-op.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.models.fbdi import FBDITemplate, FBDISheet, FBDIField
from app.parsers import parse_fbdi_template
from app.services.fbdi_service import _save_bytes, store_template_bytes

logger = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent.parent / "data" / "fbdi_templates"

# file name -> (template name, module, business object)
# `name` + `business_object` drive the fan-out keyword match in
# object_fanout_service (e.g. customer step needs "customer"+"import"), and
# `business_object` is the key the learning engine uses (_business_object_for),
# so it must line up with the seeded mapping-catalog target_objects.
_BUNDLED: dict[str, tuple[str, str, str]] = {
    "1_SupplierImport_POZ_SUPPLIERS_INT.xlsm":
        ("Supplier Import", "Procurement", "Supplier"),
    "2_SupplierAddress_POZ_SUPPLIER_ADDRESSES_INT.xlsm":
        ("Supplier Address Import", "Procurement", "Supplier Address"),
    "3_SupplierSite_POZ_SUPPLIER_SITES_INT.xlsm":
        ("Supplier Site Import", "Procurement", "Supplier Site"),
    "4_SupplierSiteAssignment_POZ_SITE_ASSIGNMENTS_INT.xlsm":
        ("Supplier Site Assignment Import", "Procurement", "Supplier Site Assignment"),
    "5_SupplierContacts_POZ_SUP_CONTACTS.xlsm":
        ("Supplier Contacts Import", "Procurement", "Supplier Contacts"),
    "6_SupplierBank_IBY_TEMP_EXT_PAYEES__modifiedAS400.xlsm":
        ("Supplier Bank Account Import", "Procurement", "Supplier Banks"),
    "CustomerImport_HZ_IMP__RA_CUSTOMER.xlsm":
        ("Customer Import", "Financials / Receivables", "Customer"),
    "ItemImport_EGP_SYSTEM_ITEMS_INTERFACE.xlsm":
        ("Item Import", "Product Hub / SCM", "Item"),
}


async def _existing_keys() -> tuple[set[str], set[str]]:
    """One scan of the template collection → the business objects and names that
    already exist (so we don't re-query per bundled template)."""
    objs: set[str] = set()
    names: set[str] = set()
    for t in await FBDITemplate.find_all().to_list():
        if t.business_object:
            objs.add(t.business_object.strip().lower())
        if t.name:
            names.add(t.name.strip().lower())
    return objs, names


async def _seed_one(path: Path, name: str, module: str, business_object: str) -> bool:
    contents = path.read_bytes()
    file_path, stored_name = _save_bytes(path.name, contents)
    try:
        parsed = parse_fbdi_template(file_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("template_seed: parse failed for %s: %s", path.name, exc)
        return False
    if not parsed.get("fields"):
        logger.warning("template_seed: no fields parsed from %s — skipping", path.name)
        return False

    tpl = FBDITemplate(
        name=name,
        module=module,
        business_object=business_object,
        version="1.0",
        required_field_count=sum(1 for f in parsed["fields"] if f.get("required")),
        file_name=stored_name,
        file_path=str(file_path),
        status="parsed",
        description=parsed.get("description"),
    )
    await tpl.insert()
    await store_template_bytes(tpl, stored_name, contents)

    sheet_id_by_name: dict[str, object] = {}
    for s in parsed["sheets"]:
        sheet = FBDISheet(
            template_id=tpl.id, sheet_name=s["sheet_name"],
            sequence=s["sequence"], field_count=s["field_count"],
        )
        await sheet.insert()
        sheet_id_by_name[s["sheet_name"]] = sheet.id

    docs = []
    for f in parsed["fields"]:
        f = dict(f)
        sheet_name = f.pop("sheet_name", None)
        sheet_id = sheet_id_by_name.get(sheet_name)
        if sheet_id is None:
            continue
        docs.append(FBDIField(template_id=tpl.id, sheet_id=sheet_id, **f))
    if docs:
        await FBDIField.insert_many(docs)

    logger.info("template_seed: seeded '%s' (%s) — %d sheets, %d fields",
                name, business_object, len(parsed["sheets"]), len(docs))
    return True


async def seed_fbdi_templates() -> dict:
    if not _DIR.exists():
        return {"seeded": 0, "skipped": 0, "note": "no bundled templates"}
    existing_objs, existing_names = await _existing_keys()
    seeded = skipped = failed = 0
    for fname, (name, module, bo) in _BUNDLED.items():
        path = _DIR / fname
        if not path.exists():
            continue
        try:
            if bo.strip().lower() in existing_objs or name.strip().lower() in existing_names:
                skipped += 1
                continue
            ok = await _seed_one(path, name, module, bo)
            if ok:
                seeded += 1
                existing_objs.add(bo.strip().lower())
                existing_names.add(name.strip().lower())
            else:
                failed += 1
        except Exception:  # noqa: BLE001 — never fail the whole seed on one template
            logger.exception("template_seed: failed seeding %s", fname)
            failed += 1
    if seeded or failed:
        logger.info("template_seed: seeded %d, skipped %d existing, failed %d",
                    seeded, skipped, failed)
    return {"seeded": seeded, "skipped": skipped, "failed": failed}
