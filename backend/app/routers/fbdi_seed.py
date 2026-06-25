"""Seed standard Oracle Fusion FBDI field definitions for well-known templates.

When a template xlsx was uploaded but parsing returned 0 fields (e.g. because
the sheet had merged cells or an unrecognised layout), this endpoint injects
the canonical Oracle Fusion field schema directly from a hard-coded dictionary
so mapping can proceed without re-uploading the file.
"""
from __future__ import annotations

from typing import Any
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.models.fbdi import FBDIField, FBDISheet, FBDITemplate
from app.models.user import User
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/api/fbdi", tags=["fbdi"])

# ── Standard Oracle Fusion FBDI field definitions ─────────────────────────────
# Each entry is a list of field dicts.  The module key matches the template
# name (case-insensitive partial match) so users can call seed without knowing
# the exact template ID — just pass ?match=InventoryOrg or similar.

_BOOL = "Character(1)"
_DATE = "Date"
_NUM  = "Number"
_VC30 = "Character(30)"
_VC60 = "Character(60)"
_VC150 = "Character(150)"
_VC240 = "Character(240)"


def _f(name: str, dtype: str = _VC60, *, req: bool = False, desc: str = "") -> dict[str, Any]:
    return {
        "field_name": name,
        "display_name": name,
        "description": desc or None,
        "required": req,
        "data_type": dtype.split("(")[0].strip().capitalize(),
        "max_length": int(dtype.split("(")[1].rstrip(")")) if "(" in dtype else None,
        "format_mask": "YYYY/MM/DD" if dtype == _DATE else None,
        "sample_value": None,
        "lookup_type": None,
        "validation_notes": None,
        "required_modules": [],
    }


STANDARD_FIELDS: dict[str, list[dict[str, Any]]] = {

    # ── Inventory Org ─────────────────────────────────────────────────────────
    "inventoryorgimport": [
        _f("OrganizationCode",           _VC30,  req=True,  desc="Unique short code for the inventory organization"),
        _f("OrganizationName",           _VC60,  req=True,  desc="Display name of the inventory organization"),
        _f("LegalEntityIdentifier",      _VC60,  req=True,  desc="Name of the legal entity this org belongs to"),
        _f("BusinessUnit",               _VC60,  req=True,  desc="Business unit associated with the org"),
        _f("SetID",                      _VC30,  req=False, desc="Reference data set identifier"),
        _f("Description",                _VC240, req=False),
        _f("ItemMasterOrganizationCode", _VC30,  req=True,  desc="Master org this org references for items"),
        _f("EffectiveStartDate",         _DATE,  req=True),
        _f("EffectiveEndDate",           _DATE,  req=False),
        _f("Calendar",                   _VC30,  req=False),
        _f("InventoryEnabledFlag",       _BOOL,  req=False, desc="Y/N — enable inventory transactions"),
        _f("ReceivingEnabledFlag",       _BOOL,  req=False),
        _f("PurchasingEnabledFlag",      _BOOL,  req=False),
        _f("DefaultCountryCode",         _VC30,  req=False),
        _f("TimeZoneCode",               _VC60,  req=False),
    ],

    # ── Item Catalog / Class ──────────────────────────────────────────────────
    "itemclassimport": [
        _f("ItemCatalogCategoryName",        _VC60,  req=True,  desc="Unique catalog category name"),
        _f("Description",                    _VC240, req=False),
        _f("ParentItemCatalogCategoryName",  _VC60,  req=False, desc="Parent category for hierarchy"),
        _f("EnabledFlag",                    _BOOL,  req=False),
        _f("InventoryItemFlag",              _BOOL,  req=False),
        _f("PurchasingEnabledFlag",          _BOOL,  req=False),
        _f("SalesOrderIssuesEnabledFlag",    _BOOL,  req=False),
        _f("BOMEnabledFlag",                 _BOOL,  req=False),
        _f("DefaultLotControlCode",          _VC30,  req=False),
        _f("DefaultSerialControlCode",       _VC30,  req=False),
        _f("DefaultPrimaryUOM",              _VC30,  req=False),
    ],

    # ── Bills of Material ─────────────────────────────────────────────────────
    "bomimport": [
        _f("OrganizationCode",         _VC30, req=True,  desc="Inventory organization code"),
        _f("AssemblyItemNumber",       _VC60, req=True,  desc="Parent/assembly item number"),
        _f("BOMName",                  _VC60, req=False, desc="BOM alternate name; blank = primary BOM"),
        _f("AlternateDesignatorCode",  _VC30, req=False),
        _f("EffectiveDate",            _DATE, req=True,  desc="BOM header effective date"),
        _f("DisableDate",              _DATE, req=False),
        _f("ComponentItemNumber",      _VC60, req=True,  desc="Component (child) item number"),
        _f("ComponentSequenceNumber",  _NUM,  req=True,  desc="Sequence within the BOM"),
        _f("ComponentQuantity",        _NUM,  req=True,  desc="Quantity of component per assembly"),
        _f("ComponentUOM",             _VC30, req=True,  desc="Unit of measure for component quantity"),
        _f("ComponentEffectiveDate",   _DATE, req=False),
        _f("ComponentDisableDate",     _DATE, req=False),
        _f("YieldFactor",              _NUM,  req=False),
        _f("MutuallyExclusiveOptions", _BOOL, req=False),
        _f("OptionalComponent",        _BOOL, req=False),
        _f("ItemDescription",          _VC240,req=False),
    ],

    # ── On-Hand Inventory Balances ────────────────────────────────────────────
    "onhandbalanceimport": [
        _f("ItemNumber",            _VC60, req=True,  desc="Inventory item number"),
        _f("OrganizationCode",      _VC30, req=True),
        _f("SubinventoryCode",      _VC30, req=True),
        _f("Locator",               _VC60, req=False, desc="Storage locator within subinventory"),
        _f("LotNumber",             _VC80 if (_VC80 := "Character(80)") else _VC60, req=False),
        _f("SerialNumber",          _VC60, req=False),
        _f("Quantity",              _NUM,  req=True,  desc="On-hand quantity"),
        _f("UOMCode",               _VC30, req=True,  desc="Unit of measure code (e.g. EA)"),
        _f("TransactionDate",       _DATE, req=True),
        _f("RevisionNumber",        _VC30, req=False),
        _f("ReasonCode",            _VC30, req=False),
        _f("TransactionReference",  _VC30, req=False),
        _f("CostGroup",             _VC30, req=False),
        _f("ProjectNumber",         _VC30, req=False),
        _f("TaskNumber",            _VC30, req=False),
    ],
}


