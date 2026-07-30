"""FBDI template upload, parse, and metadata correction service."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import UploadFile

logger = logging.getLogger(__name__)

from app.config import settings
from app.models.fbdi import FBDITemplate, FBDISheet, FBDIField, FBDITemplateFile
from app.parsers import parse_fbdi_template


ALLOWED_FBDI_EXTS = {".xlsx", ".xlsm", ".xls"}


def _save_bytes(filename: str, contents: bytes) -> tuple[Path, str]:
    """Save raw bytes to upload dir, avoiding filename collisions."""
    target_dir = settings.upload_path / "fbdi"
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    target = target_dir / safe_name
    counter = 1
    while target.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        target = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    target.write_bytes(contents)
    return target, target.name


async def store_template_bytes(tpl: FBDITemplate, filename: str, contents: bytes) -> None:
    """Persist the raw uploaded bytes in MongoDB (durable across redeploys).
    One record per template; replaces any prior copy."""
    try:
        await FBDITemplateFile.find(FBDITemplateFile.template_id == tpl.id).delete()
        await FBDITemplateFile(
            template_id=tpl.id,
            file_name=Path(filename).name,
            content=contents,
            size=len(contents),
        ).insert()
    except Exception as exc:  # storage is best-effort; never break the upload
        logger.exception(f"Failed to store template bytes in Mongo for {tpl.id}: {exc}")


_BUNDLED_DIR = Path(__file__).resolve().parent.parent / "data" / "fbdi_templates"

# Interface-table token -> bundled Oracle workbook. Keyed on the table name because
# that is what the file names and the templates' primary sheet names agree on;
# business_object spellings drift ("Supplier Site" / "Supplier Sites" / "SupplierSite").
_BUNDLED_BY_TABLE: dict[str, str] = {
    "pozsuppliersint": "1_SupplierImport_POZ_SUPPLIERS_INT.xlsm",
    "pozsupplieraddressesint": "2_SupplierAddress_POZ_SUPPLIER_ADDRESSES_INT.xlsm",
    "pozsuppliersitesint": "3_SupplierSite_POZ_SUPPLIER_SITES_INT.xlsm",
    "pozsiteassignmentsint": "4_SupplierSiteAssignment_POZ_SITE_ASSIGNMENTS_INT.xlsm",
    "pozsupcontacts": "5_SupplierContacts_POZ_SUP_CONTACTS.xlsm",
    "ibytempextpayees": "6_SupplierBank_IBY_TEMP_EXT_PAYEES.xlsm",
    "hzimp": "CustomerImport_HZ_IMP__RA_CUSTOMER.xlsm",
    "egpsystemitemsinterface": "ItemImport_EGP_SYSTEM_ITEMS_INTERFACE.xlsm",
    "egpstructuresint": "BOMItemStructure_EGP_STRUCTURES_INT.xlsm",
}

# Business-object spellings that map to an interface table, for templates whose
# sheets aren't loaded (materialize is called with just the template record).
_BUNDLED_BY_OBJECT: dict[str, str] = {
    "supplier": "pozsuppliersint",
    "supplierimport": "pozsuppliersint",
    "supplieraddress": "pozsupplieraddressesint",
    "supplieraddresses": "pozsupplieraddressesint",
    "suppliersite": "pozsuppliersitesint",
    "suppliersites": "pozsuppliersitesint",
    "suppliersiteassignment": "pozsiteassignmentsint",
    "suppliersiteassignments": "pozsiteassignmentsint",
    "suppliercontact": "pozsupcontacts",
    "suppliercontacts": "pozsupcontacts",
    "supplierbank": "ibytempextpayees",
    "supplierbanks": "ibytempextpayees",
    "supplierbankaccount": "ibytempextpayees",
    "supplierbankaccounts": "ibytempextpayees",
    "customer": "hzimp",
    "customerimport": "hzimp",
    "item": "egpsystemitemsinterface",
    "itemimport": "egpsystemitemsinterface",
    "itemstructure": "egpstructuresint",
    "bom": "egpstructuresint",
}


def _bundled_template_for(tpl: FBDITemplate) -> Path | None:
    """The Oracle workbook shipped in the repo for this template's interface."""
    import re as _re

    def _n(s) -> str:
        return _re.sub(r"[^a-z0-9]", "", str(s or "").lower())

    for cand in (tpl.business_object, tpl.name, tpl.file_name):
        key = _BUNDLED_BY_OBJECT.get(_n(cand))
        if not key:
            # The stored name often carries the table itself
            # ("Supplier Site POZ_SUPPLIER_SITES_INT").
            key = next((t for t in _BUNDLED_BY_TABLE if t in _n(cand)), None)
        if key:
            p = _BUNDLED_DIR / _BUNDLED_BY_TABLE[key]
            if p.exists():
                logger.info("template %s (%r): no stored file — using bundled %s",
                            tpl.id, tpl.business_object, p.name)
                return p
    logger.warning("template %s (%r): no stored file and no bundled workbook — "
                   "output will be a synthesised xlsx, not the Oracle template",
                   tpl.id, tpl.business_object)
    return None


