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
    "6_SupplierBank_IBY_TEMP_EXT_PAYEES.xlsm":
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
        is_global=True,  # bundled Oracle-standard templates apply to every client
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


async def _needs_comment_enrichment(business_object: str, name: str) -> bool:
    """Does this template's stored field set predate the comment miner?

    Detected by the data rather than by a version flag: if not one field carries a
    ``db_column``, the rules Oracle publishes in its header comments were never read.
    """
    tpl = await _template_for(business_object, name)
    if tpl is None:
        return False
    hit = await FBDIField.find(
        FBDIField.template_id == tpl.id,
        {"db_column": {"$nin": [None, ""]}},
    ).first_or_none()
    return hit is None


async def _template_for(business_object: str, name: str):
    for q in ({"business_object": business_object}, {"name": name}):
        tpl = await FBDITemplate.find_one(q)
        if tpl:
            return tpl
    return None


async def _enrich_from_comments(path: Path, business_object: str, name: str) -> int:
    """Re-read the workbook and fold its header-comment rules onto the STORED fields.

    Updates in place rather than re-seeding the template: the existing field ids are
    referenced by every mapping, rule and gold row on file, so replacing them would
    orphan the lot. Matching is by sheet + normalised field name.
    """
    import re as _re
    from app.services.template_comments import apply_to_field, constraints_by_sheet

    tpl = await _template_for(business_object, name)
    if tpl is None:
        return 0
    try:
        cons = constraints_by_sheet(str(path))
    except Exception:                                           # noqa: BLE001
        return 0
    if not cons:
        return 0
    try:
        parsed = parse_fbdi_template(path)
    except Exception:                                           # noqa: BLE001
        return 0
    def _n(x):
        return _re.sub(r"[^a-z0-9]", "", str(x or "").lower())
    fresh = {(_n(f.get("sheet_name")), _n(f.get("field_name"))): f
             for f in parsed.get("fields", [])}
    sheets = {s.id: s.sheet_name
              for s in await FBDISheet.find(FBDISheet.template_id == tpl.id).to_list()}
    n = 0
    for fld in await FBDIField.find(FBDIField.template_id == tpl.id).to_list():
        src = fresh.get((_n(sheets.get(fld.sheet_id)), _n(fld.field_name)))
        if not src:
            continue
        patch = {k: src.get(k) for k in
                 ("db_column", "nullable", "precision", "scale", "do_not_populate",
                  "import_actions", "comment_text", "data_type", "max_length",
                  "format_mask", "allowed_values")
                 if src.get(k) not in (None, "", [], False)}
        # required is a union: the header '*' OR the comment's NOT NULL. Never
        # un-require a field the template marked required.
        if src.get("required"):
            patch["required"] = True
        if patch:
            await fld.set(patch)
            n += 1
    logger.info("template_seed: enriched %d field(s) of %s from header comments",
                n, name)
    return n


async def seed_fbdi_templates() -> dict:
    if not _DIR.exists():
        return {"seeded": 0, "skipped": 0, "note": "no bundled templates"}
    existing_objs, existing_names = await _existing_keys()
    seeded = skipped = failed = enriched = 0
    for fname, (name, module, bo) in _BUNDLED.items():
        path = _DIR / fname
        if not path.exists():
            continue
        try:
            if bo.strip().lower() in existing_objs or name.strip().lower() in existing_names:
                # Present already — but is it present with the COLUMN RULES Oracle
                # publishes in the template's header comments? A template seeded before
                # the comment miner existed has fields with no db_column, no precision,
                # no NOT NULL and no value set, so the whole column-rules feature is
                # inert on it: live, a Supplier conversion reported 3 columns carrying a
                # rule where the parser mines 136. Shipping a feature that only works on
                # templates nobody has yet is not shipping it, so re-enrich in place.
                if await _needs_comment_enrichment(bo, name):
                    got = await _enrich_from_comments(path, bo, name)
                    enriched += got
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
    return {"seeded": seeded, "skipped": skipped, "failed": failed,
            "fields_enriched": enriched}


