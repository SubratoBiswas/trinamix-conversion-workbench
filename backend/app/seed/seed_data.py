"""Seed data: admin user + demo dataset + FBDI Item template + dependencies + sample project.

Idempotent: safe to call multiple times (it checks before inserting).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings
from app.database import init_db
from app.models.conversion import Conversion
from app.models.dataset import Dataset, DatasetColumnProfile
from app.models.dependency import Dependency
from app.models.fbdi import FBDIField, FBDISheet, FBDITemplate
from app.models.project import Project
from app.models.user import User
from app.parsers import parse_fbdi_template, parse_tabular, profile_dataframe
from app.services.auth_service import hash_password


SEED_DIR = Path(__file__).parent / "sample_files"


SEEDED_DEPENDENCIES = [
    ("UOM", "Item", "prerequisite", "Items reference UOM codes — UOM must exist first"),
    ("Inventory Org", "Item", "prerequisite", "Items belong to organisations"),
    ("Item Class", "Item", "prerequisite", "Item Class drives item attribute defaults"),
    ("Item", "Sales Order", "prerequisite", "Sales orders reference items"),
    ("Customer", "Sales Order", "prerequisite", "Sales orders require valid customers"),
    ("UOM", "Sales Order", "prerequisite", "Quantity units must resolve"),
    ("Supplier", "Purchase Order", "prerequisite", "POs require valid suppliers"),
    ("Item", "Purchase Order", "prerequisite", "POs reference items"),
    ("Item", "BOM", "prerequisite", "BOM components must exist as items"),
    ("Inventory Org", "On-Hand Balance", "prerequisite", "Balances are stored per org"),
    ("Item", "On-Hand Balance", "prerequisite", "Balances reference items"),
]


async def _seed_admin() -> User:
    user = await User.find_one(User.email == settings.ADMIN_EMAIL)
    if user:
        return user
    user = User(
        name=settings.ADMIN_NAME,
        email=settings.ADMIN_EMAIL,
        role="admin",
        password_hash=hash_password(settings.ADMIN_PASSWORD),
    )
    await user.insert()
    return user


async def _seed_dependencies() -> None:
    count = await Dependency.count()
    if count > 0:
        return
    for src, tgt, rtype, desc in SEEDED_DEPENDENCIES:
        await Dependency(
            source_object=src,
            target_object=tgt,
            relationship_type=rtype,
            description=desc,
        ).insert()


async def _seed_one_dataset(
    csv_filename: str, name: str, description: str
) -> Dataset | None:
    src_csv = SEED_DIR / csv_filename
    if not src_csv.exists():
        return None

    # Always ensure the file exists on disk (ephemeral FS is wiped on redeploy)
    dest_dir = settings.upload_path / "datasets"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src_csv.name
    if not dest.exists():
        shutil.copy2(src_csv, dest)

    existing = await Dataset.find_one(Dataset.name == name)
    if existing:
        # Always point to the seeded file — original upload path is wiped on redeploy
        if existing.file_path != str(dest):
            await existing.set({Dataset.file_path: str(dest), Dataset.file_name: dest.name})
        return existing

    df = parse_tabular(dest, file_type="csv")
    profiles = profile_dataframe(df)

    ds = Dataset(
        name=name,
        description=description,
        file_name=dest.name,
        file_path=str(dest),
        file_type="csv",
        row_count=len(df),
        column_count=len(df.columns),
        status="profiled",
    )
    await ds.insert()
    for prof in profiles:
        await DatasetColumnProfile(dataset_id=ds.id, **prof).insert()
    return ds


async def _seed_datasets() -> tuple[Dataset | None, Dataset | None]:
    item_ds = await _seed_one_dataset(
        csv_filename="legacy_item_master.csv",
        name="Legacy Item Master Extract",
        description=(
            "60-row legacy NetSuite Item extract: 34 columns, intentional data "
            "quality issues across dates, UOMs, currencies, country codes, and "
            "duplicate keys — exercises every transformation flavour."
        ),
    )
    so_ds = await _seed_one_dataset(
        csv_filename="legacy_sales_orders.csv",
        name="Legacy Sales Order Extract",
        description=(
            "180-row legacy NetSuite Sales Order extract referencing the Item "
            "Master by ITEM_NUM. Contains references to items that fail in the "
            "upstream Item conversion — surfaces the dependency cascade visibly."
        ),
    )
    await _seed_one_dataset(
        csv_filename="legacy_item_extract.csv",
        name="Legacy Item Extract (Demo)",
        description="Original 8-column quick-start sample.",
    )
    await _seed_one_dataset(
        csv_filename="legacy_po_extract.csv",
        name="SAP PO Extract for Demo",
        description=(
            "9-row SAP Purchase Order extract: 42 columns covering PO headers, "
            "lines, vendors, and accounting segments — used for PurchaseOrderImport mapping demo."
        ),
    )
    return item_ds, so_ds


async def _seed_fbdi_template() -> FBDITemplate | None:
    src = SEED_DIR / "ScpItemImportTemplate.xlsm"
    if not src.exists():
        return None

    # Always ensure the file exists on disk (ephemeral FS is wiped on redeploy)
    dest_dir = settings.upload_path / "fbdi"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists():
        shutil.copy2(src, dest)

    existing = await FBDITemplate.find_one(FBDITemplate.name == "Item Master (SCM Items)")
    if existing:
        return existing

    parsed = parse_fbdi_template(dest)
    tpl = FBDITemplate(
        name="Item Master (SCM Items)",
        module="SCM",
        tier="T1",
        phase="Validation",
        business_object="Item",
        version="1.0",
        file_name=dest.name,
        file_path=str(dest),
        status="parsed",
        description="Oracle Fusion SCM — Item Import Template (seeded demo).",
        required_field_count=2,
    )
    await tpl.insert()

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
        f = dict(f)
        sheet_id = sheet_id_by_name.get(f.pop("sheet_name", ""))
        if sheet_id is None:
            continue
        await FBDIField(template_id=tpl.id, sheet_id=sheet_id, **f).insert()
    return tpl


async def _seed_sales_order_template() -> FBDITemplate | None:
    existing = await FBDITemplate.find_one(FBDITemplate.name == "Sales Order Headers (OM)")
    if existing:
        return existing

    tpl = FBDITemplate(
        name="Sales Order Headers (OM)",
        module="OM",
        tier="T2",
        phase="Build",
        business_object="Sales Order",
        version="1.0",
        status="manual",
        description=(
            "Oracle Fusion OM — Sales Order Import. Subset of fields covering "
            "header + first-line attributes for the demo."
        ),
        required_field_count=4,
    )
    await tpl.insert()

    sheet = FBDISheet(template_id=tpl.id, sheet_name="OrderHeaders", sequence=1, field_count=18)
    await sheet.insert()

    fields = [
        ("OrderNumber",          "Source order number",             "Character", 50,  True,  1,  "SO-200001"),
        ("OrderType",            "Order transaction type",           "Character", 30,  True,  2,  "STANDARD"),
        ("OrderDate",            "Order header date",                "Date",      None, True,  3,  "2024-07-01"),
        ("OrderStatus",          "Order header status",              "Character", 30,  False, 4,  "BOOKED"),
        ("CustomerNumber",       "Sold-to customer party number",    "Character", 50,  True,  5,  "1001"),
        ("CustomerName",         "Sold-to customer party name",      "Character", 250, False, 6,  "Northwind Industries"),
        ("InventoryItemNumber",  "Item number being ordered",        "Character", 100, True,  7,  "AS54888"),
        ("LineNumber",           "Order line number",                "Number",    None, True,  8,  "1"),
        ("OrderedQuantity",      "Quantity ordered",                 "Number",    None, True,  9,  "10"),
        ("UnitOfMeasureCode",    "Order line UOM",                   "Character", 10,  True,  10, "Ea"),
        ("UnitSellingPrice",     "Selling price per unit",           "Number",    None, True,  11, "125.00"),
        ("CurrencyCode",         "Order currency code",              "Character", 3,   True,  12, "USD"),
        ("RequestShipDate",      "Customer requested ship date",     "Date",      None, False, 13, "2024-08-01"),
        ("PromisedShipDate",     "Promised ship date",               "Date",      None, False, 14, "2024-08-05"),
        ("ShipFromOrgCode",      "Source inventory organisation",    "Character", 18,  True,  15, "M1"),
        ("PaymentTerms",         "Customer payment terms",           "Character", 30,  False, 16, "Net 30"),
        ("FreightTerms",         "Freight terms code",               "Character", 30,  False, 17, "FOB Origin"),
        ("SourceSystem",         "Originating source system",        "Character", 30,  False, 18, "NETSUITE"),
    ]
    for f in fields:
        await FBDIField(
            template_id=tpl.id,
            sheet_id=sheet.id,
            field_name=f[0],
            description=f[1],
            data_type=f[2],
            max_length=f[3],
            required=int(f[4]),
            sequence=f[5],
            sample_value=f[6],
        ).insert()
    return tpl


async def _seed_environments(project: Project) -> None:
    from app.models.environment import DEFAULT_ENVIRONMENTS, Environment
    count = await Environment.find(Environment.project_id == project.id).count()
    if count > 0:
        return
    for env in DEFAULT_ENVIRONMENTS:
        await Environment(
            project_id=project.id,
            name=env["name"],
            description=env["description"],
            sort_order=env["order"],
            color=env["color"],
            sox_controlled=1 if env["name"] == "PROD" else 0,
        ).insert()


async def _seed_demo_engagement(
    item_ds: Dataset | None,
    so_ds: Dataset | None,
    item_tpl: FBDITemplate | None,
    so_tpl: FBDITemplate | None,
) -> None:
    from datetime import date, datetime, time as dtime

    if await Project.find_one(Project.name == "Trinamix → Oracle SCM Cloud Phase 1"):
        return

    proj = Project(
        name="Trinamix → Oracle SCM Cloud Phase 1",
        description=(
            "Phase 1 of the Trinamix Oracle Fusion SCM Cloud implementation. "
            "Item, customer, supplier, and order master conversions for the "
            "Plano operating unit. Cutover window 18:00–06:00 UTC, all "
            "environments staging in parallel."
        ),
        client="Trinamix",
        target_environment="Oracle Fusion SCM Cloud (UAT)",
        go_live_date=date(2026, 9, 1),
        owner=settings.ADMIN_EMAIL,
        status="in_progress",
        production_cutover_start=datetime.combine(date(2026, 8, 31), dtime(18, 0)),
        production_cutover_end=datetime.combine(date(2026, 9, 1), dtime(6, 0)),
        migration_lead="migration_lead@trinamix.com",
        data_owner="data_owner@trinamix.com",
        sox_controlled=1,
    )
    await proj.insert()

    await _seed_environments(proj)

    if item_ds is not None and item_tpl is not None:
        await Conversion(
            project_id=proj.id,
            name="Item Master Conversion",
            description=(
                "Legacy NetSuite item extract → Oracle Fusion Item Master. "
                "60 source records with deliberate data quality issues for "
                "demo purposes (date formats, hyphens, status mappings, "
                "currency codes, country codes, negative weights, duplicates)."
            ),
            target_object="Item",
            dataset_id=item_ds.id,
            template_id=item_tpl.id,
            planned_load_order=30,
            status="draft",
            created_by=settings.ADMIN_EMAIL,
        ).insert()

    if so_ds is not None and so_tpl is not None:
        await Conversion(
            project_id=proj.id,
            name="Sales Order Backlog",
            description=(
                "Open NetSuite sales-order backlog → Fusion OM. References "
                "items by ITEM_NUM — when upstream Item Master rows fail, "
                "dependent SO lines surface in the Error Traceback view."
            ),
            target_object="Sales Order",
            dataset_id=so_ds.id,
            template_id=so_tpl.id,
            planned_load_order=80,
            status="draft",
            created_by=settings.ADMIN_EMAIL,
        ).insert()

    PLANNED = [
        ("UOM Master",             "UOM",             10, "loaded"),
        ("Inventory Organization", "Inventory Org",   15, "loaded"),
        ("Item Class Setup",       "Item Class",      20, "loaded"),
        ("Customer Master",        "Customer",        40, "planning"),
        ("Supplier Master",        "Supplier",        50, "planning"),
        ("BOM Conversion",         "BOM",             60, "planning"),
        ("On-Hand Balance Load",   "On-Hand Balance", 70, "planning"),
        ("Open Purchase Orders",   "Purchase Order",  90, "planning"),
    ]
    for name, obj, order, status in PLANNED:
        await Conversion(
            project_id=proj.id,
            name=name,
            target_object=obj,
            planned_load_order=order,
            status=status,
            created_by=settings.ADMIN_EMAIL,
        ).insert()


async def _seed_purchase_order_template() -> FBDITemplate | None:
    existing = await FBDITemplate.find_one(FBDITemplate.name == "PurchaseOrderImport")
    if existing:
        # Stub may exist from manifest — still seed fields if none present
        field_count = await FBDIField.find(FBDIField.template_id == existing.id).count()
        if field_count > 0:
            return existing
        tpl = existing  # reuse existing template, just add sheets/fields below
    else:
        tpl = FBDITemplate(
            name="PurchaseOrderImport",
            module="PO",
            tier="T2",
            phase="Build",
            business_object="Purchase Order",
            version="1.0",
            status="manual",
            description=(
                "Oracle Fusion PO — Purchase Order Import FBDI. Four sheets: "
                "Headers, Lines, Line Locations (Shipments), and Distributions."
            ),
            required_field_count=11,
        )
        await tpl.insert()

    sheet_1 = FBDISheet(
        template_id=tpl.id,
        sheet_name="PoHeadersInterface",
        sequence=1,
        field_count=165,
    )
    await sheet_1.insert()
    for seq, col in enumerate([
        "INTERFACE_HEADER_KEY",
        "ACTION",
        "BATCH_ID",
        "INTERFACE_SOURCE_CODE",
        "APPROVAL_ACTION",
        "DOCUMENT_NUM",
        "DOCUMENT_TYPE_CODE",
        "STYLE_DISPLAY_NAME",
        "PRC_BU_NAME",
        "REQ_BU_NAME",
        "SOLDTO_LE_NAME",
        "BILLTO_BU_NAME",
        "AGENT_NAME",
        "CURRENCY_CODE",
        "RATE",
        "RATE_TYPE",
        "RATE_DATE",
        "COMMENTS",
        "BILL_TO_LOCATION",
        "SHIP_TO_LOCATION",
        "VENDOR_NAME",
        "VENDOR_NUM",
        "VENDOR_SITE_CODE",
        "VENDOR_CONTACT",
        "VENDOR_DOC_NUM",
        "FOB",
        "FREIGHT_CARRIER",
        "FREIGHT_TERMS",
        "PAY_ON_CODE",
        "PAYMENT_TERMS",
        "ORIGINATOR_ROLE",
        "CHANGE_ORDER_DESC",
        "ACCEPTANCE_REQUIRED_FLAG",
        "ACCEPTANCE_WITHIN_DAYS",
        "SUPPLIER_NOTIF_METHOD",
        "FAX",
        "EMAIL_ADDRESS",
        "CONFIRMING_ORDER_FLAG",
        "NOTE_TO_VENDOR",
        "NOTE_TO_RECEIVER",
        "DEFAULT_TAXATION_COUNTRY",
        "TAX_DOCUMENT_SUBTYPE",
        "ATTRIBUTE_CATEGORY",
        "ATTRIBUTE1",
        "ATTRIBUTE2",
        "ATTRIBUTE3",
        "ATTRIBUTE4",
        "ATTRIBUTE5",
        "ATTRIBUTE6",
        "ATTRIBUTE7",
        "ATTRIBUTE8",
        "ATTRIBUTE9",
        "ATTRIBUTE10",
        "ATTRIBUTE11",
        "ATTRIBUTE12",
        "ATTRIBUTE13",
        "ATTRIBUTE14",
        "ATTRIBUTE15",
        "ATTRIBUTE16",
        "ATTRIBUTE17",
        "ATTRIBUTE18",
        "ATTRIBUTE19",
        "ATTRIBUTE20",
        "ATTRIBUTE_DATE1",
        "ATTRIBUTE_DATE2",
        "ATTRIBUTE_DATE3",
        "ATTRIBUTE_DATE4",
        "ATTRIBUTE_DATE5",
        "ATTRIBUTE_DATE6",
        "ATTRIBUTE_DATE7",
        "ATTRIBUTE_DATE8",
        "ATTRIBUTE_DATE9",
        "ATTRIBUTE_DATE10",
        "ATTRIBUTE_NUMBER1",
        "ATTRIBUTE_NUMBER2",
        "ATTRIBUTE_NUMBER3",
        "ATTRIBUTE_NUMBER4",
        "ATTRIBUTE_NUMBER5",
        "ATTRIBUTE_NUMBER6",
        "ATTRIBUTE_NUMBER7",
        "ATTRIBUTE_NUMBER8",
        "ATTRIBUTE_NUMBER9",
        "ATTRIBUTE_NUMBER10",
        "ATTRIBUTE_TIMESTAMP1",
        "ATTRIBUTE_TIMESTAMP2",
        "ATTRIBUTE_TIMESTAMP3",
        "ATTRIBUTE_TIMESTAMP4",
        "ATTRIBUTE_TIMESTAMP5",
        "ATTRIBUTE_TIMESTAMP6",
        "ATTRIBUTE_TIMESTAMP7",
        "ATTRIBUTE_TIMESTAMP8",
        "ATTRIBUTE_TIMESTAMP9",
        "ATTRIBUTE_TIMESTAMP10",
        "AGENT_EMAIL_ADDRESS",
        "MODE_OF_TRANSPORT",
        "SERVICE_LEVEL",
        "FIRST_PTY_REG_NUM",
        "THIRD_PTY_REG_NUM",
        "BUYER_MANAGED_TRANSPORT_FLAG",
        "MASTER_CONTRACT_NUMBER",
        "MASTER_CONTRACT_TYPE",
        "CC_EMAIL_ADDRESS",
        "BCC_EMAIL_ADDRESS",
        "GLOBAL_ATTRIBUTE1",
        "GLOBAL_ATTRIBUTE2",
        "GLOBAL_ATTRIBUTE3",
        "GLOBAL_ATTRIBUTE4",
        "GLOBAL_ATTRIBUTE5",
        "GLOBAL_ATTRIBUTE6",
        "OVERRIDING_APPROVER_NAME",
        "SKIP_ELECTRONIC_COMM_FLAG",
        "CHECKLIST_TITLE",
        "CHECKLIST_NUM",
        "ALT_CONTACT_EMAIL_ADDRESS",
        "SH_ATTRIBUTE_CATEGORY",
        "SH_ATTRIBUTE1",
        "SH_ATTRIBUTE2",
        "SH_ATTRIBUTE3",
        "SH_ATTRIBUTE4",
        "SH_ATTRIBUTE5",
        "SH_ATTRIBUTE6",
        "SH_ATTRIBUTE7",
        "SH_ATTRIBUTE8",
        "SH_ATTRIBUTE9",
        "SH_ATTRIBUTE10",
        "SH_ATTRIBUTE11",
        "SH_ATTRIBUTE12",
        "SH_ATTRIBUTE13",
        "SH_ATTRIBUTE14",
        "SH_ATTRIBUTE15",
        "SH_ATTRIBUTE16",
        "SH_ATTRIBUTE17",
        "SH_ATTRIBUTE18",
        "SH_ATTRIBUTE19",
        "SH_ATTRIBUTE20",
        "SH_ATTRIBUTE_NUMBER1",
        "SH_ATTRIBUTE_NUMBER2",
        "SH_ATTRIBUTE_NUMBER3",
        "SH_ATTRIBUTE_NUMBER4",
        "SH_ATTRIBUTE_NUMBER5",
        "SH_ATTRIBUTE_NUMBER6",
        "SH_ATTRIBUTE_NUMBER7",
        "SH_ATTRIBUTE_NUMBER8",
        "SH_ATTRIBUTE_NUMBER9",
        "SH_ATTRIBUTE_NUMBER10",
        "SH_ATTRIBUTE_DATE1",
        "SH_ATTRIBUTE_DATE2",
        "SH_ATTRIBUTE_DATE3",
        "SH_ATTRIBUTE_DATE4",
        "SH_ATTRIBUTE_DATE5",
        "SH_ATTRIBUTE_DATE6",
        "SH_ATTRIBUTE_DATE7",
        "SH_ATTRIBUTE_DATE8",
        "SH_ATTRIBUTE_DATE9",
        "SH_ATTRIBUTE_DATE10",
        "SH_ATTRIBUTE_TIMESTAMP1",
        "SH_ATTRIBUTE_TIMESTAMP2",
        "SH_ATTRIBUTE_TIMESTAMP3",
        "SH_ATTRIBUTE_TIMESTAMP4",
        "SH_ATTRIBUTE_TIMESTAMP5",
        "SH_ATTRIBUTE_TIMESTAMP6",
        "SH_ATTRIBUTE_TIMESTAMP7",
        "SH_ATTRIBUTE_TIMESTAMP8",
        "SH_ATTRIBUTE_TIMESTAMP9",
        "SH_ATTRIBUTE_TIMESTAMP10",
    ], start=1):
        is_req = col in {'VENDOR_SITE_CODE', 'VENDOR_NAME', 'ACTION', 'PAYMENT_TERMS', 'DOCUMENT_NUM', 'PRC_BU_NAME', 'INTERFACE_HEADER_KEY', 'CURRENCY_CODE', 'DOCUMENT_TYPE_CODE', 'AGENT_NAME', 'VENDOR_NUM'}
        await FBDIField(
            template_id=tpl.id,
            sheet_id=sheet_1.id,
            field_name=col,
            description=col.replace('_', ' ').title(),
            data_type="Character",
            required=1 if is_req else 0,
            sequence=seq,
        ).insert()

    sheet_2 = FBDISheet(
        template_id=tpl.id,
        sheet_name="PoLinesInterface",
        sequence=2,
        field_count=153,
    )
    await sheet_2.insert()
    for seq, col in enumerate([
        "INTERFACE_LINE_KEY",
        "INTERFACE_HEADER_KEY",
        "ACTION",
        "LINE_NUM",
        "LINE_TYPE",
        "ITEM",
        "ITEM_DESCRIPTION",
        "ITEM_REVISION",
        "CATEGORY",
        "AMOUNT",
        "SHIPPING_UOM_QUANTITY",
        "SHIPPING_UNIT_OF_MEASURE",
        "UNIT_PRICE",
        "SECONDARY_QUANTITY",
        "SECONDARY_UOM_CODE",
        "VENDOR_PRODUCT_NUM",
        "NEGOTIATED_BY_PREPARER_FLAG",
        "HAZARD_CLASS",
        "UN_NUMBER",
        "NOTE_TO_VENDOR",
        "NOTE_TO_RECEIVER",
        "ATTRIBUTE_CATEGORY",
        "ATTRIBUTE1",
        "ATTRIBUTE2",
        "ATTRIBUTE3",
        "ATTRIBUTE4",
        "ATTRIBUTE5",
        "ATTRIBUTE6",
        "ATTRIBUTE7",
        "ATTRIBUTE8",
        "ATTRIBUTE9",
        "ATTRIBUTE10",
        "ATTRIBUTE11",
        "ATTRIBUTE12",
        "ATTRIBUTE13",
        "ATTRIBUTE14",
        "ATTRIBUTE15",
        "ATTRIBUTE16",
        "ATTRIBUTE17",
        "ATTRIBUTE18",
        "ATTRIBUTE19",
        "ATTRIBUTE20",
        "ATTRIBUTE_DATE1",
        "ATTRIBUTE_DATE2",
        "ATTRIBUTE_DATE3",
        "ATTRIBUTE_DATE4",
        "ATTRIBUTE_DATE5",
        "ATTRIBUTE_DATE6",
        "ATTRIBUTE_DATE7",
        "ATTRIBUTE_DATE8",
        "ATTRIBUTE_DATE9",
        "ATTRIBUTE_DATE10",
        "ATTRIBUTE_NUMBER1",
        "ATTRIBUTE_NUMBER2",
        "ATTRIBUTE_NUMBER3",
        "ATTRIBUTE_NUMBER4",
        "ATTRIBUTE_NUMBER5",
        "ATTRIBUTE_NUMBER6",
        "ATTRIBUTE_NUMBER7",
        "ATTRIBUTE_NUMBER8",
        "ATTRIBUTE_NUMBER9",
        "ATTRIBUTE_NUMBER10",
        "ATTRIBUTE_TIMESTAMP1",
        "ATTRIBUTE_TIMESTAMP2",
        "ATTRIBUTE_TIMESTAMP3",
        "ATTRIBUTE_TIMESTAMP4",
        "ATTRIBUTE_TIMESTAMP5",
        "ATTRIBUTE_TIMESTAMP6",
        "ATTRIBUTE_TIMESTAMP7",
        "ATTRIBUTE_TIMESTAMP8",
        "ATTRIBUTE_TIMESTAMP9",
        "ATTRIBUTE_TIMESTAMP10",
        "UNIT_WEIGHT",
        "WEIGHT_UOM_CODE",
        "WEIGHT_UNIT_OF_MEASURE",
        "UNIT_VOLUME",
        "VOLUME_UOM_CODE",
        "VOLUME_UNIT_OF_MEASURE",
        "TEMPLATE_NAME",
        "ITEM_ATTRIBUTE_CATEGORY",
        "ITEM_ATTRIBUTE1",
        "ITEM_ATTRIBUTE2",
        "ITEM_ATTRIBUTE3",
        "ITEM_ATTRIBUTE4",
        "ITEM_ATTRIBUTE5",
        "ITEM_ATTRIBUTE6",
        "ITEM_ATTRIBUTE7",
        "ITEM_ATTRIBUTE8",
        "ITEM_ATTRIBUTE9",
        "ITEM_ATTRIBUTE10",
        "ITEM_ATTRIBUTE11",
        "ITEM_ATTRIBUTE12",
        "ITEM_ATTRIBUTE13",
        "ITEM_ATTRIBUTE14",
        "ITEM_ATTRIBUTE15",
        "SOURCE_AGREEMENT_PRC_BU_NAME",
        "SOURCE_AGREEMENT",
        "SOURCE_AGREEMENT_LINE",
        "DISCOUNT_TYPE",
        "DISCOUNT",
        "DISCOUNT_REASON",
        "MAX_RETAINAGE_AMOUNT",
        "UNIT_OF_MEASURE",
        "SH_ATTRIBUTE1",
        "SH_ATTRIBUTE2",
        "SH_ATTRIBUTE3",
        "SH_ATTRIBUTE4",
        "SH_ATTRIBUTE5",
        "SH_ATTRIBUTE6",
        "SH_ATTRIBUTE7",
        "SH_ATTRIBUTE8",
        "SH_ATTRIBUTE9",
        "SH_ATTRIBUTE10",
        "SH_ATTRIBUTE11",
        "SH_ATTRIBUTE12",
        "SH_ATTRIBUTE13",
        "SH_ATTRIBUTE14",
        "SH_ATTRIBUTE15",
        "SH_ATTRIBUTE16",
        "SH_ATTRIBUTE17",
        "SH_ATTRIBUTE18",
        "SH_ATTRIBUTE19",
        "SH_ATTRIBUTE20",
        "SH_ATTRIBUTE_NUMBER1",
        "SH_ATTRIBUTE_NUMBER2",
        "SH_ATTRIBUTE_NUMBER3",
        "SH_ATTRIBUTE_NUMBER4",
        "SH_ATTRIBUTE_NUMBER5",
        "SH_ATTRIBUTE_NUMBER6",
        "SH_ATTRIBUTE_NUMBER7",
        "SH_ATTRIBUTE_NUMBER8",
        "SH_ATTRIBUTE_NUMBER9",
        "SH_ATTRIBUTE_NUMBER10",
        "SH_ATTRIBUTE_DATE1",
        "SH_ATTRIBUTE_DATE2",
        "SH_ATTRIBUTE_DATE3",
        "SH_ATTRIBUTE_DATE4",
        "SH_ATTRIBUTE_DATE5",
        "SH_ATTRIBUTE_DATE6",
        "SH_ATTRIBUTE_DATE7",
        "SH_ATTRIBUTE_DATE8",
        "SH_ATTRIBUTE_DATE9",
        "SH_ATTRIBUTE_DATE10",
        "SH_ATTRIBUTE_TIMESTAMP1",
        "SH_ATTRIBUTE_TIMESTAMP2",
        "SH_ATTRIBUTE_TIMESTAMP3",
        "SH_ATTRIBUTE_TIMESTAMP4",
        "SH_ATTRIBUTE_TIMESTAMP5",
        "SH_ATTRIBUTE_TIMESTAMP6",
        "SH_ATTRIBUTE_TIMESTAMP7",
        "SH_ATTRIBUTE_TIMESTAMP8",
        "SH_ATTRIBUTE_TIMESTAMP9",
        "SH_ATTRIBUTE_TIMESTAMP10",
    ], start=1):
        is_req = col in {'UNIT_OF_MEASURE', 'LINE_TYPE', 'UNIT_PRICE', 'ACTION', 'INTERFACE_LINE_KEY', 'LINE_NUM', 'INTERFACE_HEADER_KEY', 'SHIPPING_UOM_QUANTITY', 'CATEGORY', 'ITEM', 'ITEM_DESCRIPTION'}
        await FBDIField(
            template_id=tpl.id,
            sheet_id=sheet_2.id,
            field_name=col,
            description=col.replace('_', ' ').title(),
            data_type="Character",
            required=1 if is_req else 0,
            sequence=seq,
        ).insert()

    sheet_3 = FBDISheet(
        template_id=tpl.id,
        sheet_name="PoLineLocationsInterface",
        sequence=3,
        field_count=97,
    )
    await sheet_3.insert()
    for seq, col in enumerate([
        "INTERFACE_LINE_LOCATION_KEY",
        "INTERFACE_LINE_KEY",
        "SHIPMENT_NUM",
        "SHIP_TO_LOCATION",
        "SHIP_TO_ORGANIZATION_CODE",
        "AMOUNT",
        "SHIPPING_UOM_QUANTITY",
        "NEED_BY_DATE",
        "PROMISED_DATE",
        "SECONDARY_QUANTITY",
        "SECONDARY_UOM_CODE",
        "DESTINATION_TYPE_CODE",
        "ACCRUE_ON_RECEIPT_FLAG",
        "ALLOW_SUBSTITUTE_RECEIPTS_FLAG",
        "ASSESSABLE_VALUE",
        "DAYS_EARLY_RECEIPT_ALLOWED",
        "DAYS_LATE_RECEIPT_ALLOWED",
        "ENFORCE_SHIP_TO_LOCATION_CODE",
        "INSPECTION_REQUIRED_FLAG",
        "RECEIPT_REQUIRED_FLAG",
        "INVOICE_CLOSE_TOLERANCE",
        "RECEIVE_CLOSE_TOLERANCE",
        "QTY_RCV_TOLERANCE",
        "QTY_RCV_EXCEPTION_CODE",
        "RECEIPT_DAYS_EXCEPTION_CODE",
        "RECEIVING_ROUTING",
        "NOTE_TO_RECEIVER",
        "INPUT_TAX_CLASSIFICATION_CODE",
        "LINE_INTENDED_USE",
        "PRODUCT_CATEGORY",
        "PRODUCT_FISC_CLASSIFICATION",
        "PRODUCT_TYPE",
        "TRX_BUSINESS_CATEGORY",
        "USER_DEFINED_FISC_CLASS",
        "ATTRIBUTE_CATEGORY",
        "ATTRIBUTE1",
        "ATTRIBUTE2",
        "ATTRIBUTE3",
        "ATTRIBUTE4",
        "ATTRIBUTE5",
        "ATTRIBUTE6",
        "ATTRIBUTE7",
        "ATTRIBUTE8",
        "ATTRIBUTE9",
        "ATTRIBUTE10",
        "ATTRIBUTE11",
        "ATTRIBUTE12",
        "ATTRIBUTE13",
        "ATTRIBUTE14",
        "ATTRIBUTE15",
        "ATTRIBUTE16",
        "ATTRIBUTE17",
        "ATTRIBUTE18",
        "ATTRIBUTE19",
        "ATTRIBUTE20",
        "ATTRIBUTE_DATE1",
        "ATTRIBUTE_DATE2",
        "ATTRIBUTE_DATE3",
        "ATTRIBUTE_DATE4",
        "ATTRIBUTE_DATE5",
        "ATTRIBUTE_DATE6",
        "ATTRIBUTE_DATE7",
        "ATTRIBUTE_DATE8",
        "ATTRIBUTE_DATE9",
        "ATTRIBUTE_DATE10",
        "ATTRIBUTE_NUMBER1",
        "ATTRIBUTE_NUMBER2",
        "ATTRIBUTE_NUMBER3",
        "ATTRIBUTE_NUMBER4",
        "ATTRIBUTE_NUMBER5",
        "ATTRIBUTE_NUMBER6",
        "ATTRIBUTE_NUMBER7",
        "ATTRIBUTE_NUMBER8",
        "ATTRIBUTE_NUMBER9",
        "ATTRIBUTE_NUMBER10",
        "ATTRIBUTE_TIMESTAMP1",
        "ATTRIBUTE_TIMESTAMP2",
        "ATTRIBUTE_TIMESTAMP3",
        "ATTRIBUTE_TIMESTAMP4",
        "ATTRIBUTE_TIMESTAMP5",
        "ATTRIBUTE_TIMESTAMP6",
        "ATTRIBUTE_TIMESTAMP7",
        "ATTRIBUTE_TIMESTAMP8",
        "ATTRIBUTE_TIMESTAMP9",
        "ATTRIBUTE_TIMESTAMP10",
        "FREIGHT_CARRIER",
        "MODE_OF_TRANSPORT",
        "SERVICE_LEVEL",
        "FINAL_DISCHARGE_LOCATION_CODE",
        "REQUESTED_SHIP_DATE",
        "PROMISED_SHIP_DATE",
        "REQUESTED_DELIVERY_DATE",
        "PROMISED_DELIVERY_DATE",
        "RETAINAGE_RATE",
        "INVOICE_MATCH_OPTION",
        "GLOBAL_ATTRIBUTE1",
        "GLOBAL_ATTRIBUTE_NUMBER1",
    ], start=1):
        is_req = col in {'NEED_BY_DATE', 'DESTINATION_TYPE_CODE', 'SHIP_TO_ORGANIZATION_CODE', 'INTERFACE_LINE_KEY', 'INTERFACE_LINE_LOCATION_KEY', 'SHIPMENT_NUM', 'SHIPPING_UOM_QUANTITY', 'SHIP_TO_LOCATION', 'RECEIPT_REQUIRED_FLAG'}
        await FBDIField(
            template_id=tpl.id,
            sheet_id=sheet_3.id,
            field_name=col,
            description=col.replace('_', ' ').title(),
            data_type="Character",
            required=1 if is_req else 0,
            sequence=seq,
        ).insert()

    sheet_4 = FBDISheet(
        template_id=tpl.id,
        sheet_name="PoDistributionsInterface",
        sequence=4,
        field_count=125,
    )
    await sheet_4.insert()
    for seq, col in enumerate([
        "INTERFACE_DISTRIBUTION_KEY",
        "INTERFACE_LINE_LOCATION_KEY",
        "DISTRIBUTION_NUM",
        "DELIVER_TO_LOCATION",
        "DELIVER_TO_PERSON_FULL_NAME",
        "DESTINATION_SUBINVENTORY",
        "AMOUNT_ORDERED",
        "SHIPPING_UOM_QUANTITY",
        "CHARGE_ACCOUNT_SEGMENT1",
        "CHARGE_ACCOUNT_SEGMENT2",
        "CHARGE_ACCOUNT_SEGMENT3",
        "CHARGE_ACCOUNT_SEGMENT4",
        "CHARGE_ACCOUNT_SEGMENT5",
        "CHARGE_ACCOUNT_SEGMENT6",
        "CHARGE_ACCOUNT_SEGMENT7",
        "CHARGE_ACCOUNT_SEGMENT8",
        "CHARGE_ACCOUNT_SEGMENT9",
        "CHARGE_ACCOUNT_SEGMENT10",
        "CHARGE_ACCOUNT_SEGMENT11",
        "CHARGE_ACCOUNT_SEGMENT12",
        "CHARGE_ACCOUNT_SEGMENT13",
        "CHARGE_ACCOUNT_SEGMENT14",
        "CHARGE_ACCOUNT_SEGMENT15",
        "CHARGE_ACCOUNT_SEGMENT16",
        "CHARGE_ACCOUNT_SEGMENT17",
        "CHARGE_ACCOUNT_SEGMENT18",
        "CHARGE_ACCOUNT_SEGMENT19",
        "CHARGE_ACCOUNT_SEGMENT20",
        "CHARGE_ACCOUNT_SEGMENT21",
        "CHARGE_ACCOUNT_SEGMENT22",
        "CHARGE_ACCOUNT_SEGMENT23",
        "CHARGE_ACCOUNT_SEGMENT24",
        "CHARGE_ACCOUNT_SEGMENT25",
        "CHARGE_ACCOUNT_SEGMENT26",
        "CHARGE_ACCOUNT_SEGMENT27",
        "CHARGE_ACCOUNT_SEGMENT28",
        "CHARGE_ACCOUNT_SEGMENT29",
        "CHARGE_ACCOUNT_SEGMENT30",
        "DESTINATION_CONTEXT",
        "PROJECT",
        "TASK",
        "PJC_EXPENDITURE_ITEM_DATE",
        "EXPENDITURE_TYPE",
        "EXPENDITURE_ORGANIZATION",
        "PJC_BILLABLE_FLAG",
        "PJC_CAPITALIZABLE_FLAG",
        "PJC_WORK_TYPE",
        "PJC_RESERVED_ATTRIBUTE1",
        "PJC_RESERVED_ATTRIBUTE2",
        "PJC_RESERVED_ATTRIBUTE3",
        "PJC_RESERVED_ATTRIBUTE4",
        "PJC_RESERVED_ATTRIBUTE5",
        "PJC_RESERVED_ATTRIBUTE6",
        "PJC_RESERVED_ATTRIBUTE7",
        "PJC_RESERVED_ATTRIBUTE8",
        "PJC_RESERVED_ATTRIBUTE9",
        "PJC_RESERVED_ATTRIBUTE10",
        "PJC_USER_DEF_ATTRIBUTE1",
        "PJC_USER_DEF_ATTRIBUTE2",
        "PJC_USER_DEF_ATTRIBUTE3",
        "PJC_USER_DEF_ATTRIBUTE4",
        "PJC_USER_DEF_ATTRIBUTE5",
        "PJC_USER_DEF_ATTRIBUTE6",
        "PJC_USER_DEF_ATTRIBUTE7",
        "PJC_USER_DEF_ATTRIBUTE8",
        "PJC_USER_DEF_ATTRIBUTE9",
        "PJC_USER_DEF_ATTRIBUTE10",
        "RATE",
        "RATE_DATE",
        "ATTRIBUTE_CATEGORY",
        "ATTRIBUTE1",
        "ATTRIBUTE2",
        "ATTRIBUTE3",
        "ATTRIBUTE4",
        "ATTRIBUTE5",
        "ATTRIBUTE6",
        "ATTRIBUTE7",
        "ATTRIBUTE8",
        "ATTRIBUTE9",
        "ATTRIBUTE10",
        "ATTRIBUTE11",
        "ATTRIBUTE12",
        "ATTRIBUTE13",
        "ATTRIBUTE14",
        "ATTRIBUTE15",
        "ATTRIBUTE16",
        "ATTRIBUTE17",
        "ATTRIBUTE18",
        "ATTRIBUTE19",
        "ATTRIBUTE20",
        "ATTRIBUTE_DATE1",
        "ATTRIBUTE_DATE2",
        "ATTRIBUTE_DATE3",
        "ATTRIBUTE_DATE4",
        "ATTRIBUTE_DATE5",
        "ATTRIBUTE_DATE6",
        "ATTRIBUTE_DATE7",
        "ATTRIBUTE_DATE8",
        "ATTRIBUTE_DATE9",
        "ATTRIBUTE_DATE10",
        "ATTRIBUTE_NUMBER1",
        "ATTRIBUTE_NUMBER2",
        "ATTRIBUTE_NUMBER3",
        "ATTRIBUTE_NUMBER4",
        "ATTRIBUTE_NUMBER5",
        "ATTRIBUTE_NUMBER6",
        "ATTRIBUTE_NUMBER7",
        "ATTRIBUTE_NUMBER8",
        "ATTRIBUTE_NUMBER9",
        "ATTRIBUTE_NUMBER10",
        "ATTRIBUTE_TIMESTAMP1",
        "ATTRIBUTE_TIMESTAMP2",
        "ATTRIBUTE_TIMESTAMP3",
        "ATTRIBUTE_TIMESTAMP4",
        "ATTRIBUTE_TIMESTAMP5",
        "ATTRIBUTE_TIMESTAMP6",
        "ATTRIBUTE_TIMESTAMP7",
        "ATTRIBUTE_TIMESTAMP8",
        "ATTRIBUTE_TIMESTAMP9",
        "ATTRIBUTE_TIMESTAMP10",
        "DELIVER_TO_PERSON_EMAIL_ADDR",
        "BUDGET_DATE",
        "PJC_CONTRACT_NUMBER",
        "PJC_FUNDING_SOURCE",
        "GLOBAL_ATTRIBUTE1",
    ], start=1):
        is_req = col in {'CHARGE_ACCOUNT_SEGMENT1', 'INTERFACE_LINE_LOCATION_KEY', 'DISTRIBUTION_NUM', 'SHIPPING_UOM_QUANTITY', 'CHARGE_ACCOUNT_SEGMENT2', 'INTERFACE_DISTRIBUTION_KEY', 'CHARGE_ACCOUNT_SEGMENT3'}
        await FBDIField(
            template_id=tpl.id,
            sheet_id=sheet_4.id,
            field_name=col,
            description=col.replace('_', ' ').title(),
            data_type="Character",
            required=1 if is_req else 0,
            sequence=seq,
        ).insert()

    return tpl

async def _repair_zero_field_templates() -> None:
    """One-time repair: seed standard Oracle Fusion fields for any template that
    was uploaded or manifested with 0 FBDIField records, then re-trigger mapping
    for any conversion linked to that template.

    Safe to call on every startup — auto_seed_if_empty is a no-op when fields
    already exist, and run_mapping_suggestions only runs when suggestion count
    is 0.
    """
    import logging
    log = logging.getLogger(__name__)

    from app.models.fbdi import FBDIField, FBDITemplate
    from app.routers.fbdi_seed import auto_seed_if_empty

    all_templates = await FBDITemplate.find_all().to_list()
    repaired_ids = []

    for tpl in all_templates:
        count = await FBDIField.find(FBDIField.template_id == tpl.id).count()
        if count == 0:
            seeded = await auto_seed_if_empty(tpl)
            if seeded:
                log.info(f"Startup repair: seeded {seeded} fields for '{tpl.name}'")
                repaired_ids.append(tpl.id)

    if not repaired_ids:
        return

    # Re-trigger mapping for any conversion linked to a repaired template
    from app.models.conversion import Conversion
    from app.models.mapping import MappingSuggestion
    from app.services.mapping_service import run_mapping_suggestions

    for tpl_id in repaired_ids:
        convs = await Conversion.find(Conversion.template_id == tpl_id).to_list()
        for conv in convs:
            if not conv.dataset_id:
                continue
            existing = await MappingSuggestion.find(
                MappingSuggestion.conversion_id == conv.id
            ).count()
            if existing == 0:
                try:
                    await run_mapping_suggestions(conv)
                    log.info(
                        f"Startup repair: ran mapping suggestions for conversion '{conv.name}'"
                    )
                except Exception as exc:
                    log.warning(f"Startup repair: mapping failed for '{conv.name}': {exc}")


async def _reseed_scm_om_templates() -> None:
    """Force-reseed all SCM and OM FBDI templates with the latest Oracle Fusion
    field schemas from STANDARD_FIELDS on every deploy.

    This replaces any stale / zero-field records so that the Templates page
    always reflects the current canonical field definitions after a redeploy.
    Templates in other modules are left untouched.
    """
    import logging
    log = logging.getLogger(__name__)

    from app.models.fbdi import FBDIField, FBDITemplate
    from app.routers.fbdi_seed import STANDARD_FIELDS, _schema_key_for, auto_seed_if_empty

    TARGET_MODULES = {"SCM", "OM", "scm", "om"}

    all_templates = await FBDITemplate.find_all().to_list()
    reseeded = 0

    for tpl in all_templates:
        if tpl.module not in TARGET_MODULES:
            continue
        key = _schema_key_for(tpl.name, tpl.business_object)
        if key is None:
            log.debug(f"_reseed_scm_om: no schema for '{tpl.name}' — skipped")
            continue
        seeded = await auto_seed_if_empty(tpl, force=True)
        if seeded:
            log.info(f"_reseed_scm_om: reseeded {seeded} fields for '{tpl.name}' (schema={key})")
            reseeded += 1

    if reseeded:
        log.info(f"_reseed_scm_om: refreshed {reseeded} SCM/OM templates with latest Oracle FBDI schemas")


async def run_seed() -> None:
    await _seed_admin()
    await _seed_dependencies()
    item_ds, so_ds = await _seed_datasets()
    item_tpl = await _seed_fbdi_template()
    so_tpl = await _seed_sales_order_template()
    await _seed_purchase_order_template()
    from app.seed.fbdi_manifest import seed_fbdi_manifest
    await seed_fbdi_manifest()
    await _seed_demo_engagement(item_ds, so_ds, item_tpl, so_tpl)
    await _repair_zero_field_templates()
    await _reseed_scm_om_templates()


if __name__ == "__main__":
    import asyncio

    async def _main():
        await init_db()
        await run_seed()
        print("Seed complete.")

    asyncio.run(_main())
