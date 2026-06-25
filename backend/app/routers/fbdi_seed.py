"""Seed standard Oracle Fusion FBDI field definitions for well-known templates.

When a template xlsx was uploaded but parsing returned 0 fields (e.g. because
the sheet had merged cells or an unrecognised layout), this endpoint injects
the canonical Oracle Fusion field schema directly from a hard-coded dictionary
so mapping can proceed without re-uploading the file.

`auto_seed_if_empty(tpl)` is a shared utility called:
  • at upload time when the parser returns 0 fields
  • in the _auto_map background task before running mapping suggestions
"""
from __future__ import annotations

import logging
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.models.fbdi import FBDIField, FBDISheet, FBDITemplate
from app.models.user import User
from app.services.auth_service import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fbdi", tags=["fbdi"])

# ── Field definition helpers ──────────────────────────────────────────────────
_BOOL = "Character(1)"
_DATE = "Date"
_NUM  = "Number"
_VC10  = "Character(10)"
_VC15  = "Character(15)"
_VC25  = "Character(25)"
_VC30  = "Character(30)"
_VC50  = "Character(50)"
_VC60  = "Character(60)"
_VC80  = "Character(80)"
_VC100 = "Character(100)"
_VC150 = "Character(150)"
_VC240 = "Character(240)"
_VC300 = "Character(300)"
_VC360 = "Character(360)"
_VC2000 = "Character(2000)"


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


# ── Standard Oracle Fusion FBDI field definitions (all 10 objects) ────────────
# Keys are the template name stripped of spaces/dashes/underscores, lowercased.
# Matching is partial — "inventoryorg" matches "InventoryOrgImport" etc.