async def ensure_customer_multisheet() -> dict:
    """Guarantee the real 19-sheet Customer Import exists and Customer conversions
    point at it — automatically, at startup.

    A flat synthetic "Customer Import" (one sheet) can occupy the name+object and
    make the idempotent seed SKIP the real HZ_IMP file, so conversions end up on a
    flat template and generate a flat file. This repairs that without any button:
    force-seed the real bundled template when no multi-sheet Customer template
    exists, then re-point flat-template conversions onto it. Re-pointing only
    changes template_id (cheap) — the stale mappings are cleared so a later Re-run
    AI rebuilds them against the real 19 sheets. No inline AI mapping, so it can't
    time out.

    Idempotent: once a multi-sheet Customer template exists and conversions point at
    it, every subsequent boot is a no-op.
    """
    from app.models.conversion import Conversion
    from app.models.mapping import MappingSuggestion

    def _is_customer(t) -> bool:
        bo = (t.business_object or "").strip().lower()
        if bo:
            return bo == "customer"
        return (t.name or "").strip().lower() in ("customer import", "customerimport")

    templates = await FBDITemplate.find_all().to_list()
    cust = [t for t in templates if _is_customer(t)]
    counts: dict = {}
    for t in cust:
        counts[t.id] = await FBDISheet.find(FBDISheet.template_id == t.id).count()

    real = max(cust, key=lambda t: counts.get(t.id, 0)) if cust else None
    seeded = False
    if real is None or counts.get(real.id, 0) < 5:
        bundled = _DIR / "CustomerImport_HZ_IMP__RA_CUSTOMER.xlsm"
        if not bundled.exists():
            return {"note": "bundled customer template missing", "seeded": False}
        ok = await _seed_one(bundled, "Customer Import (HZ_IMP)",
                             "Financials / Receivables", "Customer")
        if not ok:
            return {"note": "failed to seed real customer template", "seeded": False}
        seeded = True
        templates = await FBDITemplate.find_all().to_list()
        cust = [t for t in templates if _is_customer(t)]
        for t in cust:
            counts[t.id] = await FBDISheet.find(FBDISheet.template_id == t.id).count()
        real = max(cust, key=lambda t: counts.get(t.id, 0))

    flat_ids = {t.id for t in cust if t.id != real.id and counts.get(t.id, 0) < 5}
    repointed = 0
    if flat_ids:
        for c in await Conversion.find_all().to_list():
            if c.template_id in flat_ids:
                await c.set({"template_id": real.id})
                # Clear stale mappings (they referenced the flat template's fields);
                # Re-run AI on the conversion rebuilds them against the real sheets.
                await MappingSuggestion.find(MappingSuggestion.conversion_id == c.id).delete()
                repointed += 1

    if seeded or repointed:
        logger.info("customer template repair: seeded_real=%s, repointed=%d conversions",
                    seeded, repointed)
    return {"seeded_real_template": seeded, "real_sheets": counts.get(real.id) if real else 0,
            "conversions_repointed": repointed}


async def ensure_item_multisheet() -> dict:
    """Guarantee the real 17-sheet Product Hub Item Import exists and Item
    conversions point at it — automatically, at startup.

    Same failure mode as Customer: a flat synthetic "Item"/"Item Import" (one
    "Import" sheet, seeded from the generic itemmasterimport schema in fbdi_seed)
    can occupy the name+object, which makes the idempotent template seed SKIP the
    real EGP_SYSTEM_ITEMS_INTERFACE workbook. Conversions then sit on the flat
    template and generate a single 26-column CSV instead of the 18-sheet FBDI.
    This repairs it without any button: force-seed the real bundled template when
    no multi-sheet Item template exists, then re-point flat-template conversions
    onto it and clear their stale mappings so a Re-run AI rebuilds them against
    the real sheets. No inline AI mapping, so it can't time out. Idempotent.
    """
    from app.models.conversion import Conversion
    from app.models.mapping import MappingSuggestion

    # Child item objects that are their OWN templates — must NOT be swept into the
    # main Item Master repair (they map to different interface tables).
    _ITEM_CHILD = ("cost", "categor", "revision", "relationship", "association",
                   "spec", "structure", "attachment", "trading", "supplier",
                   "organization", "cross", "price", "uom")

    def _is_item(t) -> bool:
        bo = (t.business_object or "").strip().lower()
        nm = (t.name or "").strip().lower()
        # Never treat a child item object as the item master.
        if any(k in bo for k in _ITEM_CHILD) or any(k in nm for k in _ITEM_CHILD):
            return False
        if bo in ("item", "item master"):
            return True
        # Match ANY item-master template by name — including source-prefixed flats
        # like "SyteLine ERP Item Import" or "NetSuite Item Master" that the narrow
        # exact-name list missed. Requires an item + import/master token.
        return ("item" in nm) and ("import" in nm or "master" in nm
                                   or "egp_system_items" in nm or nm == "item")

    templates = await FBDITemplate.find_all().to_list()
    items = [t for t in templates if _is_item(t)]
    counts: dict = {}
    for t in items:
        counts[t.id] = await FBDISheet.find(FBDISheet.template_id == t.id).count()

    real = max(items, key=lambda t: counts.get(t.id, 0)) if items else None
    seeded = False
    if real is None or counts.get(real.id, 0) < 5:
        bundled = _DIR / "ItemImport_EGP_SYSTEM_ITEMS_INTERFACE.xlsm"
        if not bundled.exists():
            return {"note": "bundled item template missing", "seeded": False}
        ok = await _seed_one(bundled, "Item Import", "Product Hub / SCM", "Item")
        if not ok:
            return {"note": "failed to seed real item template", "seeded": False}
        seeded = True
        templates = await FBDITemplate.find_all().to_list()
        items = [t for t in templates if _is_item(t)]
        for t in items:
            counts[t.id] = await FBDISheet.find(FBDISheet.template_id == t.id).count()
        real = max(items, key=lambda t: counts.get(t.id, 0))

    flat_ids = {t.id for t in items if t.id != real.id and counts.get(t.id, 0) < 5}
    repointed = 0
    if flat_ids:
        for c in await Conversion.find_all().to_list():
            if c.template_id in flat_ids:
                await c.set({"template_id": real.id})
                await MappingSuggestion.find(MappingSuggestion.conversion_id == c.id).delete()
                repointed += 1

    if seeded or repointed:
        logger.info("item template repair: seeded_real=%s, repointed=%d conversions",
                    seeded, repointed)
    return {"seeded_real_template": seeded, "real_sheets": counts.get(real.id) if real else 0,
            "conversions_repointed": repointed}


