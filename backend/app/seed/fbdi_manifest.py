"""Seed the FBDI Manifest — a comprehensive catalogue of Oracle Fusion FBDI
templates organised by module and tier.

Only the Item Master template is parsed from a real .xlsm and gets actual
fields. All others are catalogue entries (name + module + tier + phase +
required-count) so the manifest screen has realistic breadth without needing
to ship 162 actual Excel files.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.fbdi import FBDITemplate


# ─── The complete manifest ───────────────────────────────────────────────
# Each tuple: (name, module, tier, business_object, required_count, description)

# Tier guide:
#   T0 = Configuration / setup data (must precede master)
#   T1 = Master data
#   T2 = Open transactions
#   T3 = History / period-end / supplemental

MANIFEST: list[tuple[str, str, str, str, int, str]] = [
    # ─── GL (General Ledger) ───
    ("ChartOfAccountsImport",    "GL", "T0", "Chart of Accounts",  6, "COA value sets and segments"),
    ("LedgerImport",             "GL", "T0", "Ledger",              4, "Primary and secondary ledgers"),
    ("AccountingCalendarImport", "GL", "T0", "Accounting Calendar", 3, "Period definitions"),
    ("BudgetImport",             "GL", "T1", "Budget",              5, "Budget balances by account"),
    ("JournalImport",            "GL", "T2", "Journal",             7, "Manual journals and reclassifications"),
    ("AllocationRuleImport",     "GL", "T1", "Allocation Rule",     4, "Cost allocation formulas"),
    ("RevaluationImport",        "GL", "T2", "Revaluation",         3, "Foreign currency revaluations"),
    ("ConsolidationMappingImport","GL","T0", "Consolidation Map",   3, "Subsidiary → parent COA mapping"),
    ("OpenBalanceImport",        "GL", "T3", "Open Balance",        4, "Period-zero ledger balances"),

    # ─── LE (Legal Entity) ───
    ("LegalEntityImport",        "LE", "T0", "Legal Entity",        5, "Legal entities and registrations"),
    ("LegalAddressImport",       "LE", "T0", "Legal Address",       4, "Registered legal addresses"),
    ("LegalReportingUnitImport", "LE", "T0", "LRU",                 3, "Statutory reporting units"),

    # ─── CM (Cash Management) ───
    ("BankImport",               "CM", "T0", "Bank",                3, "Banks and branches"),
    ("BankAccountImport",        "CM", "T0", "Bank Account",        4, "Internal bank accounts"),
    ("BankStatementImport",      "CM", "T2", "Bank Statement",      5, "Daily bank statements"),
    ("CashTxnImport",            "CM", "T2", "Cash Transaction",    4, "Manual cash transactions"),

    # ─── AP (Accounts Payable) ───
    ("ApSuppliersImport",        "AP", "T1", "Supplier",            3, "Supplier master records"),
    ("ApSupplierSitesImport",    "AP", "T1", "Supplier Site",       4, "Supplier site addresses"),
    ("ApSupplierContactsImport", "AP", "T1", "Supplier Contact",    3, "Supplier contact persons"),
    ("ApBankAccountImport",      "AP", "T1", "Supplier Bank",       4, "Supplier banking details"),
    ("ApInvoiceImport",          "AP", "T2", "AP Invoice",          7, "Standard supplier invoices"),
    ("ApInvoiceLineImport",      "AP", "T2", "AP Invoice Line",     6, "Invoice line distributions"),
    ("ApPaymentImport",          "AP", "T2", "AP Payment",          5, "Payment records"),
    ("ApHoldsImport",            "AP", "T2", "AP Hold",             3, "Invoice holds"),
    ("ApOpenBalanceImport",      "AP", "T3", "AP Open Balance",     5, "Cutover open invoice balances"),
    ("Withholding TaxImport",    "AP", "T0", "Withholding Tax",     3, "Withholding tax codes"),

    # ─── TAX ───
    ("TaxRegimeImport",          "TAX","T0", "Tax Regime",          3, "Tax regime configuration"),
    ("TaxRatesImport",           "TAX","T0", "Tax Rate",            4, "Tax rates by jurisdiction"),
    ("TaxRulesImport",           "TAX","T0", "Tax Rule",            4, "Tax determination rules"),
    ("TaxJurisdictionImport",    "TAX","T0", "Tax Jurisdiction",    3, "Tax jurisdictions"),

    # ─── SCM (Supply Chain) ───
    ("UomImport",                "SCM","T0", "UOM",                 2, "Units of measure"),
    ("InventoryOrgImport",       "SCM","T0", "Inventory Org",       4, "Inventory organisations"),
    ("InventorySubinventoryImport","SCM","T0", "Subinventory",      3, "Subinventories and locators"),
    ("ItemClassImport",          "SCM","T0", "Item Class",          3, "Item class hierarchy"),
    ("ItemImport",               "SCM","T1", "Item",                2, "Item master records"),
    ("ItemRelationshipsImport",  "SCM","T1", "Item Relationship",   3, "Cross-references and substitutes"),
    ("ItemCostImport",           "SCM","T1", "Item Cost",           4, "Standard / average costs"),
    ("ItemCategoryImport",       "SCM","T1", "Item Category",       3, "Category assignments"),
    ("BomImport",                "SCM","T1", "BOM",                 4, "Bills of material"),
    ("RoutingImport",            "SCM","T1", "Routing",             4, "Manufacturing routings"),
    ("OnHandBalanceImport",      "SCM","T3", "On-Hand Balance",     4, "Opening on-hand inventory"),
    ("ItemRevisionsImport",      "SCM","T1", "Item Revision",       3, "Engineering revisions"),
    ("ItemSpecImport",           "SCM","T1", "Item Spec",           3, "Item specifications"),
    ("CatalogImport",            "SCM","T1", "Catalog",             3, "Product catalog hierarchy"),
    ("LocatorImport",            "SCM","T0", "Locator",             3, "Inventory locators"),
    ("LotSerialImport",          "SCM","T3", "Lot/Serial",          3, "Open lot and serial numbers"),
    ("CountTypesImport",         "SCM","T0", "Count Type",          2, "Cycle count types"),

    # ─── HCM (Human Capital) ───
    ("HcmPersonImport",          "HCM","T1", "Person",              3, "People records"),
    ("HcmAddressImport",         "HCM","T1", "Person Address",      3, "Person home addresses"),
    ("HcmEmploymentImport",      "HCM","T1", "Employment",          5, "Employment records"),
    ("HcmAssignmentImport",      "HCM","T1", "Assignment",          3, "Position assignments"),
    ("HcmWorkerImport",          "HCM","T1", "Worker",              4, "Worker master"),
    ("HcmWorkRelationshipImport","HCM","T1", "Work Relationship",   3, "Multi-employment legal links"),
    ("HcmPositionImport",        "HCM","T0", "Position",            3, "Positions"),
    ("HcmJobImport",             "HCM","T0", "Job",                 3, "Job catalogue"),
    ("HcmDepartmentImport",      "HCM","T0", "Department",          3, "Departments"),
    ("HcmGradeImport",           "HCM","T0", "Grade",               3, "Pay grades"),
    ("HcmLocationImport",        "HCM","T0", "Location",            3, "HR locations"),
    ("HcmCompensationImport",    "HCM","T1", "Compensation",        3, "Compensation history"),
    ("HcmSalaryImport",          "HCM","T1", "Salary",              3, "Current salary"),
    ("HcmAbsenceImport",         "HCM","T2", "Absence",             3, "Absence balances"),

    # ─── EXP (Expenses) ───
    ("ExpenseTypeImport",        "EXP","T0", "Expense Type",        3, "Expense type setup"),
    ("ExpenseReportImport",      "EXP","T2", "Expense Report",      5, "Pending expense reports"),

    # ─── AR (Accounts Receivable) ───
    ("ArCustomerImport",         "AR", "T1", "Customer",            3, "Customer master"),
    ("ArCustomerSiteImport",     "AR", "T1", "Customer Site",       4, "Customer sites"),
    ("ArCustomerContactImport",  "AR", "T1", "Customer Contact",    3, "Customer contacts"),
    ("ArCustomerBankImport",     "AR", "T1", "Customer Bank",       3, "Customer banking details"),
    ("CustomerCreditProfileImport","AR","T1", "Credit Profile",     2, "Customer credit limits"),
    ("ArInvoiceImport",          "AR", "T2", "AR Invoice",          6, "AR invoices and credit memos"),
    ("ArReceiptImport",          "AR", "T2", "AR Receipt",          4, "Customer receipts"),
    ("ArOpenBalanceImport",      "AR", "T3", "AR Open Balance",     5, "Open AR cutover balances"),
    ("CollectionsImport",        "AR", "T2", "Collection",          3, "Collection actions"),

    # ─── PO (Purchasing) ───
    ("PoRequisitionImport",      "PO", "T2", "Requisition",         5, "Purchase requisitions"),
    ("PurchaseOrderImport",      "PO", "T2", "Purchase Order",      6, "Purchase order headers"),
    ("PurchaseOrderLineImport",  "PO", "T2", "PO Line",             5, "Purchase order lines"),
    ("PoReceiptImport",          "PO", "T2", "PO Receipt",          4, "Receipts against POs"),
    ("PoChangeOrderImport",      "PO", "T2", "Change Order",        4, "PO change orders"),
    ("BlanketAgreementImport",   "PO", "T1", "Blanket Agreement",   4, "Blanket purchase agreements"),
    ("ContractAgreementImport",  "PO", "T1", "Contract Agreement",  4, "Contract purchase agreements"),
    ("SourcingRulesImport",      "PO", "T0", "Sourcing Rule",       3, "Approved supplier list"),

    # ─── OM (Order Management) ───
    ("SalesOrderImport",         "OM", "T2", "Sales Order",         3, "Sales order headers"),
    ("SalesOrderLinesImport",    "OM", "T2", "Sales Order Line",    4, "Sales order lines"),
    ("PriceListImport",          "OM", "T1", "Price List",          3, "Price list values"),
    ("PriceBookImport",          "OM", "T1", "Price Book",          3, "Modifier price book"),
    ("DiscountImport",           "OM", "T1", "Discount",            3, "Discount lists"),
    ("BackorderImport",          "OM", "T2", "Backorder",           3, "Backordered demand"),
    ("DropShipmentImport",       "OM", "T2", "Drop Shipment",       3, "Open drop-ship orders"),
    ("CustomerReturnImport",     "OM", "T2", "Customer Return",     3, "RMAs"),
    ("CommissionImport",         "OM", "T3", "Commission",          3, "Sales commission accruals"),
    ("ShipmentImport",           "OM", "T2", "Shipment",            3, "Open shipments"),
    ("OrderHoldImport",          "OM", "T2", "Order Hold",          3, "Order holds"),

    # ─── FA (Fixed Assets) ───
    ("FixedAssetsImport",        "FA", "T1", "Fixed Asset",         5, "Asset master"),
    ("AssetAdditionImport",      "FA", "T2", "Asset Addition",      4, "Period asset additions"),
    ("AssetDepreciationImport",  "FA", "T3", "Asset Depreciation",  3, "Accumulated depreciation"),
    ("AssetCategoryImport",      "FA", "T0", "Asset Category",      3, "Asset category accounts"),
    ("AssetTransferImport",      "FA", "T2", "Asset Transfer",      3, "Asset transfers"),
    ("AssetRetirementImport",    "FA", "T2", "Asset Retirement",    3, "Asset retirements"),

    # ─── PPM (Projects) ───
    ("PjfProjectsImport",        "PPM","T1", "Project",             3, "Project headers"),
    ("PjfProjectTasksImport",    "PPM","T1", "Project Task",        3, "Project tasks (WBS)"),
    ("PjfTeamMemberImport",      "PPM","T1", "Project Member",      3, "Team members"),
    ("ProjectContractImport",    "PPM","T1", "Project Contract",    3, "Customer contracts"),
    ("ProjectResourceImport",    "PPM","T1", "Project Resource",    2, "Named resources"),
    ("PjfBudgetImport",          "PPM","T1", "Project Budget",      4, "Project budgets"),
    ("PjfCostImport",            "PPM","T2", "Project Cost",        4, "Cost transactions"),
    ("PjfBillingImport",         "PPM","T2", "Project Billing",     4, "Billing events"),
    ("PjfRateScheduleImport",    "PPM","T0", "Rate Schedule",       3, "Rate schedules"),

    # ─── PAY (Payroll) ───
    ("PayrollEarningsImport",    "PAY","T2", "Payroll Earnings",    4, "Earnings elements"),
    ("PayrollDeductionsImport",  "PAY","T2", "Payroll Deductions",  4, "Deduction elements"),
    ("PayrollTaxImport",         "PAY","T2", "Payroll Tax",         3, "Tax balances"),
    ("PayrollBalanceImport",     "PAY","T3", "Payroll Balance",     4, "Year-to-date balances"),
    ("ElementEntryImport",       "PAY","T1", "Element Entry",       3, "Recurring element entries"),
    ("PayrollDefinitionImport",  "PAY","T0", "Payroll Definition",  3, "Payroll calendar"),

    # ─── MFG (Manufacturing) ───
    ("WorkOrderImport",          "MFG","T2", "Work Order",          4, "Open work orders"),
    ("WorkDefinitionImport",     "MFG","T1", "Work Definition",     3, "Manufacturing work definitions"),
    ("WorkCenterImport",         "MFG","T0", "Work Center",         3, "Work centers"),
    ("ResourceImport",           "MFG","T0", "Resource",            3, "Manufacturing resources"),
    ("StandardOperationImport",  "MFG","T0", "Standard Op",         3, "Standard operations"),
    ("WipIssueImport",           "MFG","T2", "WIP Issue",           3, "WIP material issues"),
    ("WipCompletionImport",      "MFG","T2", "WIP Completion",      3, "WIP completions"),
    ("ScrapImport",              "MFG","T2", "Scrap",               3, "Scrap transactions"),
    ("EngineeringChangeImport",  "MFG","T1", "ECO",                 3, "Engineering changes"),
]

# Phase distribution — most templates land in "Blueprint" or "Build" early in
# an engagement; a few are "Cutover" to indicate they're final.
PHASE_BY_TIER = {
    "T0": "Blueprint",
    "T1": "Build",
    "T2": "Validation",
    "T3": "Cutover",
}


def seed_fbdi_manifest(db: Session) -> int:
    """Seed the manifest stubs. Returns number of rows added.

    Templates already present (e.g. the parsed Item template) are skipped.
    """
    existing_names = {t.name for t in db.query(FBDITemplate.name).all()}
    added = 0
    for name, module, tier, biz, req, desc in MANIFEST:
        if name in existing_names:
            continue
        db.add(FBDITemplate(
            name=name,
            module=module,
            tier=tier,
            phase=PHASE_BY_TIER.get(tier, "Blueprint"),
            business_object=biz,
            required_field_count=req,
            description=desc,
            status="manual",   # not parsed from a real .xlsm
        ))
        added += 1
    db.commit()
    return added