async def materialize_template_file(tpl: FBDITemplate) -> Path | None:
    """Return a filesystem path to the template's raw file for parsing.

    Prefers the on-disk copy; if the ephemeral disk was wiped (e.g. after a
    Render redeploy), rehydrates the bytes stored in MongoDB to a local file and
    returns that. Also refreshes tpl.file_path so later reads hit the disk copy.
    Returns None only when no bytes exist anywhere.
    """
    if tpl.file_path and Path(tpl.file_path).exists():
        return Path(tpl.file_path)
    rec = await FBDITemplateFile.find_one(FBDITemplateFile.template_id == tpl.id)
    if not rec or not rec.content:
        # Last resort: the Oracle workbook bundled in the repo for this interface.
        # A template record whose file was never stored used to fall through to a
        # SYNTHESISED xlsx — the columns are right (they come from the parsed field
        # records) but the workbook is not Oracle's: no "Instructions and CSV
        # Generation" sheet, no macros, .xlsx instead of .xlsm. That is what the
        # 28-Jul "FBDI templates" download shipped for Supplier Site, and nothing in
        # the file says so. The real workbooks are already in app/data/fbdi_templates,
        # so prefer them over degrading.
        return _bundled_template_for(tpl)
    name = rec.file_name or tpl.file_name or f"{tpl.id}.xlsx"
    target = settings.upload_path / "fbdi" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(rec.content)
    try:
        await tpl.set({"file_path": str(target), "file_name": target.name})
    except Exception:
        pass
    return target


async def create_template_from_upload(
    upload: UploadFile,
    name: str | None,
    module: str | None,
    business_object: str | None,
) -> FBDITemplate:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_FBDI_EXTS:
        raise ValueError(f"Unsupported FBDI file extension: {ext}")

    # Must use await upload.read() — shutil.copyfileobj on upload.file is
    # unreliable in async FastAPI (buffer may already be consumed).
    contents = await upload.read()
    file_path, stored_name = _save_bytes(upload.filename or "template.xlsx", contents)
    file_size = len(contents)
    logger.info(f"FBDI upload saved: {file_path} size={file_size}")

    try:
        parsed = parse_fbdi_template(file_path)
    except Exception as exc:
        logger.exception(f"FBDI parse error for {file_path}: {exc}")
        parsed = {"business_object": None, "description": None, "sheets": [], "fields": []}
    logger.info(f"FBDI parse result: {len(parsed['fields'])} fields, {len(parsed['sheets'])} sheets")

    required_field_count = sum(1 for f in parsed["fields"] if f.get("required"))
    tpl = FBDITemplate(
        name=name or Path(upload.filename or stored_name).stem,
        module=module,
        business_object=business_object or parsed.get("business_object"),
        version="1.0",
        required_field_count=required_field_count,
        file_name=stored_name,
        file_path=str(file_path),
        status="parsed" if parsed["fields"] else "manual",
        description=parsed.get("description"),
    )
    await tpl.insert()
    # Durable copy in MongoDB so re-parse still works after a redeploy wipes disk.
    await store_template_bytes(tpl, stored_name, contents)

    sheet_id_by_name: dict[str, object] = {}
    for s in parsed["sheets"]:
        sheet = FBDISheet(
            template_id=tpl.id,
            sheet_name=s["sheet_name"],
            sequence=s["sequence"],
            field_count=s["field_count"],
        )
        await sheet.insert()
        sheet_id_by_name[s["sheet_name"]] = sheet.id

    for f in parsed["fields"]:
        sheet_name = f.pop("sheet_name", None)
        sheet_id = sheet_id_by_name.get(sheet_name)
        if sheet_id is None:
            continue
        await FBDIField(template_id=tpl.id, sheet_id=sheet_id, **f).insert()

    return tpl