STANDARD_FIELDS: dict[str, list[dict[str, Any]]] = {

    # ── 1. UOM ────────────────────────────────────────────────────────────────
    "uomimport": [
        _f("UOMClass",                  _VC30,  req=True,  desc="Unit of measure class (e.g. Weight, Volume, Ea)"),
        _f("UOMCode",                   _VC25,  req=True,  desc="Unique UOM code (e.g. KG, LB, EA)"),
        _f("UOMName",                   _VC80,  req=True,  desc="Display name of the unit of measure"),
        _f("Description",               _VC240, req=False),
        _f("BaseUOMFlag",               _BOOL,  req=False, desc="Y if this is the base UOM for its class"),
        _f("ConversionType",            _VC30,  req=False, desc="Standard / Fixed / Variable"),
        _f("ConversionRate",            _NUM,   req=False, desc="Conversion factor to base UOM"),
        _f("BaseUOMCode",               _VC25,  req=False, desc="Base UOM of the class for conversions"),
        _f("EffectiveStartDate",        _DATE,  req=False),
        _f("EffectiveEndDate",          _DATE,  req=False),
    ],

    # ── 2. Inventory Organization ─────────────────────────────────────────────
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

    # ── 3. Item Class / Catalog ───────────────────────────────────────────────
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

    # ── 4. Item Master (SCM Items) ────────────────────────────────────────────
    "itemmasterimport": [
        _f("OrganizationCode",               _VC30,  req=True,  desc="Inventory organization code"),
        _f("ItemNumber",                     _VC300, req=True,  desc="Unique item number / part number"),
        _f("Description",                    _VC240, req=True,  desc="Item description"),
        _f("PrimaryUOM",                     _VC25,  req=True,  desc="Primary unit of measure code"),
        _f("ItemClass",                      _VC60,  req=False, desc="Item class / catalog category"),
        _f("ItemType",                       _VC30,  req=False, desc="Standard, ATO, PTO, Kit, etc."),
        _f("InventoryItemFlag",              _BOOL,  req=False, desc="Y if stocked in inventory"),
        _f("PurchasedFlag",                  _BOOL,  req=False),
        _f("SalesOrderIssuesEnabledFlag",    _BOOL,  req=False),
        _f("BOMEnabledFlag",                 _BOOL,  req=False),
        _f("AssetItemFlag",                  _BOOL,  req=False),
        _f("EnabledFlag",                    _BOOL,  req=False),
        _f("ItemStatus",                     _VC30,  req=False, desc="Active / Inactive / Obsolete"),
        _f("ListPrice",                      _NUM,   req=False),
        _f("StdCost",                        _NUM,   req=False),
        _f("Weight",                         _NUM,   req=False),
        _f("WeightUOM",                      _VC25,  req=False),
        _f("Volume",                         _NUM,   req=False),
        _f("VolumeUOM",                      _VC25,  req=False),
        _f("LotControlCode",                 _VC30,  req=False),
        _f("SerialControlCode",              _VC30,  req=False),
        _f("LongDescription",                _VC2000,req=False),
    ],

    # ── 5. Customer Master ────────────────────────────────────────────────────
    "customerimport": [
        _f("PartyNumber",            _VC30,  req=False, desc="Unique party number (auto-assigned if blank)"),
        _f("PartyName",              _VC360, req=True,  desc="Customer / organisation name"),
        _f("PartyType",              _VC30,  req=True,  desc="ORGANIZATION or PERSON"),
        _f("CustomerAccountNumber",  _VC30,  req=False, desc="Unique account number"),
        _f("CustomerAccountName",    _VC360, req=True,  desc="Trading / account name"),
        _f("CustomerType",           _VC30,  req=False, desc="R (External) or I (Internal)"),
        _f("CustomerClass",          _VC30,  req=False, desc="Customer classification code"),
        _f("AccountStatus",          _VC30,  req=False, desc="Active / Inactive"),
        _f("CurrencyCode",           _VC15,  req=False, desc="Default transaction currency (ISO-4217)"),
        _f("PaymentTerms",           _VC30,  req=False),
        _f("CreditLimit",            _NUM,   req=False),
        _f("AddressLine1",           _VC240, req=False),
        _f("AddressLine2",           _VC240, req=False),
        _f("City",                   _VC60,  req=False),
        _f("State",                  _VC60,  req=False),
        _f("PostalCode",             _VC60,  req=False),
        _f("Country",                _VC10,  req=True,  desc="ISO-3166 two-letter country code"),
        _f("Phone",                  _VC40  if (_VC40 := "Character(40)") else _VC60, req=False),
        _f("Email",                  _VC300, req=False),
        _f("SiteUseCode",            _VC30,  req=False, desc="BILL_TO / SHIP_TO / DUNNING etc."),
    ],

    # ── 6. Supplier Master ────────────────────────────────────────────────────
    "supplierimport": [
        _f("SupplierName",              _VC360, req=True,  desc="Supplier / vendor legal name"),
        _f("SupplierNumber",            _VC30,  req=False, desc="Unique supplier number (auto if blank)"),
        _f("SupplierType",              _VC30,  req=False, desc="VENDOR / EMPLOYEE / OTHER"),
        _f("TaxRegistrationNumber",     _VC50,  req=False),
        _f("TaxOrganizationType",       _VC30,  req=False, desc="Corporation / Individual etc."),
        _f("ActiveFlag",                _BOOL,  req=False),
        _f("PaymentTerms",              _VC30,  req=False),
        _f("PaymentMethod",             _VC30,  req=False, desc="CHECK / EFT / WIRE etc."),
        _f("CurrencyCode",              _VC15,  req=False),
        _f("AddressLine1",              _VC240, req=False),
        _f("AddressLine2",              _VC240, req=False),
        _f("City",                      _VC60,  req=False),
        _f("State",                     _VC60,  req=False),
        _f("PostalCode",                _VC60,  req=False),
        _f("Country",                   _VC10,  req=False),
        _f("Phone",                     "Character(40)", req=False),
        _f("Email",                     _VC300, req=False),
        _f("BankAccountNumber",         _VC30,  req=False),
        _f("BankName",                  _VC60,  req=False),
        _f("RoutingNumber",             _VC30,  req=False),
    ],

    # ── 7. Bills of Material ──────────────────────────────────────────────────
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

    # ── 8. On-Hand Inventory Balances ─────────────────────────────────────────
    "onhandbalanceimport": [
        _f("ItemNumber",            _VC60, req=True,  desc="Inventory item number"),
        _f("OrganizationCode",      _VC30, req=True),
        _f("SubinventoryCode",      _VC30, req=True),
        _f("Locator",               _VC60, req=False, desc="Storage locator within subinventory"),
        _f("LotNumber",             _VC80, req=False),
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

    # ── 9. Open Sales Orders ──────────────────────────────────────────────────
    "salesorderimport": [
        _f("OrderNumber",           _VC60,  req=True,  desc="Source order number"),
        _f("OrderType",             _VC30,  req=True,  desc="Order transaction type (e.g. STANDARD)"),
        _f("OrderDate",             _DATE,  req=True,  desc="Order header date"),
        _f("OrderStatus",           _VC30,  req=False, desc="BOOKED / ENTERED"),
        _f("CustomerNumber",        _VC50,  req=True,  desc="Sold-to customer party number"),
        _f("CustomerName",          _VC240, req=False),
        _f("InventoryItemNumber",   _VC100, req=True,  desc="Item number being ordered"),
        _f("LineNumber",            _NUM,   req=True,  desc="Order line number"),
        _f("OrderedQuantity",       _NUM,   req=True),
        _f("UnitOfMeasureCode",     _VC25,  req=True,  desc="Order line UOM (e.g. Ea)"),
        _f("UnitSellingPrice",      _NUM,   req=False),
        _f("CurrencyCode",          _VC15,  req=True),
        _f("RequestShipDate",       _DATE,  req=False),
        _f("PromisedShipDate",      _DATE,  req=False),
        _f("ShipFromOrgCode",       _VC30,  req=False, desc="Source inventory organisation"),
        _f("PaymentTerms",          _VC30,  req=False),
        _f("FreightTerms",          _VC30,  req=False),
        _f("SourceSystem",          _VC30,  req=False, desc="Originating source system"),
    ],

    # ── 10. Open Purchase Orders ──────────────────────────────────────────────
    "purchaseorderimport": [
        _f("PONumber",              _VC25,  req=True,  desc="Purchase order number"),
        _f("POHeaderDescription",   _VC240, req=False),
        _f("Buyer",                 _VC240, req=False, desc="Buyer name or email"),
        _f("SupplierNumber",        _VC30,  req=True,  desc="Fusion supplier number"),
        _f("SupplierName",          _VC360, req=False),
        _f("SupplierSiteCode",      _VC30,  req=False),
        _f("CurrencyCode",          _VC15,  req=True),
        _f("PaymentTerms",          _VC30,  req=False),
        _f("ShipToOrganizationCode",_VC30,  req=True),
        _f("ShipToLocationCode",    _VC60,  req=False),
        _f("BillToLocationCode",    _VC60,  req=False),
        _f("LineNumber",            _NUM,   req=True,  desc="PO line number"),
        _f("LineType",              _VC30,  req=True,  desc="Goods / Services"),
        _f("ItemNumber",            _VC300, req=True,  desc="Item number being ordered"),
        _f("ItemDescription",       _VC240, req=False),
        _f("Quantity",              _NUM,   req=True),
        _f("UnitOfMeasure",         _VC25,  req=True),
        _f("UnitPrice",             _NUM,   req=True),
        _f("NeedByDate",            _DATE,  req=False),
        _f("PromisedDate",          _DATE,  req=False),
    ],

    # ── 11. Subinventories ────────────────────────────────────────────────────
    # Source: Oracle Fusion Cloud SCM FBDI — Inventory Subinventories
    # Table: INV_SUB_LEDGER_INTF / MTLS_SECONDARY_INVENTORIES staging
    "subinventoryimport": [
        _f("OrganizationCode",          _VC30,  req=True,  desc="Inventory organization short code"),
        _f("SubinventoryCode",          _VC10,  req=True,  desc="Unique subinventory code within the organization"),
        _f("Description",               _VC50,  req=False, desc="Description of the subinventory"),
        _f("QuantityTracking",          _VC30,  req=False, desc="Y = quantity tracked; N = not tracked"),
        _f("AssetSubinventory",         _VC30,  req=False, desc="Y = asset subinventory; N = expense"),
        _f("LocatorControl",            _VC30,  req=False, desc="No Control / Prespecified / Dynamic / Item Level"),
        _f("Reservable",                _VC30,  req=False, desc="Y = material can be reserved in this sub"),
        _f("Nettable",                  _VC30,  req=False, desc="Y = included in MRP net requirements"),
        _f("AvailabilityType",          _VC30,  req=False, desc="Nettable / Non-Nettable"),
        _f("PickingOrder",              _NUM,   req=False, desc="Picking sequence for warehouse management"),
        _f("PutawayOrder",              _NUM,   req=False, desc="Putaway sequence for warehouse management"),
        _f("DefaultCostGroupName",      _VC10,  req=False, desc="Default cost group for periodic average costing"),
        _f("StatusName",                _VC80,  req=False, desc="Inventory status that controls transactions"),
        _f("EffectiveStartDate",        _DATE,  req=False, desc="Date the subinventory becomes active"),
        _f("EffectiveEndDate",          _DATE,  req=False, desc="Date the subinventory becomes inactive"),
    ],

    # ── 12. Locators (Bin / Rack / Row) ───────────────────────────────────────
    # Source: Oracle Fusion Cloud SCM FBDI — Inventory Locators
    # Table: MTL_ITEM_LOCATIONS_INTERFACE staging
    "locatorimport": [
        _f("OrganizationCode",          _VC30,  req=True,  desc="Inventory organization short code"),
        _f("SubinventoryCode",          _VC10,  req=True,  desc="Subinventory the locator belongs to"),
        _f("LocatorSegments",           _VC60,  req=True,  desc="Concatenated locator flexfield segments (e.g. A.1.01)"),
        _f("Description",               _VC50,  req=False, desc="Description of the storage locator"),
        _f("PickingOrder",              _NUM,   req=False, desc="Sequence for pick wave processing"),
        _f("PutawayOrder",              _NUM,   req=False, desc="Sequence for putaway processing"),
        _f("MaximumUnits",              _NUM,   req=False, desc="Maximum inventory units this locator can hold"),
        _f("MaximumWeight",             _NUM,   req=False, desc="Maximum weight capacity"),
        _f("WeightUOMCode",             _VC25,  req=False, desc="Unit of measure for the weight capacity"),
        _f("MaximumVolume",             _NUM,   req=False, desc="Maximum volume capacity"),
        _f("VolumeUOMCode",             _VC25,  req=False, desc="Unit of measure for the volume capacity"),
        _f("StatusName",                _VC80,  req=False, desc="Inventory status controlling allowed transactions"),
        _f("SuggestedUOMCode",          _VC25,  req=False, desc="Suggested unit of measure for this locator"),
        _f("DimensionX",                _NUM,   req=False, desc="X dimension (length)"),
        _f("DimensionY",                _NUM,   req=False, desc="Y dimension (width)"),
        _f("DimensionZ",                _NUM,   req=False, desc="Z dimension (height)"),
    ],

    # -- 13. Customer Sites (Bill-to / Ship-to) ----------------------------
    # Source: Oracle Fusion Cloud ERP FBDI - TCA Customer Account Sites
    # Table: HZ_CUST_ACCT_SITES_INT staging
    "customersiteimport": [
        _f("CustomerAccountNumber",     _VC30,  req=True,  desc="Customer account number the site belongs to"),
        _f("SiteUseCode",               _VC30,  req=True,  desc="BILL_TO / SHIP_TO / DUNNING / STATEMENTS"),
        _f("PartySiteNumber",           _VC30,  req=False, desc="Party site number (auto-assigned if blank)"),
        _f("AddressLine1",              _VC240, req=True,  desc="First line of the site address"),
        _f("AddressLine2",              _VC240, req=False, desc="Second line of the site address"),
        _f("City",                      _VC60,  req=False),
        _f("State",                     _VC60,  req=False),
        _f("PostalCode",                _VC60,  req=False),
        _f("County",                    _VC60,  req=False),
        _f("Country",                   _VC10,  req=True,  desc="ISO-3166 two-letter country code"),
        _f("PrimaryFlag",               _VC30,  req=False, desc="Y = primary site for this use code"),
        _f("ActiveFlag",                _VC30,  req=False, desc="Y = active; N = inactive"),
        _f("PaymentTerms",              _VC30,  req=False, desc="Default payment terms for this site"),
        _f("PriceList",                 _VC240, req=False, desc="Default price list for this ship-to site"),
        _f("FreightTerms",              _VC30,  req=False, desc="Freight terms (e.g. FOB, CIF)"),
        _f("ShippingMethod",            _VC30,  req=False, desc="Default carrier / shipping method code"),
        _f("WarehouseCode",             _VC30,  req=False, desc="Default ship-from warehouse org code"),
        _f("DemandClass",               _VC30,  req=False, desc="Demand class for planning"),
        _f("TaxRegistrationNumber",     _VC60,  req=False, desc="VAT / tax registration number for this site"),
        _f("ContactName",               _VC360, req=False, desc="Primary contact name at this site"),
        _f("ContactPhone",              "Character(40)", req=False),
        _f("ContactEmail",              _VC300, req=False),
    ],

    # -- 14. Supplier Sites ------------------------------------------------
    # Source: Oracle Fusion Cloud ERP FBDI - AP Supplier Sites
    # Table: AP_SUPPLIER_SITES_INT staging
    "suppliersiteimport": [
        _f("SupplierNumber",            _VC30,  req=True,  desc="Supplier number the site belongs to"),
        _f("PurchasingOrganizationCode",_VC30,  req=True,  desc="Purchasing org that owns this site relationship"),
        _f("SiteCode",                  _VC15,  req=True,  desc="Unique site code within the supplier"),
        _f("AddressLine1",              _VC240, req=False, desc="First line of the supplier site address"),
        _f("AddressLine2",              _VC240, req=False),
        _f("City",                      _VC60,  req=False),
        _f("State",                     _VC60,  req=False),
        _f("PostalCode",                _VC60,  req=False),
        _f("Country",                   _VC10,  req=False, desc="ISO-3166 two-letter country code"),
        _f("Phone",                     _VC30,  req=False),
        _f("Fax",                       _VC30,  req=False),
        _f("EmailAddress",              _VC240, req=False),
        _f("PrimaryPaySite",            _VC30,  req=False, desc="Y = primary site for payments"),
        _f("PayDateBasis",              _VC30,  req=False, desc="Discount / Due / Invoice date basis for payment"),
        _f("PaymentCurrencyCode",       _VC15,  req=False, desc="Payment currency (ISO-4217)"),
        _f("PaymentTerms",              _VC30,  req=False),
        _f("PaymentMethod",             _VC30,  req=False, desc="CHECK / EFT / WIRE"),
        _f("HoldAllPayments",           _VC30,  req=False, desc="Y = place all invoices from this site on hold"),
        _f("IncomeReportingSite",       _VC30,  req=False, desc="Y = report 1099 income for US federal tax"),
        _f("BankAccountNumber",         _VC30,  req=False),
        _f("BankName",                  _VC60,  req=False),
        _f("BankBranchName",            _VC60,  req=False),
    ],

    # -- 15. Price Lists ---------------------------------------------------
    # Source: Oracle Fusion Cloud SCM FBDI - Order Management / Pricing Price Lists
    # Table: QP_PRICE_LISTS_INTERFACE / QP_PRICE_LIST_LINES_INTERFACE staging
    "pricelistimport": [
        _f("PriceListName",             _VC240, req=True,  desc="Unique name of the price list"),
        _f("Description",               _VC2000,req=False, desc="Description of the price list"),
        _f("CurrencyCode",              _VC15,  req=True,  desc="Price list currency (ISO-4217)"),
        _f("RoundTo",                   _NUM,   req=False, desc="Rounding precision (e.g. 2 = two decimal places)"),
        _f("StartDate",                 _DATE,  req=False, desc="Date the price list becomes active"),
        _f("EndDate",                   _DATE,  req=False, desc="Date the price list expires"),
        _f("FreightTermsCode",          _VC30,  req=False),
        _f("ShipmentMethodCode",        _VC30,  req=False),
        _f("DefaultPaymentTerms",       _VC30,  req=False, desc="Default payment terms"),
        _f("ItemNumber",                _VC300, req=True,  desc="Item number the price applies to"),
        _f("ItemDescription",           _VC240, req=False),
        _f("UOMCode",                   _VC25,  req=True,  desc="Unit of measure the price is expressed in"),
        _f("ListPrice",                 _NUM,   req=True,  desc="Unit price for the item on this list"),
        _f("MinimumQuantity",           _NUM,   req=False, desc="Minimum order quantity for this price to apply"),
        _f("MaximumQuantity",           _NUM,   req=False, desc="Maximum order quantity for this price to apply"),
        _f("LineStartDate",             _DATE,  req=False, desc="Effective start date for this price line"),
        _f("LineEndDate",               _DATE,  req=False, desc="Effective end date for this price line"),
        _f("PriceType",                 _VC30,  req=False, desc="List Price / Discount Percent / Surcharge Amount"),
        _f("OperandValue",              _NUM,   req=False, desc="Modifier value (used for discounts / surcharges)"),
    ],

    # -- 16. Lot Numbers ---------------------------------------------------
    # Source: Oracle Fusion Cloud SCM FBDI - Inventory Lot Numbers
    # Table: MTL_LOT_NUMBERS_INT staging
    "lotnumberimport": [
        _f("ItemNumber",                _VC300, req=True,  desc="Inventory item number the lot belongs to"),
        _f("OrganizationCode",          _VC30,  req=True,  desc="Inventory organization short code"),
        _f("LotNumber",                 _VC80,  req=True,  desc="Unique lot number within item + organization"),
        _f("ParentLotNumber",           _VC80,  req=False, desc="Parent lot for hierarchical lot tracking"),
        _f("GradeCode",                 _VC150, req=False, desc="Quality grade assigned to this lot"),
        _f("OriginationDate",           _DATE,  req=False, desc="Date the lot was created / manufactured"),
        _f("DateCode",                  _VC30,  req=False, desc="Date code stamped on the lot"),
        _f("ExpirationDate",            _DATE,  req=False, desc="Date the lot expires (for shelf-life items)"),
        _f("MandatoryExpirationFlag",   _VC30,  req=False, desc="Y = lot cannot be used after expiration date"),
        _f("BestByDate",                _DATE,  req=False, desc="Best-by date for perishable items"),
        _f("MaturityDate",              _DATE,  req=False, desc="Date the lot reaches maturity"),
        _f("RetestDate",                _DATE,  req=False, desc="Date the lot must be retested"),
        _f("StatusName",                _VC80,  req=False, desc="Lot status controlling allowed transactions"),
        _f("SupplierLotNumber",         _VC80,  req=False, desc="Lot number assigned by the supplier"),
        _f("Description",               _VC240, req=False, desc="Description or notes for the lot"),
        _f("Attribute1",                _VC150, req=False, desc="Descriptive flexfield segment 1"),
        _f("Attribute2",                _VC150, req=False, desc="Descriptive flexfield segment 2"),
    ],

    # -- 17. Serial Numbers ------------------------------------------------
    # Source: Oracle Fusion Cloud SCM FBDI - Inventory Serial Numbers
    # Table: MTL_SERIAL_NUMBERS_INT staging
    "serialnumberimport": [
        _f("ItemNumber",                _VC300, req=True,  desc="Inventory item number the serial belongs to"),
        _f("OrganizationCode",          _VC30,  req=True,  desc="Inventory organization short code"),
        _f("SerialNumber",              _VC30,  req=True,  desc="Unique serial number within item + organization"),
        _f("Revision",                  _VC30,  req=False, desc="Item revision code for revision-controlled items"),
        _f("LotNumber",                 _VC80,  req=False, desc="Lot number if item is also lot-controlled"),
        _f("InitializationDate",        _DATE,  req=False, desc="Date the serial number was first created"),
        _f("ExpirationDate",            _DATE,  req=False, desc="Date the serial number expires"),
        _f("StatusName",                _VC80,  req=False, desc="Serial status controlling allowed transactions"),
        _f("CurrentSubinventoryCode",   _VC10,  req=False, desc="Subinventory where the serial is currently stored"),
        _f("CurrentLocatorSegments",    _VC60,  req=False, desc="Locator segments where the serial is stored"),
        _f("VendorSerialNumber",        _VC30,  req=False, desc="Serial number assigned by the supplier / OEM"),
        _f("VendorLotNumber",           _VC80,  req=False, desc="Supplier lot number for this serial"),
        _f("Attribute1",                _VC150, req=False, desc="Descriptive flexfield segment 1"),
        _f("Attribute2",                _VC150, req=False, desc="Descriptive flexfield segment 2"),
    ],

    # -- 18. Sales Order Lines ---------------------------------------------
    # Source: Oracle Fusion Cloud SCM FBDI - Order Management Sales Order Lines
    # Table: DOO_LINES_INT / DOO_FULFILL_LINES_INT staging
    "salesorderlinesimport": [
        _f("SourceTransactionNumber",   _VC100, req=True,  desc="Source order number (matches header)"),
        _f("SourceTransactionLineId",   _VC100, req=True,  desc="Unique line identifier from the source system"),
        _f("SourceLineNumber",          _VC30,  req=False, desc="Human-readable line number from source"),
        _f("ItemNumber",                _VC300, req=True,  desc="Inventory item number being ordered"),
        _f("ItemDescription",           _VC240, req=False),
        _f("OrderedQuantity",           _NUM,   req=True,  desc="Quantity ordered on this line"),
        _f("UOMCode",                   _VC25,  req=True,  desc="Unit of measure for the ordered quantity"),
        _f("UnitListPrice",             _NUM,   req=False, desc="Catalog / list price per unit"),
        _f("UnitSellingPrice",          _NUM,   req=False, desc="Actual selling price after discounts"),
        _f("RequestShipDate",           _DATE,  req=False, desc="Customer requested ship date"),
        _f("RequestDeliveryDate",       _DATE,  req=False, desc="Customer requested delivery date"),
        _f("ScheduledShipDate",         _DATE,  req=False, desc="System-scheduled ship date"),
        _f("ShipToCustomerNumber",      _VC30,  req=False, desc="Ship-to party account number"),
        _f("ShipToAddressLine1",        _VC240, req=False),
        _f("ShipToCity",                _VC60,  req=False),
        _f("ShipToState",               _VC60,  req=False),
        _f("ShipToPostalCode",          _VC60,  req=False),
        _f("ShipToCountry",             _VC10,  req=False),
        _f("ShipFromOrgCode",           _VC30,  req=False, desc="Fulfillment inventory organization code"),
        _f("LineStatus",                _VC30,  req=False, desc="BOOKED / SHIPPED / AWAITING_SHIPPING"),
        _f("ReturnReasonCode",          _VC30,  req=False, desc="Return reason code (for RMA lines only)"),
        _f("ShippingMethod",            _VC30,  req=False, desc="Carrier / shipping method code"),
        _f("FreightTerms",              _VC30,  req=False),
    ],
}

# -- Aliases: common naming variants all resolve to canonical keys -----------
# Items
STANDARD_FIELDS["scpitemimport"]          = STANDARD_FIELDS["itemmasterimport"]
STANDARD_FIELDS["itemimport"]             = STANDARD_FIELDS["itemmasterimport"]
STANDARD_FIELDS["egpitemimport"]          = STANDARD_FIELDS["itemmasterimport"]
# UOM
STANDARD_FIELDS["uomclassimport"]         = STANDARD_FIELDS["uomimport"]
STANDARD_FIELDS["invuomimport"]           = STANDARD_FIELDS["uomimport"]
# Customer / Supplier
STANDARD_FIELDS["customermaster"]         = STANDARD_FIELDS["customerimport"]
STANDARD_FIELDS["arcustomerimport"]       = STANDARD_FIELDS["customerimport"]
STANDARD_FIELDS["suppliermaster"]         = STANDARD_FIELDS["supplierimport"]
STANDARD_FIELDS["vendorimport"]           = STANDARD_FIELDS["supplierimport"]
STANDARD_FIELDS["apsuppliersimport"]      = STANDARD_FIELDS["supplierimport"]
# Supplier Site
STANDARD_FIELDS["apsuppliersitesimport"]  = STANDARD_FIELDS["suppliersiteimport"]
# Customer Site
STANDARD_FIELDS["arcustomersiteimport"]   = STANDARD_FIELDS["customersiteimport"]
# Subinventory
STANDARD_FIELDS["inventorysubinventoryimport"] = STANDARD_FIELDS["subinventoryimport"]
STANDARD_FIELDS["invsubinventoryimport"]  = STANDARD_FIELDS["subinventoryimport"]
# Locator
STANDARD_FIELDS["invlocatorimport"]       = STANDARD_FIELDS["locatorimport"]
STANDARD_FIELDS["inventorylocatorimport"] = STANDARD_FIELDS["locatorimport"]
# Price List
STANDARD_FIELDS["pricebookimport"]        = STANDARD_FIELDS["pricelistimport"]
STANDARD_FIELDS["qppricelistimport"]      = STANDARD_FIELDS["pricelistimport"]
# Lot / Serial
STANDARD_FIELDS["lotserialimport"]        = STANDARD_FIELDS["lotnumberimport"]
STANDARD_FIELDS["invlotimport"]           = STANDARD_FIELDS["lotnumberimport"]
STANDARD_FIELDS["invserialimport"]        = STANDARD_FIELDS["serialnumberimport"]
# Sales Order Lines
STANDARD_FIELDS["omsalesorderlinesimport"]= STANDARD_FIELDS["salesorderlinesimport"]
# On-Hand
STANDARD_FIELDS["invonhandimport"]        = STANDARD_FIELDS["onhandbalanceimport"]
STANDARD_FIELDS["onhandimport"]           = STANDARD_FIELDS["onhandbalanceimport"]


# -- Shared utility ---------------------------------------------------------

def _schema_key_for(name: str, business_object: str | None = None) -> str | None:
    """Return the STANDARD_FIELDS key that best matches a template name / BO."""
    raw = name.lower().replace(" ", "").replace("_", "").replace("-", "")
    # Try longest prefix match first so "onhandbalanceimport" beats "import"
    match = next(
        (k for k in sorted(STANDARD_FIELDS, key=len, reverse=True)
         if k in raw or raw in k),
        None,
    )
    if match:
        return match
    if business_object:
        bo = business_object.lower().replace(" ", "")
        match = next(
            (k for k in sorted(STANDARD_FIELDS, key=len, reverse=True)
             if k in bo or bo in k),
            None,
        )
    return match


async def auto_seed_if_empty(tpl: FBDITemplate) -> int:
    """Seed standard fields for *tpl* if it has 0 FBDIField records.

    Returns the number of fields seeded (0 if already populated or no schema
    found).  Safe to call multiple times -- is a no-op when fields exist.
    """
    existing = await FBDIField.find(FBDIField.template_id == tpl.id).count()
    if existing > 0:
        return 0

    key = _schema_key_for(tpl.name, tpl.business_object)
    if key is None:
        logger.warning(f"auto_seed_if_empty: no schema for '{tpl.name}' -- skipped")
        return 0

    sheet = await FBDISheet.find_one(FBDISheet.template_id == tpl.id)
    if sheet is None:
        sheet = FBDISheet(
            template_id=tpl.id,
            sheet_name="Import",
            sequence=0,
            field_count=0,
        )
        await sheet.insert()

    field_defs = STANDARD_FIELDS[key]
    for seq, fdef in enumerate(field_defs, start=1):
        await FBDIField(
            template_id=tpl.id,
            sheet_id=sheet.id,
            sequence=seq,
            **fdef,
        ).insert()

    await sheet.set({"field_count": len(field_defs)})
    await tpl.set({"status": "parsed"})
    logger.info(
        f"auto_seed_if_empty: seeded {len(field_defs)} fields "
        f"(schema='{key}') for template '{tpl.name}'"
    )
    return len(field_defs)


# -- REST endpoint (manual trigger from UI) ---------------------------------

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
        return {
            "seeded": 0,
            "existing": existing_count,
            "message": "Template already has fields -- skipped",
        }

    key = _schema_key_for(tpl.name, tpl.business_object)
    if key is None:
        raise HTTPException(
            422,
            f"No standard schema found for template '{tpl.name}'. "
            f"Known schemas: {list(STANDARD_FIELDS.keys())}",
        )

    seeded = await auto_seed_if_empty(tpl)
    return {
        "seeded": seeded,
        "existing": 0,
        "schema_matched": key,
        "message": f"Seeded {seeded} standard Oracle Fusion fields for '{tpl.name}'",
    }
