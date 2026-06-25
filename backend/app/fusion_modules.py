"""Fusion Cloud module catalog — exhaustive edition.

Drives the Setup Wizard "Implementation Scope" step. Each module declares
the canonical Fusion target objects a migration team typically loads, plus
the EBS source-extract hints and realistic mock row-counts.

Load order convention
---------------------
  1-49   Reference / master data (UOM, COA, legal entities, categories)
  50-69  Organizational setup (orgs, BUs, locations, work centres)
  70-89  Party / item master (items, customers, suppliers, employees)
  90-109 Transactional open balances (invoices, orders, on-hand)
  110+   Historical / reporting data (journals, actuals, histories)

Modules
-------
  financials    GL · AP · AR · Cash Management · Fixed Assets · Expenses
  tax           Oracle Fusion Tax (ZX)
  scm           Inventory · Order Management · Lot/Serial tracking
  procurement   Purchasing · Sourcing · Supplier Management
  manufacturing Shop Floor · BOM · Routings · WIP · Quality
  planning      Supply Chain Planning · Demand Management · S&OP
  hcm           Core HR · Payroll · Benefits · Absence · Talent
  ppm           Projects · Billing · Costing · Grants
  epm           Planning · Budgeting · Consolidation · Reconciliation
  maintenance   Asset Maintenance / EAM
  risk          GRC Controls · Risk Library
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class FusionObject:
    """One target object the implementation migrates to a Fusion module."""
    target_object: str
    label: str
    fbdi_template: str | None = None
    planned_load_order: int = 100
    source_extracts: dict[str, str] = field(default_factory=dict)
    mock_row_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class FusionModule:
    code: str
    name: str
    family: str
    description: str
    objects: tuple[FusionObject, ...]


# =============================================================================
# FINANCIALS  (GL · AP · AR · Cash Management · Fixed Assets · Expenses)
# =============================================================================

_FINANCIALS_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Chart of Accounts", "Chart of Accounts (Coding Combinations)",
        fbdi_template="GL Account Codes (FBDI)", planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from GL_CODE_COMBINATIONS",
            "netsuite":   "Saved Search -> Chart of Accounts export",
            "sap_ecc":    "FS00 / GL account master download",
        },
        mock_row_counts={"oracle_ebs": 4_200, "netsuite": 1_850, "sap_ecc": 3_100},
    ),
    FusionObject(
        "Legal Entity", "Legal Entities",
        fbdi_template="Legal Entity (FBDI)", planned_load_order=15,
        source_extracts={
            "oracle_ebs": "Extract from XLE_ENTITY_PROFILES",
            "netsuite":   "Setup -> Subsidiaries CSV export",
            "sap_ecc":    "Company code list (OX02)",
        },
        mock_row_counts={"oracle_ebs": 8, "netsuite": 6, "sap_ecc": 12},
    ),
    FusionObject(
        "Ledger", "Ledgers",
        fbdi_template="Ledger (FBDI)", planned_load_order=20,
        source_extracts={
            "oracle_ebs": "Extract from GL_LEDGERS",
            "netsuite":   "Accounting Books listing",
            "sap_ecc":    "Controlling area / ledger config",
        },
        mock_row_counts={"oracle_ebs": 4, "netsuite": 3, "sap_ecc": 6},
    ),
    FusionObject(
        "Business Unit", "Business Units",
        planned_load_order=25,
        source_extracts={
            "oracle_ebs": "Extract from HR_OPERATING_UNITS",
            "netsuite":   "Subsidiary -> BU mapping export",
            "sap_ecc":    "Profit center / business area download",
        },
        mock_row_counts={"oracle_ebs": 12, "netsuite": 8, "sap_ecc": 18},
    ),
    FusionObject(
        "Bank", "Banks",
        fbdi_template="Cash Management Banks (FBDI)", planned_load_order=30,
        source_extracts={
            "oracle_ebs": "Extract from CE_BANKS_V",
            "netsuite":   "Banking -> Banks list export",
            "sap_ecc":    "Bank master (FI12)",
        },
        mock_row_counts={"oracle_ebs": 14, "netsuite": 8, "sap_ecc": 20},
    ),
    FusionObject(
        "Bank Branch", "Bank Branches",
        fbdi_template="Cash Management Bank Branches (FBDI)", planned_load_order=32,
        source_extracts={
            "oracle_ebs": "Extract from CE_BANK_BRANCHES_V",
            "netsuite":   "Banking -> Branch list",
            "sap_ecc":    "Bank master branches (FI03)",
        },
        mock_row_counts={"oracle_ebs": 28, "netsuite": 12, "sap_ecc": 35},
    ),
    FusionObject(
        "Bank Account", "Bank Accounts",
        fbdi_template="Cash Management Bank Accounts (FBDI)", planned_load_order=35,
        source_extracts={
            "oracle_ebs": "Extract from CE_BANK_ACCOUNTS",
            "netsuite":   "Setup -> Bank Accounts export",
            "sap_ecc":    "House bank accounts (FI12)",
        },
        mock_row_counts={"oracle_ebs": 24, "netsuite": 18, "sap_ecc": 30},
    ),
    FusionObject(
        "Payment Terms", "Payment Terms",
        fbdi_template="Payment Terms (FBDI)", planned_load_order=40,
        source_extracts={
            "oracle_ebs": "Extract from AP_TERMS",
            "netsuite":   "Lists -> Payment Terms",
            "sap_ecc":    "Payment terms (OBB8)",
        },
        mock_row_counts={"oracle_ebs": 85, "netsuite": 42, "sap_ecc": 60},
    ),
    FusionObject(
        "Asset Book", "Asset Books",
        fbdi_template="Asset Books (FBDI)", planned_load_order=44,
        source_extracts={
            "oracle_ebs": "Extract from FA_BOOK_CONTROLS",
            "netsuite":   "Fixed Asset Books setup",
            "sap_ecc":    "Depreciation areas (OADB)",
        },
        mock_row_counts={"oracle_ebs": 6, "netsuite": 4, "sap_ecc": 8},
    ),
    FusionObject(
        "Asset Category", "Asset Categories",
        fbdi_template="Asset Categories (FBDI)", planned_load_order=46,
        source_extracts={
            "oracle_ebs": "Extract from FA_CATEGORIES",
            "netsuite":   "Asset category list",
            "sap_ecc":    "Asset class (AS08)",
        },
        mock_row_counts={"oracle_ebs": 180, "netsuite": 60, "sap_ecc": 120},
    ),
    FusionObject(
        "Fixed Asset", "Fixed Assets",
        fbdi_template="Asset Mass Additions (FBDI)", planned_load_order=90,
        source_extracts={
            "oracle_ebs": "Extract from FA_BOOKS / FA_ASSET_HISTORY",
            "netsuite":   "Fixed Asset Management module export",
            "sap_ecc":    "Asset master + values (AS05 / AR01)",
        },
        mock_row_counts={"oracle_ebs": 4_300, "netsuite": 1_200, "sap_ecc": 3_800},
    ),
    FusionObject(
        "Asset Depreciation", "Asset Depreciation History",
        fbdi_template="Asset Mass Additions (FBDI)", planned_load_order=92,
        source_extracts={
            "oracle_ebs": "Extract from FA_DEPRN_SUMMARY",
            "netsuite":   "Depreciation Schedule report",
            "sap_ecc":    "Asset history sheet (AR02)",
        },
        mock_row_counts={"oracle_ebs": 18_600, "netsuite": 4_800, "sap_ecc": 15_200},
    ),
    FusionObject(
        "Open AP Invoice", "Open AP Invoices",
        fbdi_template="Payables Invoices Import (FBDI)", planned_load_order=100,
        source_extracts={
            "oracle_ebs": "Extract from AP_INVOICES_ALL (status=Open)",
            "netsuite":   "Saved Search -> Open Vendor Bills",
            "sap_ecc":    "Open vendor line items (FBL1N)",
        },
        mock_row_counts={"oracle_ebs": 42_000, "netsuite": 8_400, "sap_ecc": 31_000},
    ),
    FusionObject(
        "AP Credit Memo", "AP Credit Memos",
        fbdi_template="Payables Invoices Import (FBDI)", planned_load_order=102,
        source_extracts={
            "oracle_ebs": "Extract from AP_INVOICES_ALL",
            "netsuite":   "Saved Search -> Open Vendor Credits",
            "sap_ecc":    "Open credit notes (FBL1N)",
        },
        mock_row_counts={"oracle_ebs": 3_200, "netsuite": 640, "sap_ecc": 2_100},
    ),
    FusionObject(
        "AP Prepayment", "AP Prepayments",
        fbdi_template="Payables Invoices Import (FBDI)", planned_load_order=104,
        source_extracts={
            "oracle_ebs": "Extract from AP_INVOICES_ALL",
            "netsuite":   "Saved Search -> Vendor Prepayments",
            "sap_ecc":    "Down payments (FBL1N)",
        },
        mock_row_counts={"oracle_ebs": 820, "netsuite": 180, "sap_ecc": 560},
    ),
    FusionObject(
        "Open AR Invoice", "Open AR Invoices",
        fbdi_template="Receivables AutoInvoice (FBDI)", planned_load_order=106,
        source_extracts={
            "oracle_ebs": "Extract from AR_PAYMENT_SCHEDULES_ALL",
            "netsuite":   "Saved Search -> Open Customer Invoices",
            "sap_ecc":    "Open customer line items (FBL5N)",
        },
        mock_row_counts={"oracle_ebs": 28_000, "netsuite": 6_200, "sap_ecc": 21_000},
    ),
    FusionObject(
        "AR Credit Memo", "AR Credit Memos",
        fbdi_template="Receivables AutoInvoice (FBDI)", planned_load_order=108,
        source_extracts={
            "oracle_ebs": "Extract from RA_CUSTOMER_TRX_ALL",
            "netsuite":   "Saved Search -> Open Customer Credits",
            "sap_ecc":    "Open credit memos (FBL5N)",
        },
        mock_row_counts={"oracle_ebs": 2_400, "netsuite": 480, "sap_ecc": 1_800},
    ),
    FusionObject(
        "Open Receipt", "Unapplied / Unmatched Receipts",
        fbdi_template="Receivables Receipts (FBDI)", planned_load_order=110,
        source_extracts={
            "oracle_ebs": "Extract from AR_CASH_RECEIPTS_ALL",
            "netsuite":   "Saved Search -> Unapplied Receipts",
            "sap_ecc":    "Open items in clearing (FBL3N)",
        },
        mock_row_counts={"oracle_ebs": 4_800, "netsuite": 920, "sap_ecc": 3_400},
    ),
    FusionObject(
        "GL Opening Balance", "GL Opening Balances",
        fbdi_template="GL Journal Import (FBDI)", planned_load_order=112,
        source_extracts={
            "oracle_ebs": "Extract from GL_BALANCES",
            "netsuite":   "Trial Balance at cutover date",
            "sap_ecc":    "Balance carry-forward report (F.16)",
        },
        mock_row_counts={"oracle_ebs": 38_000, "netsuite": 9_200, "sap_ecc": 28_000},
    ),
    FusionObject(
        "Open GL Journal", "Open / Unposted GL Journals",
        fbdi_template="GL Journal Import (FBDI)", planned_load_order=114,
        source_extracts={
            "oracle_ebs": "Extract from GL_JE_HEADERS / GL_JE_LINES (unposted)",
            "netsuite":   "Saved Search -> Unposted Journal Entries",
            "sap_ecc":    "Parked / unposted documents (FBV3)",
        },
        mock_row_counts={"oracle_ebs": 142_000, "netsuite": 31_000, "sap_ecc": 98_000},
    ),
    FusionObject(
        "Expense Report", "Open Expense Reports",
        fbdi_template="Expenses Import (FBDI)", planned_load_order=116,
        source_extracts={
            "oracle_ebs": "Extract from AP_EXPENSE_REPORT_HEADERS_ALL",
            "netsuite":   "Saved Search -> Pending Expense Reports",
            "sap_ecc":    "Travel expense reports (PR05)",
        },
        mock_row_counts={"oracle_ebs": 1_840, "netsuite": 420, "sap_ecc": 1_200},
    ),
)


# =============================================================================
# TAX  (Oracle Fusion Tax — ZX module)
# =============================================================================

_TAX_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Tax Regime", "Tax Regimes",
        fbdi_template="Tax Regime Import (FBDI)", planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from ZX_REGIMES_B",
            "netsuite":   "Tax -> Tax Nexus list",
            "sap_ecc":    "Tax procedure / country assignment (OBBG)",
        },
        mock_row_counts={"oracle_ebs": 12, "netsuite": 8, "sap_ecc": 15},
    ),
    FusionObject(
        "Tax", "Taxes",
        fbdi_template="Tax Import (FBDI)", planned_load_order=15,
        source_extracts={
            "oracle_ebs": "Extract from ZX_TAXES_B",
            "netsuite":   "Tax -> Tax Codes list",
            "sap_ecc":    "Tax codes (FTXP)",
        },
        mock_row_counts={"oracle_ebs": 48, "netsuite": 32, "sap_ecc": 60},
    ),
    FusionObject(
        "Tax Rate", "Tax Rates",
        fbdi_template="Tax Rates Import (FBDI)", planned_load_order=20,
        source_extracts={
            "oracle_ebs": "Extract from ZX_RATES_B",
            "netsuite":   "Tax -> Tax Rates list",
            "sap_ecc":    "Tax rates (FTXP)",
        },
        mock_row_counts={"oracle_ebs": 120, "netsuite": 85, "sap_ecc": 140},
    ),
    FusionObject(
        "Tax Exemption", "Customer Tax Exemptions",
        fbdi_template="Tax Exemptions (FBDI)", planned_load_order=30,
        source_extracts={
            "oracle_ebs": "Extract from ZX_EXEMPTIONS",
            "netsuite":   "Tax -> Customer Tax Exemptions",
            "sap_ecc":    "Tax classification per customer",
        },
        mock_row_counts={"oracle_ebs": 640, "netsuite": 280, "sap_ecc": 420},
    ),
    FusionObject(
        "Tax Registration", "Supplier / Party Tax Registrations",
        fbdi_template="Tax Registrations (FBDI)", planned_load_order=35,
        source_extracts={
            "oracle_ebs": "Extract from ZX_REGISTRATIONS",
            "netsuite":   "Tax -> Supplier Tax IDs",
            "sap_ecc":    "VAT registration numbers (XK03)",
        },
        mock_row_counts={"oracle_ebs": 480, "netsuite": 220, "sap_ecc": 380},
    ),
)


# =============================================================================
# SUPPLY CHAIN MANAGEMENT  (Inventory · Order Management · Lot/Serial)
# =============================================================================

_SCM_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "UOM Class", "Units of Measure Classes",
        fbdi_template="UOM Class Import (FBDI)", planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from MTL_UOM_CLASSES",
            "netsuite":   "Setup -> UOM Groups",
            "sap_ecc":    "Unit of measure groups (T006A)",
        },
        mock_row_counts={"oracle_ebs": 28, "netsuite": 12, "sap_ecc": 20},
    ),
    FusionObject(
        "UOM", "Units of Measure",
        fbdi_template="UOM Import (FBDI)", planned_load_order=12,
        source_extracts={
            "oracle_ebs": "Extract from MTL_UNITS_OF_MEASURE",
            "netsuite":   "Setup -> Units of Measure export",
            "sap_ecc":    "Units of measure (T006)",
        },
        mock_row_counts={"oracle_ebs": 847, "netsuite": 42, "sap_ecc": 320},
    ),
    FusionObject(
        "Inventory Org", "Inventory Organizations",
        fbdi_template="Inventory Org Import (FBDI)", planned_load_order=50,
        source_extracts={
            "oracle_ebs": "Extract from MTL_PARAMETERS",
            "netsuite":   "Locations / Warehouses export",
            "sap_ecc":    "Plant / storage location (OX10 / OX09)",
        },
        mock_row_counts={"oracle_ebs": 42, "netsuite": 18, "sap_ecc": 35},
    ),
    FusionObject(
        "Subinventory", "Subinventories (Storage Locations)",
        fbdi_template="Subinventory Import (FBDI)", planned_load_order=52,
        source_extracts={
            "oracle_ebs": "Extract from MTL_SECONDARY_INVENTORIES",
            "netsuite":   "Warehouse -> Bin list",
            "sap_ecc":    "Storage locations (OX09)",
        },
        mock_row_counts={"oracle_ebs": 280, "netsuite": 85, "sap_ecc": 210},
    ),
    FusionObject(
        "Locator", "Locators (Bin / Rack / Row)",
        fbdi_template="Locator Import (FBDI)", planned_load_order=54,
        source_extracts={
            "oracle_ebs": "Extract from MTL_ITEM_LOCATIONS",
            "netsuite":   "Warehouse -> Bin Locations",
            "sap_ecc":    "WM storage bins (LS24)",
        },
        mock_row_counts={"oracle_ebs": 3_400, "netsuite": 960, "sap_ecc": 2_800},
    ),
    FusionObject(
        "Item Class", "Item Classes (Catalog Groups)",
        fbdi_template="Item Catalog Import (FBDI)", planned_load_order=60,
        source_extracts={
            "oracle_ebs": "Extract from MTL_CATEGORIES_B",
            "netsuite":   "Item Categories export",
            "sap_ecc":    "Material groups (T023)",
        },
        mock_row_counts={"oracle_ebs": 384, "netsuite": 120, "sap_ecc": 280},
    ),
    FusionObject(
        "Item", "Item Master",
        fbdi_template="Item Master (SCM Items)", planned_load_order=70,
        source_extracts={
            "oracle_ebs": "Extract from MTL_SYSTEM_ITEMS_B",
            "netsuite":   "Saved Search -> All Active Items",
            "sap_ecc":    "Material master (MM60 / MM17)",
        },
        mock_row_counts={"oracle_ebs": 8_500, "netsuite": 3_200, "sap_ecc": 12_400},
    ),
    FusionObject(
        "Customer", "Customer Master",
        fbdi_template="Customer Import (FBDI)", planned_load_order=74,
        source_extracts={
            "oracle_ebs": "Extract from HZ_PARTIES (party_type=Customer)",
            "netsuite":   "Saved Search -> All Active Customers",
            "sap_ecc":    "Customer master (XD03)",
        },
        mock_row_counts={"oracle_ebs": 5_600, "netsuite": 2_100, "sap_ecc": 7_200},
    ),
    FusionObject(
        "Customer Site", "Customer Sites (Bill-to / Ship-to)",
        fbdi_template="Customer Site Import (FBDI)", planned_load_order=76,
        source_extracts={
            "oracle_ebs": "Extract from HZ_CUST_ACCT_SITES_ALL",
            "netsuite":   "Customer -> Addresses export",
            "sap_ecc":    "Customer delivery addresses (XD03)",
        },
        mock_row_counts={"oracle_ebs": 12_400, "netsuite": 4_800, "sap_ecc": 16_000},
    ),
    FusionObject(
        "Supplier", "Supplier Master",
        fbdi_template="Suppliers Import (FBDI)", planned_load_order=78,
        source_extracts={
            "oracle_ebs": "Extract from HZ_PARTIES (party_type=Supplier)",
            "netsuite":   "Saved Search -> All Active Vendors",
            "sap_ecc":    "Vendor master (XK03)",
        },
        mock_row_counts={"oracle_ebs": 3_200, "netsuite": 980, "sap_ecc": 4_800},
    ),
    FusionObject(
        "Supplier Site", "Supplier Sites",
        fbdi_template="Supplier Sites Import (FBDI)", planned_load_order=80,
        source_extracts={
            "oracle_ebs": "Extract from AP_SUPPLIER_SITES_ALL",
            "netsuite":   "Vendor -> Addresses export",
            "sap_ecc":    "Vendor purchase org data (XK03)",
        },
        mock_row_counts={"oracle_ebs": 7_400, "netsuite": 2_200, "sap_ecc": 10_800},
    ),
    FusionObject(
        "Price List", "Price Lists",
        fbdi_template="Price List Import (FBDI)", planned_load_order=82,
        source_extracts={
            "oracle_ebs": "Extract from QP_LIST_HEADERS_B",
            "netsuite":   "Pricing -> Price Levels export",
            "sap_ecc":    "Condition records (VK13)",
        },
        mock_row_counts={"oracle_ebs": 48, "netsuite": 22, "sap_ecc": 120},
    ),
    FusionObject(
        "On-Hand Balance", "On-Hand Inventory Balances",
        fbdi_template="On-Hand Balance Import (FBDI)", planned_load_order=100,
        source_extracts={
            "oracle_ebs": "Extract from MTL_ONHAND_QUANTITIES_DETAIL",
            "netsuite":   "Saved Search -> Inventory on Hand by Location",
            "sap_ecc":    "Stock overview (MMBE / MB52)",
        },
        mock_row_counts={"oracle_ebs": 2_400, "netsuite": 1_100, "sap_ecc": 3_800},
    ),
    FusionObject(
        "Lot Number", "Lot Numbers",
        fbdi_template="Lot Number Import (FBDI)", planned_load_order=102,
        source_extracts={
            "oracle_ebs": "Extract from MTL_LOT_NUMBERS",
            "netsuite":   "Inventory -> Lot Numbers",
            "sap_ecc":    "Batch master (MB56)",
        },
        mock_row_counts={"oracle_ebs": 18_400, "netsuite": 6_200, "sap_ecc": 24_000},
    ),
    FusionObject(
        "Serial Number", "Serial Numbers",
        fbdi_template="Serial Number Import (FBDI)", planned_load_order=104,
        source_extracts={
            "oracle_ebs": "Extract from MTL_SERIAL_NUMBERS",
            "netsuite":   "Inventory -> Serial Numbers",
            "sap_ecc":    "Serial number master (MB58)",
        },
        mock_row_counts={"oracle_ebs": 42_000, "netsuite": 15_000, "sap_ecc": 58_000},
    ),
    FusionObject(
        "Sales Order", "Open Sales Orders",
        fbdi_template="Sales Order Import (FBDI)", planned_load_order=110,
        source_extracts={
            "oracle_ebs": "Extract from OE_ORDER_HEADERS_ALL (status=Open)",
            "netsuite":   "Saved Search -> Open Sales Orders",
            "sap_ecc":    "Open sales orders (VA05N)",
        },
        mock_row_counts={"oracle_ebs": 12_400, "netsuite": 4_800, "sap_ecc": 18_000},
    ),
    FusionObject(
        "Sales Order Line", "Open Sales Order Lines",
        fbdi_template="Sales Order Import (FBDI)", planned_load_order=112,
        source_extracts={
            "oracle_ebs": "Extract from OE_ORDER_LINES_ALL",
            "netsuite":   "Saved Search -> Open Sales Order Lines",
            "sap_ecc":    "Sales order line items (VA05N)",
        },
        mock_row_counts={"oracle_ebs": 38_000, "netsuite": 14_400, "sap_ecc": 52_000},
    ),
)


# =============================================================================
# PROCUREMENT  (Purchasing · Sourcing · Supplier Management)
# =============================================================================

_PROCUREMENT_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Purchasing Category", "Purchasing Categories",
        fbdi_template="Purchasing Categories (FBDI)", planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from MTL_CATEGORIES_B",
            "netsuite":   "Purchasing -> Categories list",
            "sap_ecc":    "Material groups (T023)",
        },
        mock_row_counts={"oracle_ebs": 280, "netsuite": 85, "sap_ecc": 220},
    ),
    FusionObject(
        "Approved Supplier List", "Approved Supplier Lists (ASL)",
        fbdi_template="ASL Import (FBDI)", planned_load_order=30,
        source_extracts={
            "oracle_ebs": "Extract from PO_APPROVED_SUPPLIER_LIST",
            "netsuite":   "Preferred Vendor list",
            "sap_ecc":    "Source list (ME03)",
        },
        mock_row_counts={"oracle_ebs": 2_400, "netsuite": 680, "sap_ecc": 3_200},
    ),
    FusionObject(
        "Blanket Purchase Agreement", "Blanket Purchase Agreements (BPA)",
        fbdi_template="Purchase Orders Import (FBDI)", planned_load_order=40,
        source_extracts={
            "oracle_ebs": "Extract from PO_HEADERS_ALL",
            "netsuite":   "Purchasing -> Blanket POs",
            "sap_ecc":    "Outline agreements (ME3N)",
        },
        mock_row_counts={"oracle_ebs": 840, "netsuite": 240, "sap_ecc": 1_200},
    ),
    FusionObject(
        "Contract Purchase Agreement", "Contract Purchase Agreements",
        fbdi_template="Purchase Orders Import (FBDI)", planned_load_order=42,
        source_extracts={
            "oracle_ebs": "Extract from PO_HEADERS_ALL",
            "netsuite":   "Purchasing -> Purchase Contracts",
            "sap_ecc":    "Contracts (ME33K)",
        },
        mock_row_counts={"oracle_ebs": 320, "netsuite": 80, "sap_ecc": 480},
    ),
    FusionObject(
        "Purchase Order", "Open Purchase Orders",
        fbdi_template="Purchase Orders Import (FBDI)", planned_load_order=100,
        source_extracts={
            "oracle_ebs": "Extract from PO_HEADERS_ALL (status=Open)",
            "netsuite":   "Saved Search -> Open Purchase Orders",
            "sap_ecc":    "Open purchase orders (ME2N)",
        },
        mock_row_counts={"oracle_ebs": 9_800, "netsuite": 2_600, "sap_ecc": 14_000},
    ),
    FusionObject(
        "Purchase Requisition", "Open Purchase Requisitions",
        fbdi_template="Purchase Requisition Import (FBDI)", planned_load_order=102,
        source_extracts={
            "oracle_ebs": "Extract from PO_REQUISITION_HEADERS_ALL",
            "netsuite":   "Saved Search -> Open Purchase Requests",
            "sap_ecc":    "Open requisitions (ME5A)",
        },
        mock_row_counts={"oracle_ebs": 4_200, "netsuite": 1_100, "sap_ecc": 6_400},
    ),
    FusionObject(
        "Receipt", "Open Receiving Receipts",
        fbdi_template="Receiving Transactions (FBDI)", planned_load_order=104,
        source_extracts={
            "oracle_ebs": "Extract from RCV_SHIPMENT_HEADERS",
            "netsuite":   "Purchasing -> Open Receipts",
            "sap_ecc":    "Goods receipts to be invoiced (MB5S)",
        },
        mock_row_counts={"oracle_ebs": 6_800, "netsuite": 1_800, "sap_ecc": 9_200},
    ),
)


# =============================================================================
# MANUFACTURING  (Shop Floor · BOM · Routings · WIP · Quality)
# =============================================================================

_MANUFACTURING_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Work Center", "Work Centers",
        fbdi_template="Work Center Import (FBDI)", planned_load_order=50,
        source_extracts={
            "oracle_ebs": "Extract from BOM_DEPARTMENTS",
            "netsuite":   "Manufacturing -> Work Centers",
            "sap_ecc":    "Work centers (CR03)",
        },
        mock_row_counts={"oracle_ebs": 120, "netsuite": 48, "sap_ecc": 180},
    ),
    FusionObject(
        "Resource", "Resources (Machines / Labor)",
        fbdi_template="Resource Import (FBDI)", planned_load_order=52,
        source_extracts={
            "oracle_ebs": "Extract from BOM_RESOURCES",
            "netsuite":   "Manufacturing -> Resources",
            "sap_ecc":    "Capacities / resources (CR03)",
        },
        mock_row_counts={"oracle_ebs": 380, "netsuite": 120, "sap_ecc": 540},
    ),
    FusionObject(
        "BOM", "Bills of Material",
        fbdi_template="BOM Import (FBDI)", planned_load_order=70,
        source_extracts={
            "oracle_ebs": "Extract from BOM_BILL_OF_MATERIALS / BOM_COMPONENTS",
            "netsuite":   "Manufacturing -> BOM CSV export",
            "sap_ecc":    "Bills of material (CS11)",
        },
        mock_row_counts={"oracle_ebs": 1_200, "netsuite": 480, "sap_ecc": 1_800},
    ),
    FusionObject(
        "Routing", "Production Routings",
        fbdi_template="Routing Import (FBDI)", planned_load_order=72,
        source_extracts={
            "oracle_ebs": "Extract from BOM_OPERATIONAL_ROUTINGS",
            "netsuite":   "Manufacturing -> Routings",
            "sap_ecc":    "Task list / routings (CA03)",
        },
        mock_row_counts={"oracle_ebs": 840, "netsuite": 320, "sap_ecc": 1_200},
    ),
    FusionObject(
        "Work Order", "Open Work Orders (WIP Jobs)",
        fbdi_template="Work Order Import (FBDI)", planned_load_order=100,
        source_extracts={
            "oracle_ebs": "Extract from WIP_DISCRETE_JOBS",
            "netsuite":   "Manufacturing -> Open Production Orders",
            "sap_ecc":    "Open production orders (COOIS)",
        },
        mock_row_counts={"oracle_ebs": 3_200, "netsuite": 840, "sap_ecc": 4_800},
    ),
    FusionObject(
        "Work Order Component", "WIP Component Requirements",
        fbdi_template="Work Order Component (FBDI)", planned_load_order=102,
        source_extracts={
            "oracle_ebs": "Extract from WIP_REQUIREMENT_OPERATIONS",
            "netsuite":   "Manufacturing -> Component Requirements",
            "sap_ecc":    "Production order components (CS15)",
        },
        mock_row_counts={"oracle_ebs": 12_400, "netsuite": 3_200, "sap_ecc": 18_000},
    ),
    FusionObject(
        "Quality Inspection Plan", "Quality Inspection Plans",
        fbdi_template="Quality Plan Import (FBDI)", planned_load_order=60,
        source_extracts={
            "oracle_ebs": "Extract from QA_PLANS",
            "netsuite":   "Quality -> Inspection Plans",
            "sap_ecc":    "Inspection plans (QP03)",
        },
        mock_row_counts={"oracle_ebs": 480, "netsuite": 120, "sap_ecc": 640},
    ),
)


# =============================================================================
# PLANNING  (Supply Chain Planning · Demand Management · S&OP)
# =============================================================================

_PLANNING_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Item Planning Parameter", "Item Planning Parameters (Policies)",
        fbdi_template="Planning Parameters (FBDI)", planned_load_order=70,
        source_extracts={
            "oracle_ebs": "Extract from MTL_SYSTEM_ITEMS_B",
            "netsuite":   "Item -> Reorder Point settings",
            "sap_ecc":    "MRP data (MM03 MRP views)",
        },
        mock_row_counts={"oracle_ebs": 8_200, "netsuite": 3_000, "sap_ecc": 11_800},
    ),
    FusionObject(
        "Safety Stock", "Safety Stock Levels",
        fbdi_template="Safety Stock Import (FBDI)", planned_load_order=72,
        source_extracts={
            "oracle_ebs": "Extract from MTL_SAFETY_STOCKS",
            "netsuite":   "Item -> Safety Stock settings",
            "sap_ecc":    "Safety stock (MM03 MRP1)",
        },
        mock_row_counts={"oracle_ebs": 4_800, "netsuite": 1_600, "sap_ecc": 6_200},
    ),
    FusionObject(
        "Demand History", "Historical Demand Data",
        fbdi_template="Demand History (FBDI)", planned_load_order=110,
        source_extracts={
            "oracle_ebs": "Extract from OE_ORDER_LINES_ALL",
            "netsuite":   "Sales History by Item report",
            "sap_ecc":    "Sales statistics (MC94)",
        },
        mock_row_counts={"oracle_ebs": 280_000, "netsuite": 92_000, "sap_ecc": 420_000},
    ),
    FusionObject(
        "Supply Plan", "Existing Supply Plan / MPS Data",
        fbdi_template="Supply Plan Load (FBDI)", planned_load_order=90,
        source_extracts={
            "oracle_ebs": "Extract from MRP_RECOMMENDATIONS",
            "netsuite":   "Planning -> Supply Plan export",
            "sap_ecc":    "MRP results (MD04)",
        },
        mock_row_counts={"oracle_ebs": 24_000, "netsuite": 8_400, "sap_ecc": 36_000},
    ),
)


# =============================================================================
# HUMAN CAPITAL MANAGEMENT
# =============================================================================

_HCM_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Department", "Departments",
        fbdi_template="HCM Departments (FBDI)", planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from HR_ORGANIZATION_UNITS",
            "netsuite":   "Departments list export",
            "sap_ecc":    "Organizational units (PPOME)",
        },
        mock_row_counts={"oracle_ebs": 120, "netsuite": 48, "sap_ecc": 180},
    ),
    FusionObject(
        "Location", "Workforce Locations",
        fbdi_template="HCM Locations (FBDI)", planned_load_order=15,
        source_extracts={
            "oracle_ebs": "Extract from HR_LOCATIONS_ALL",
            "netsuite":   "Locations list export",
            "sap_ecc":    "Personnel areas / sub-areas (PA03)",
        },
        mock_row_counts={"oracle_ebs": 85, "netsuite": 32, "sap_ecc": 120},
    ),
    FusionObject(
        "Grade", "Grades & Salary Bands",
        fbdi_template="HCM Grades (FBDI)", planned_load_order=17,
        source_extracts={
            "oracle_ebs": "Extract from PER_GRADES",
            "netsuite":   "Payroll -> Pay Grades",
            "sap_ecc":    "Pay scales / groups (PU97)",
        },
        mock_row_counts={"oracle_ebs": 48, "netsuite": 20, "sap_ecc": 72},
    ),
    FusionObject(
        "Job", "Jobs",
        fbdi_template="HCM Jobs (FBDI)", planned_load_order=20,
        source_extracts={
            "oracle_ebs": "Extract from PER_JOBS",
            "netsuite":   "HR -> Job list",
            "sap_ecc":    "Jobs (OOSP)",
        },
        mock_row_counts={"oracle_ebs": 480, "netsuite": 180, "sap_ecc": 640},
    ),
    FusionObject(
        "Position", "Positions",
        fbdi_template="HCM Positions (FBDI)", planned_load_order=25,
        source_extracts={
            "oracle_ebs": "Extract from PER_ALL_POSITIONS",
            "netsuite":   "HR -> Position list",
            "sap_ecc":    "Positions (PPOME)",
        },
        mock_row_counts={"oracle_ebs": 680, "netsuite": 240, "sap_ecc": 920},
    ),
    FusionObject(
        "Worker", "Workers (Employees + Contingent Workers)",
        fbdi_template="HCM Workers (FBDI)", planned_load_order=70,
        source_extracts={
            "oracle_ebs": "Extract from PER_ALL_PEOPLE_F",
            "netsuite":   "Employees list export",
            "sap_ecc":    "Personnel data (PA20)",
        },
        mock_row_counts={"oracle_ebs": 1_847, "netsuite": 620, "sap_ecc": 2_400},
    ),
    FusionObject(
        "Assignment", "Work Assignments",
        fbdi_template="HCM Assignments (FBDI)", planned_load_order=72,
        source_extracts={
            "oracle_ebs": "Extract from PER_ALL_ASSIGNMENTS_F",
            "netsuite":   "HR -> Employee Assignments",
            "sap_ecc":    "Actions / org assignments (PA30)",
        },
        mock_row_counts={"oracle_ebs": 2_140, "netsuite": 680, "sap_ecc": 2_800},
    ),
    FusionObject(
        "Salary", "Employee Salaries",
        fbdi_template="HCM Salaries (FBDI)", planned_load_order=80,
        source_extracts={
            "oracle_ebs": "Extract from PER_PAY_PROPOSALS",
            "netsuite":   "Payroll -> Employee Rates",
            "sap_ecc":    "Basic pay (PA30 IT0008)",
        },
        mock_row_counts={"oracle_ebs": 1_920, "netsuite": 600, "sap_ecc": 2_500},
    ),
    FusionObject(
        "Payroll Element", "Payroll Elements",
        fbdi_template="HCM Payroll Elements (FBDI)", planned_load_order=85,
        source_extracts={
            "oracle_ebs": "Extract from PAY_ELEMENT_TYPES_F",
            "netsuite":   "Payroll items export",
            "sap_ecc":    "Wage types (OH11)",
        },
        mock_row_counts={"oracle_ebs": 280, "netsuite": 140, "sap_ecc": 380},
    ),
    FusionObject(
        "Payroll Balance", "Historical Payroll Balances",
        fbdi_template="Payroll Balances (FBDI)", planned_load_order=110,
        source_extracts={
            "oracle_ebs": "Extract from PAY_BALANCE_FEEDS_F",
            "netsuite":   "Payroll -> YTD Balance report",
            "sap_ecc":    "Payroll results (PC_PAYRESULT)",
        },
        mock_row_counts={"oracle_ebs": 48_000, "netsuite": 14_400, "sap_ecc": 62_000},
    ),
    FusionObject(
        "Benefits Plan", "Benefits Plans & Programs",
        fbdi_template="Benefits Plan Import (FBDI)", planned_load_order=60,
        source_extracts={
            "oracle_ebs": "Extract from BEN_PGM_F",
            "netsuite":   "Benefits -> Plans list",
            "sap_ecc":    "Benefit plans (HRBEN0001)",
        },
        mock_row_counts={"oracle_ebs": 48, "netsuite": 22, "sap_ecc": 65},
    ),
    FusionObject(
        "Benefits Enrollment", "Benefits Enrollments",
        fbdi_template="Benefits Enrollment (FBDI)", planned_load_order=90,
        source_extracts={
            "oracle_ebs": "Extract from BEN_ELIG_PER_ELCTBL_CHC",
            "netsuite":   "Benefits -> Active Enrollments",
            "sap_ecc":    "Benefit enrollments (HRBEN0001)",
        },
        mock_row_counts={"oracle_ebs": 4_200, "netsuite": 1_400, "sap_ecc": 5_600},
    ),
    FusionObject(
        "Absence Plan", "Absence Plans (Leave Types)",
        fbdi_template="Absence Plan Import (FBDI)", planned_load_order=55,
        source_extracts={
            "oracle_ebs": "Extract from PER_ABSENCE_ATTENDANCE_TYPES",
            "netsuite":   "HR -> Time Off Policies",
            "sap_ecc":    "Absence types (PTABS)",
        },
        mock_row_counts={"oracle_ebs": 24, "netsuite": 12, "sap_ecc": 32},
    ),
    FusionObject(
        "Absence Balance", "Accrued Leave Balances",
        fbdi_template="Absence Balance Import (FBDI)", planned_load_order=92,
        source_extracts={
            "oracle_ebs": "Extract from PER_ABSENCE_ATTENDANCES",
            "netsuite":   "HR -> Employee Time Off Balances",
            "sap_ecc":    "Leave quotas (PT50)",
        },
        mock_row_counts={"oracle_ebs": 6_400, "netsuite": 2_100, "sap_ecc": 8_200},
    ),
)


# =============================================================================
# PROJECT PORTFOLIO MANAGEMENT
# =============================================================================

_PPM_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Project Type", "Project Types",
        fbdi_template="PPM Project Types (FBDI)", planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from PA_PROJECT_TYPES_ALL",
            "netsuite":   "Projects -> Project Types",
            "sap_ecc":    "Project profiles (CJ20N)",
        },
        mock_row_counts={"oracle_ebs": 28, "netsuite": 12, "sap_ecc": 40},
    ),
    FusionObject(
        "Project", "Projects",
        fbdi_template="PPM Project Import (FBDI)", planned_load_order=70,
        source_extracts={
            "oracle_ebs": "Extract from PA_PROJECTS_ALL",
            "netsuite":   "Projects module export",
            "sap_ecc":    "Project master (CJ03)",
        },
        mock_row_counts={"oracle_ebs": 340, "netsuite": 160, "sap_ecc": 480},
    ),
    FusionObject(
        "Project Task", "Project Tasks / WBS",
        fbdi_template="PPM Tasks (FBDI)", planned_load_order=72,
        source_extracts={
            "oracle_ebs": "Extract from PA_TASKS",
            "netsuite":   "Projects -> Tasks export",
            "sap_ecc":    "WBS elements (CJ03)",
        },
        mock_row_counts={"oracle_ebs": 1_820, "netsuite": 840, "sap_ecc": 2_600},
    ),
    FusionObject(
        "Project Team Member", "Project Team Members / Resources",
        fbdi_template="PPM Team Members (FBDI)", planned_load_order=74,
        source_extracts={
            "oracle_ebs": "Extract from PA_PROJECT_PLAYERS",
            "netsuite":   "Projects -> Team Members",
            "sap_ecc":    "Project assignments (CJ20N)",
        },
        mock_row_counts={"oracle_ebs": 1_240, "netsuite": 560, "sap_ecc": 1_800},
    ),
    FusionObject(
        "Project Budget", "Project Budgets",
        fbdi_template="PPM Budgets (FBDI)", planned_load_order=80,
        source_extracts={
            "oracle_ebs": "Extract from PA_BUDGET_VERSIONS",
            "netsuite":   "Projects -> Project Budgets export",
            "sap_ecc":    "Project plan values (CJ40)",
        },
        mock_row_counts={"oracle_ebs": 480, "netsuite": 220, "sap_ecc": 680},
    ),
    FusionObject(
        "Project Expenditure", "Project Expenditures (Open Costs)",
        fbdi_template="PPM Costs (FBDI)", planned_load_order=100,
        source_extracts={
            "oracle_ebs": "Extract from PA_EXPENDITURE_ITEMS_ALL",
            "netsuite":   "Projects -> Project Expenses report",
            "sap_ecc":    "Actual costs (CJI3)",
        },
        mock_row_counts={"oracle_ebs": 12_400, "netsuite": 3_800, "sap_ecc": 18_000},
    ),
    FusionObject(
        "Project Billing Event", "Project Billing Events",
        fbdi_template="PPM Billing Events (FBDI)", planned_load_order=102,
        source_extracts={
            "oracle_ebs": "Extract from PA_EVENTS",
            "netsuite":   "Projects -> Billing Events",
            "sap_ecc":    "Billing plan (VA43)",
        },
        mock_row_counts={"oracle_ebs": 2_800, "netsuite": 840, "sap_ecc": 4_200},
    ),
    FusionObject(
        "Grant", "Grants (Awards)",
        fbdi_template="Grants Award Import (FBDI)", planned_load_order=76,
        source_extracts={
            "oracle_ebs": "Extract from GMS_AWARDS_ALL",
            "netsuite":   "Grants -> Awards list",
            "sap_ecc":    "Grants / funds management (GMIA)",
        },
        mock_row_counts={"oracle_ebs": 180, "netsuite": 60, "sap_ecc": 240},
    ),
)


# =============================================================================
# ENTERPRISE PERFORMANCE MANAGEMENT
# =============================================================================

_EPM_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "EPM Budget", "Financial Budgets (EPBCS)",
        fbdi_template="EPM Planning Data Load", planned_load_order=30,
        source_extracts={
            "oracle_ebs": "Extract from Hyperion / Essbase export",
            "netsuite":   "Budget CSV export",
            "sap_ecc":    "Profit centre planning (KE1A)",
        },
        mock_row_counts={"oracle_ebs": 2_400, "netsuite": 800, "sap_ecc": 3_600},
    ),
    FusionObject(
        "EPM Forecast", "Financial Forecasts",
        fbdi_template="EPM Forecast Load", planned_load_order=35,
        source_extracts={
            "oracle_ebs": "Extract from forecasting tools",
            "netsuite":   "Forecast scenarios export",
            "sap_ecc":    "CO-PA plan values (KE13N)",
        },
        mock_row_counts={"oracle_ebs": 1_800, "netsuite": 600, "sap_ecc": 2_800},
    ),
    FusionObject(
        "Account Reconciliation", "Account Reconciliation Profiles (ARCS)",
        fbdi_template="ARCS Profile Import", planned_load_order=50,
        source_extracts={
            "oracle_ebs": "Extract from GL_BALANCES",
            "netsuite":   "Trial Balance at period-end",
            "sap_ecc":    "Account balance report (FS10N)",
        },
        mock_row_counts={"oracle_ebs": 4_200, "netsuite": 1_400, "sap_ecc": 5_800},
    ),
    FusionObject(
        "Financial Consolidation", "Consolidation Data (FCCS)",
        fbdi_template="FCCS Data Load", planned_load_order=55,
        source_extracts={
            "oracle_ebs": "Extract from GL_BALANCES",
            "netsuite":   "Consolidated Balance Sheet report",
            "sap_ecc":    "Special ledger balances (GCAC)",
        },
        mock_row_counts={"oracle_ebs": 18_000, "netsuite": 5_400, "sap_ecc": 24_000},
    ),
)


# =============================================================================
# MAINTENANCE  (Oracle Fusion Asset Maintenance / EAM)
# =============================================================================

_MAINTENANCE_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Maintainable Asset", "Maintainable Assets",
        fbdi_template="Maintenance Asset Import (FBDI)", planned_load_order=70,
        source_extracts={
            "oracle_ebs": "Extract from CSI_ITEM_INSTANCES",
            "netsuite":   "Assets -> Fixed Asset list",
            "sap_ecc":    "Equipment master (IE03)",
        },
        mock_row_counts={"oracle_ebs": 3_800, "netsuite": 1_200, "sap_ecc": 5_400},
    ),
    FusionObject(
        "Maintenance Plan", "Preventive Maintenance Plans",
        fbdi_template="Maintenance Plan Import (FBDI)", planned_load_order=60,
        source_extracts={
            "oracle_ebs": "Extract from EAM_PM_ACTIVITIES",
            "netsuite":   "Maintenance -> Maintenance Schedules",
            "sap_ecc":    "Maintenance plans (IP03)",
        },
        mock_row_counts={"oracle_ebs": 640, "netsuite": 180, "sap_ecc": 920},
    ),
    FusionObject(
        "Open Work Order (EAM)", "Open Maintenance Work Orders",
        fbdi_template="Maintenance Work Order (FBDI)", planned_load_order=100,
        source_extracts={
            "oracle_ebs": "Extract from WIP_DISCRETE_JOBS",
            "netsuite":   "Maintenance -> Open Work Orders",
            "sap_ecc":    "Open maintenance orders (IW38)",
        },
        mock_row_counts={"oracle_ebs": 1_840, "netsuite": 480, "sap_ecc": 2_600},
    ),
)


# =============================================================================
# RISK MANAGEMENT & COMPLIANCE  (GRC)
# =============================================================================

_RISK_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Risk", "Risks",
        fbdi_template="GRC Risk Import (FBDI)", planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from GRC_RISKS",
            "netsuite":   "Risk register CSV",
            "sap_ecc":    "GRC risk data export",
        },
        mock_row_counts={"oracle_ebs": 180, "netsuite": 80, "sap_ecc": 240},
    ),
    FusionObject(
        "Risk Control", "Risk Controls",
        fbdi_template="GRC Controls (FBDI)", planned_load_order=20,
        source_extracts={
            "oracle_ebs": "Extract from GRC schema",
            "netsuite":   "Control register CSV",
            "sap_ecc":    "GRC controls export",
        },
        mock_row_counts={"oracle_ebs": 320, "netsuite": 140, "sap_ecc": 420},
    ),
    FusionObject(
        "Control Assessment", "Control Assessments / Test Results",
        fbdi_template="GRC Assessments (FBDI)", planned_load_order=30,
        source_extracts={
            "oracle_ebs": "Extract from GRC assessment tables",
            "netsuite":   "Audit results export",
            "sap_ecc":    "GRC assessment data export",
        },
        mock_row_counts={"oracle_ebs": 1_240, "netsuite": 480, "sap_ecc": 1_680},
    ),
    FusionObject(
        "Audit Finding", "Audit Findings / Issues",
        fbdi_template="GRC Issues (FBDI)", planned_load_order=35,
        source_extracts={
            "oracle_ebs": "Extract from GRC issues schema",
            "netsuite":   "Audit findings export",
            "sap_ecc":    "Audit management issues",
        },
        mock_row_counts={"oracle_ebs": 480, "netsuite": 180, "sap_ecc": 640},
    ),
)


# =============================================================================
# MODULE CATALOG
# =============================================================================

MODULES: tuple[FusionModule, ...] = (
    FusionModule(
        "financials", "Financials",
        family="financials",
        description=(
            "GL / AP / AR / Cash Management / Fixed Assets / Expenses — "
            "the core finance modules. Foundation for any Fusion go-live."
        ),
        objects=_FINANCIALS_OBJECTS,
    ),
    FusionModule(
        "tax", "Tax (Oracle Fusion Tax)",
        family="financials",
        description=(
            "Tax Regimes, Rates, Exemptions and Registrations (ZX). "
            "Loaded alongside Financials before any invoice is raised."
        ),
        objects=_TAX_OBJECTS,
    ),
    FusionModule(
        "scm", "Supply Chain (Inventory & Order Management)",
        family="scm",
        description=(
            "Items, Customers, Suppliers, Price Lists, Lot/Serial Numbers, "
            "On-Hand Balances, and Open Sales / Purchase Orders."
        ),
        objects=_SCM_OBJECTS,
    ),
    FusionModule(
        "procurement", "Procurement",
        family="scm",
        description=(
            "Purchasing Categories, Approved Supplier Lists, Blanket & "
            "Contract Agreements, Open POs and Requisitions."
        ),
        objects=_PROCUREMENT_OBJECTS,
    ),
    FusionModule(
        "manufacturing", "Manufacturing (Shop Floor)",
        family="scm",
        description=(
            "Work Centers, Resources, BOMs, Routings, Open WIP Work Orders "
            "and Quality Inspection Plans."
        ),
        objects=_MANUFACTURING_OBJECTS,
    ),
    FusionModule(
        "planning", "Supply Chain Planning",
        family="scm",
        description=(
            "Item Planning Parameters, Safety Stock, Demand History and "
            "Supply Plan data for Demand Management / S&OP."
        ),
        objects=_PLANNING_OBJECTS,
    ),
    FusionModule(
        "hcm", "Human Capital Management",
        family="hcm",
        description=(
            "Departments, Locations, Grades, Jobs, Positions, Workers, "
            "Assignments, Payroll, Benefits, Absence — full HCM migration."
        ),
        objects=_HCM_OBJECTS,
    ),
    FusionModule(
        "ppm", "Project Portfolio Management",
        family="ppm",
        description=(
            "Project Types, Projects, WBS Tasks, Team Members, Budgets, "
            "Open Expenditures, Billing Events and Grants."
        ),
        objects=_PPM_OBJECTS,
    ),
    FusionModule(
        "epm", "Enterprise Performance Management",
        family="epm",
        description=(
            "Financial Budgets (EPBCS), Forecasts, Account Reconciliation "
            "(ARCS) and Consolidation data (FCCS)."
        ),
        objects=_EPM_OBJECTS,
    ),
    FusionModule(
        "maintenance", "Asset Maintenance (EAM)",
        family="scm",
        description=(
            "Maintainable Assets, Preventive Maintenance Plans and "
            "Open Maintenance Work Orders."
        ),
        objects=_MAINTENANCE_OBJECTS,
    ),
    FusionModule(
        "risk", "Risk Management & Compliance",
        family="risk",
        description=(
            "GRC Risks, Controls, Control Assessments and Audit Findings."
        ),
        objects=_RISK_OBJECTS,
    ),
)


MODULE_BY_CODE: dict[str, FusionModule] = {m.code: m for m in MODULES}


def modules_for_codes(codes: Iterable[str]) -> list[FusionModule]:
    return [MODULE_BY_CODE[c] for c in codes if c in MODULE_BY_CODE]


def all_objects_for_modules(codes: Iterable[str]) -> list[FusionObject]:
    """Flat, de-duplicated list of objects across the selected modules.
    Objects that appear in multiple modules are returned once only."""
    seen: set[str] = set()
    out: list[FusionObject] = []
    for m in modules_for_codes(codes):
        for o in m.objects:
            if o.target_object in seen:
                continue
            seen.add(o.target_object)
            out.append(o)
    return out