@router.post("/templates/{template_id}/seed-standard-fields", status_code=200)
async def seed_standard_fields(
    template_id: str,
    _: User = Depends(get_current_user),
):
    """Inject standard Oracle Fusion field definitions for a template that has 0 parsed fields.

    Matches the template name (case-insensitive, partial) against the known schema dictionary.
    Only operates when the template currently has 0 FBDIField records (safe to call repeatedly).
    """
    tpl = await FBDITemplate.get(PydanticObjectId(template_id))
    if not tpl:
        raise HTTPException(404, "Template not found")

    existing_count = await FBDIField.find(FBDIField.template_id == tpl.id).count()
    if existing_count > 0:
        return {"seeded": 0, "existing": existing_count, "message": "Template already has fields — skipped"}

    # Find matching schema by template name
    tpl_key = tpl.name.lower().replace(" ", "").replace("_", "").replace("-", "")
    schema_key = next(
        (k for k in STANDARD_FIELDS if k in tpl_key or tpl_key in k),
        None,
    )
    if schema_key is None:
        # Also try business_object match
        bo = (tpl.business_object or "").lower().replace(" ", "")
        schema_key = next(
            (k for k in STANDARD_FIELDS if k in bo or bo in k),
            None,
        )

    if schema_key is None:
        raise HTTPException(
            422,
            f"No standard schema found for template '{tpl.name}'. "
            f"Known schemas: {list(STANDARD_FIELDS.keys())}",
        )

    # Ensure a sheet exists to attach fields to
    sheet = await FBDISheet.find_one(FBDISheet.template_id == tpl.id)
    if sheet is None:
        sheet = FBDISheet(
            template_id=tpl.id,
            sheet_name="Import",
            sequence=0,
            field_count=0,
        )
        await sheet.insert()

    field_defs = STANDARD_FIELDS[schema_key]
    for seq, fdef in enumerate(field_defs, start=1):
        await FBDIField(
            template_id=tpl.id,
            sheet_id=sheet.id,
            sequence=seq,
            **fdef,
        ).insert()

    # Update sheet field_count and template status
    await sheet.set({"field_count": len(field_defs)})
    await tpl.set({"status": "parsed"})

    return {
        "seeded": len(field_defs),
        "existing": 0,
        "schema_matched": schema_key,
        "message": f"Seeded {len(field_defs)} standard Oracle Fusion fields for '{tpl.name}'",
    }
