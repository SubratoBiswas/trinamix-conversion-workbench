"""Fusion Cloud module catalog.

Drives the Setup Wizard's "Implementation Scope" step. Each module
declares the canonical Fusion target objects an implementation team
typically migrates to that module, plus the conventional source-side
extract names per source ERP (NetSuite, EBS, ...).

When a customer picks modules at project creation, the server auto-
creates planned-status ``Conversion`` rows — one per canonical object —
so the migration team isn't manually inventing scope. They can still
add / remove conversions on the Project Overview after.

The catalog reflects what's actually in an Oracle Fusion go-live for
a mid-to-large enterprise. Adding a module = add an entry here +
extend the FBDI template seeds with the matching files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class FusionObject:
    """One target object the implementation migrates to a Fusion module."""

    target_object: str            # e.g. "Item" → matches Conversion.target_object
    label: str                    # human-readable name on the picker
    fbdi_template: str | None = None   # FBDI template name (if shipped)
    planned_load_order: int = 100      # default sequence on Project Overview
    # Hints — per source ERP — for how the source extract is typically
    # labelled. Surfaced as the placeholder text on the dataset upload step.
    source_extracts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FusionModule:
    code: str                     # canonical short id (used in API + DB)
    name: str                     # display name
    family: str                   # "financials" | "scm" | "hcm" | "ppm" | "epm" | "risk"
    description: str
    objects: tuple[FusionObject, ...]


# Source-extract column conventions are deliberately verbose so the
# wizard's "what will be auto-created" preview reads like a real
# implementation plan, not a generic stub.

_FINANCIALS_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Chart of Accounts", "Chart of Accounts (Coding Combinations)",
        fbdi_template="GL Account Codes (FBDI)", planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from GL_CODE_COMBINATIONS",
            "netsuite":   "Saved Search → Accounts list export",
        },
    ),
    FusionObject(
        "Legal Entity", "Legal Entity",
        fbdi_template="Legal Entity (FBDI)", planned_load_order=15,
        source_extracts={
            "oracle_ebs": "Extract from XLE_ENTITY_PROFILES",
            "netsuite":   "Setup → Subsidiaries CSV export",
        },
    ),
    FusionObject(
        "Ledger", "Ledger", fbdi_template="Ledger (FBDI)", planned_load_order=20,
        source_extracts={
            "oracle_ebs": "Extract from GL_LEDGERS",
            "netsuite":   "Accounting Books listing",
        },
    ),
    FusionObject(
        "Business Unit", "Business Unit", planned_load_order=25,
        source_extracts={
            "oracle_ebs": "Extract from HR_OPERATING_UNITS",
            "netsuite":   "Subsidiary → BU mapping export",
        },
    ),
    FusionObject(
        "Open AP Invoices", "Open AP Invoices",
        fbdi_template="Payables Invoices Import (FBDI)", planned_load_order=70,
        source_extracts={
            "oracle_ebs": "Extract from AP_INVOICES_ALL (status=Open)",
            "netsuite":   "Saved Search → Open Vendor Bills",
        },
    ),
    FusionObject(
        "Open AR Invoices", "Open AR Invoices",
        fbdi_template="Receivables Open Invoices (FBDI)", planned_load_order=72,
        source_extracts={
            "oracle_ebs": "Extract from AR_PAYMENT_SCHEDULES_ALL",
            "netsuite":   "Saved Search → Open Customer Invoices",
        },
    ),
    FusionObject(
        "Bank Accounts", "Bank Accounts",
        fbdi_template="Cash Management Bank Accounts (FBDI)", planned_load_order=40,
        source_extracts={
            "oracle_ebs": "Extract from CE_BANK_ACCOUNTS",
            "netsuite":   "Setup → Bank Accounts export",
        },
    ),
    FusionObject(
        "Fixed Assets", "Fixed Assets",
        fbdi_template="Asset Mass Additions (FBDI)", planned_load_order=80,
        source_extracts={
            "oracle_ebs": "Extract from FA_BOOKS / FA_ASSET_HISTORY",
            "netsuite":   "Fixed Asset Management module export",
        },
    ),
    FusionObject(
        "Open GL Journals", "Open GL Journals",
        fbdi_template="GL Journal Import (FBDI)", planned_load_order=85,
        source_extracts={
            "oracle_ebs": "Extract from GL_JE_HEADERS / GL_JE_LINES (unposted)",
            "netsuite":   "Saved Search → Unposted Journal Entries",
        },
    ),
)


_SCM_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "UOM", "Units of Measure", fbdi_template="UOM Import (FBDI)",
        planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from MTL_UNITS_OF_MEASURE",
            "netsuite":   "Setup → Units of Measure export",
        },
    ),
    FusionObject(
        "Inventory Org", "Inventory Organization",
        fbdi_template="Inventory Org (FBDI)", planned_load_order=15,
        source_extracts={
            "oracle_ebs": "Extract from MTL_PARAMETERS",
            "netsuite":   "Locations export",
        },
    ),
    FusionObject(
        "Item Class", "Item Catalog / Class",
        fbdi_template="Item Catalog (FBDI)", planned_load_order=20,
        source_extracts={
            "oracle_ebs": "Extract from MTL_CATEGORIES_B",
            "netsuite":   "Item Categories export",
        },
    ),
    FusionObject(
        "Item", "Item Master",
        fbdi_template="Item Master (SCM Items)", planned_load_order=30,
        source_extracts={
            "oracle_ebs": "Extract from MTL_SYSTEM_ITEMS_B",
            "netsuite":   "Saved Search → All Active Items",
        },
    ),
    FusionObject(
        "Customer", "Customer Master",
        fbdi_template="Trading Community Architecture (FBDI)",
        planned_load_order=40,
        source_extracts={
            "oracle_ebs": "Extract from HZ_PARTIES (party_type=Customer)",
            "netsuite":   "Saved Search → All Active Customers",
        },
    ),
    FusionObject(
        "Supplier", "Supplier Master",
        fbdi_template="Suppliers Import (FBDI)", planned_load_order=45,
        source_extracts={
            "oracle_ebs": "Extract from HZ_PARTIES (party_type=Supplier)",
            "netsuite":   "Saved Search → All Active Vendors",
        },
    ),
    FusionObject(
        "BOM", "Bills of Material",
        fbdi_template="BOM Import (FBDI)", planned_load_order=55,
        source_extracts={
            "oracle_ebs": "Extract from BOM_BILL_OF_MATERIALS / BOM_COMPONENTS",
            "netsuite":   "Manufacturing → BOM CSV export",
        },
    ),
    FusionObject(
        "On-Hand Balance", "On-Hand Inventory Balances",
        fbdi_template="Inventory Balances (FBDI)", planned_load_order=60,
        source_extracts={
            "oracle_ebs": "Extract from MTL_ONHAND_QUANTITIES_DETAIL",
            "netsuite":   "Saved Search → Inventory on Hand by Location",
        },
    ),
    FusionObject(
        "Sales Order", "Open Sales Orders",
        fbdi_template="Sales Order Headers (OM)", planned_load_order=80,
        source_extracts={
            "oracle_ebs": "Extract from OE_ORDER_HEADERS_ALL (status=Open)",
            "netsuite":   "Saved Search → Open Sales Orders",
        },
    ),
    FusionObject(
        "Purchase Order", "Open Purchase Orders",
        fbdi_template="Purchase Orders Import (FBDI)", planned_load_order=82,
        source_extracts={
            "oracle_ebs": "Extract from PO_HEADERS_ALL (status=Open)",
            "netsuite":   "Saved Search → Open Purchase Orders",
        },
    ),
)


_HCM_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Department", "Departments",
        fbdi_template="HCM Departments (FBDI)", planned_load_order=10,
        source_extracts={
            "oracle_ebs": "Extract from HR_ORGANIZATION_UNITS",
            "netsuite":   "Departments list export",
        },
    ),
    FusionObject(
        "Location", "Workforce Locations",
        fbdi_template="HCM Locations (FBDI)", planned_load_order=15,
        source_extracts={
            "oracle_ebs": "Extract from HR_LOCATIONS",
            "netsuite":   "Locations list",
        },
    ),
    FusionObject(
        "Job", "Jobs", fbdi_template="HCM Jobs (FBDI)", planned_load_order=20,
        source_extracts={
            "oracle_ebs": "Extract from PER_JOBS",
            "netsuite":   "—",
        },
    ),
    FusionObject(
        "Position", "Positions",
        fbdi_template="HCM Positions (FBDI)", planned_load_order=25,
        source_extracts={
            "oracle_ebs": "Extract from PER_ALL_POSITIONS",
            "netsuite":   "—",
        },
    ),
    FusionObject(
        "Worker", "Workers (Employees + Contingents)",
        fbdi_template="HCM Workers (FBDI)", planned_load_order=30,
        source_extracts={
            "oracle_ebs": "Extract from PER_ALL_PEOPLE_F",
            "netsuite":   "Employees list export",
        },
    ),
    FusionObject(
        "Payroll Element", "Payroll Elements",
        fbdi_template="HCM Payroll Elements (FBDI)", planned_load_order=70,
        source_extracts={
            "oracle_ebs": "Extract from PAY_ELEMENT_TYPES_F",
            "netsuite":   "Payroll items export",
        },
    ),
)


_PPM_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Project", "Projects",
        fbdi_template="PPM Project Import (FBDI)", planned_load_order=20,
        source_extracts={
            "oracle_ebs": "Extract from PA_PROJECTS_ALL",
            "netsuite":   "Projects module export",
        },
    ),
    FusionObject(
        "Project Task", "Project Tasks",
        fbdi_template="PPM Tasks (FBDI)", planned_load_order=25,
        source_extracts={
            "oracle_ebs": "Extract from PA_TASKS",
            "netsuite":   "Tasks export",
        },
    ),
    FusionObject(
        "Project Budget", "Project Budgets",
        fbdi_template="PPM Budgets (FBDI)", planned_load_order=40,
        source_extracts={
            "oracle_ebs": "Extract from PA_BUDGET_VERSIONS",
            "netsuite":   "Project Budgets export",
        },
    ),
    FusionObject(
        "Project Cost", "Project Expenditures (Costs)",
        fbdi_template="PPM Costs (FBDI)", planned_load_order=70,
        source_extracts={
            "oracle_ebs": "Extract from PA_EXPENDITURE_ITEMS_ALL",
            "netsuite":   "Project Expenses report",
        },
    ),
)


_EPM_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "EPM Budget", "EPM Budgets",
        fbdi_template="EPM Planning Data (FBDI)", planned_load_order=30,
        source_extracts={
            "oracle_ebs": "Extract from Hyperion / Essbase export",
            "netsuite":   "Budget CSV export",
        },
    ),
    FusionObject(
        "EPM Forecast", "EPM Forecasts",
        fbdi_template="EPM Forecast Load (FBDI)", planned_load_order=35,
        source_extracts={
            "oracle_ebs": "Extract from forecasting tools",
            "netsuite":   "Forecast scenarios export",
        },
    ),
)


_RISK_OBJECTS: tuple[FusionObject, ...] = (
    FusionObject(
        "Risk Control", "Risk Controls",
        fbdi_template="GRC Controls (FBDI)", planned_load_order=20,
        source_extracts={
            "oracle_ebs": "Extract from GRC schema",
            "netsuite":   "Risk register CSV",
        },
    ),
)


MODULES: tuple[FusionModule, ...] = (
    FusionModule(
        "financials", "Financials",
        family="financials",
        description=(
            "GL / AP / AR / Cash Management / Fixed Assets — the core "
            "finance modules. Foundation for any Fusion go-live."
        ),
        objects=_FINANCIALS_OBJECTS,
    ),
    FusionModule(
        "scm", "Supply Chain (Inventory, Procurement, OM)",
        family="scm",
        description=(
            "Items, Customers, Suppliers, Orders, POs, BOMs, Inventory "
            "balances. Standard SCM go-live scope."
        ),
        objects=_SCM_OBJECTS,
    ),
    FusionModule(
        "hcm", "Human Capital Management",
        family="hcm",
        description=(
            "Workforce, Departments, Jobs, Positions, Payroll Elements. "
            "Independent go-live from Financials but often combined."
        ),
        objects=_HCM_OBJECTS,
    ),
    FusionModule(
        "ppm", "Project Portfolio Management",
        family="ppm",
        description=(
            "Projects, Tasks, Budgets, Expenditures. Depends on Financials "
            "+ HCM being live first."
        ),
        objects=_PPM_OBJECTS,
    ),
    FusionModule(
        "epm", "Enterprise Performance Management",
        family="epm",
        description=(
            "Planning, Budgeting, Forecasting. Pulls from GL once "
            "Financials is live."
        ),
        objects=_EPM_OBJECTS,
    ),
    FusionModule(
        "risk", "Risk Management & Compliance",
        family="risk",
        description="GRC Controls + Risk Library. Audit / compliance overlay.",
        objects=_RISK_OBJECTS,
    ),
)


MODULE_BY_CODE: dict[str, FusionModule] = {m.code: m for m in MODULES}


def modules_for_codes(codes: Iterable[str]) -> list[FusionModule]:
    return [MODULE_BY_CODE[c] for c in codes if c in MODULE_BY_CODE]


def all_objects_for_modules(codes: Iterable[str]) -> list[FusionObject]:
    """Flat, de-duplicated list of objects across the selected modules.
    Items / Customers / Suppliers etc. that appear in multiple modules
    (e.g., Suppliers in both SCM and Financials) are returned once."""
    seen: set[str] = set()
    out: list[FusionObject] = []
    for m in modules_for_codes(codes):
        for o in m.objects:
            if o.target_object in seen:
                continue
            seen.add(o.target_object)
            out.append(o)
    return out