async def ensure_bom_multisheet() -> dict:
    """Guarantee the real multi-sheet BOM / Item Structure workbook exists — at
    startup, on any database.

    Same skip trap as Customer/Item: a thin pre-seeded "BomImport" (one sheet,
    business_object "BOM") occupies the object, so the idempotent template seed
    SKIPS the real 4-sheet ItemStructureImportTemplate (EGP_STRUCTURES_INTERFACE /
    EGP_COMPONENTS_INTERFACE / EGP_SUB_COMPS_INTERFACE / EGP_REF_DESGS_INTERFACE).
    BOM conversions then have nowhere to map the analyst BOM learnings. This
    force-seeds the bundled workbook when no multi-sheet BOM template is present
    and re-points any BOM conversion sitting on the thin template onto it. No
    inline AI mapping, so it can't time out. Idempotent — a run where the rich
    template already exists is a no-op (won't create a duplicate)."""
    from app.models.conversion import Conversion
    from app.models.mapping import MappingSuggestion

    def _is_bom(t) -> bool:
        bo = (t.business_object or "").strip().lower()
        nm = (t.name or "").strip().lower()
        return (bo in ("bom", "bill of materials", "bill of material")
                or "bom" in nm or "item structure" in nm or "structures_interface" in nm)

    templates = await FBDITemplate.find_all().to_list()
    boms = [t for t in templates if _is_bom(t)]
    counts: dict = {}
    for t in boms:
        counts[t.id] = await FBDISheet.find(FBDISheet.template_id == t.id).count()

    real = max(boms, key=lambda t: counts.get(t.id, 0)) if boms else None
    seeded = False
    # The real Item Structure workbook has 4 interface sheets; a thin BomImport has
    # 1. Seed the bundle whenever the richest BOM template we have is still flat.
    if real is None or counts.get(real.id, 0) < 2:
        bundled = _DIR / "BOMItemStructure_EGP_STRUCTURES_INT.xlsm"
        if not bundled.exists():
            return {"note": "bundled BOM template missing", "seeded": False}
        ok = await _seed_one(bundled, "BOM Item Structure Import", "Product Hub / SCM", "BOM")
        if not ok:
            return {"note": "failed to seed real BOM template", "seeded": False}
        seeded = True
        templates = await FBDITemplate.find_all().to_list()
        boms = [t for t in templates if _is_bom(t)]
        for t in boms:
            counts[t.id] = await FBDISheet.find(FBDISheet.template_id == t.id).count()
        real = max(boms, key=lambda t: counts.get(t.id, 0))

    flat_ids = {t.id for t in boms if t.id != real.id and counts.get(t.id, 0) < 2}
    repointed = 0
    if flat_ids:
        for c in await Conversion.find_all().to_list():
            if c.template_id in flat_ids:
                await c.set({"template_id": real.id})
                await MappingSuggestion.find(MappingSuggestion.conversion_id == c.id).delete()
                repointed += 1

    if seeded or repointed:
        logger.info("BOM template repair: seeded_real=%s, repointed=%d conversions",
                    seeded, repointed)
    return {"seeded_real_template": seeded, "real_sheets": counts.get(real.id) if real else 0,
            "conversions_repointed": repointed}
