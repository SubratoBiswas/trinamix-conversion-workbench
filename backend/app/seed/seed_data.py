"""Seed data: admin user + demo dataset + FBDI Item template + dependencies + sample project.

Idempotent: safe to call multiple times (it checks before inserting).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, init_db
from app.models.conversion import Conversion
from app.models.dataset import Dataset, DatasetColumnProfile
from app.models.dependency import Dependency
from app.models.fbdi import FBDIField, FBDISheet, FBDITemplate
from app.models.project import Project
from app.models.user import User
from app.parsers import parse_fbdi_template, parse_tabular, profile_dataframe
from app.services.auth_service import hash_password


SEED_DIR = Path(__file__).parent / "sample_files"


# Edges based on real Oracle conversion order: master data must precede transactions
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


def _seed_admin(db: Session) -> User:
    user = db.query(User).filter(User.email == settings.ADMIN_EMAIL).first()
    if user:
        return user
    user = User(
        name=settings.ADMIN_NAME,
        email=settings.ADMIN_EMAIL,
        role="admin",
        password_hash=hash_password(settings.ADMIN_PASSWORD),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_dependencies(db: Session) -> None:
    if db.query(Dependency).count() > 0:
        return
    for src, tgt, rtype, desc in SEEDED_DEPENDENCIES:
        db.add(Dependency(source_object=src, target_object=tgt, relationship_type=rtype, description=desc))
    db.commit()


def _seed_one_dataset(
    db: Session, csv_filename: str, name: str, description: str
) -> Dataset | None:
    """Seed a single CSV file as a Dataset (idempotent on `name`)."""
    src_csv = SEED_DIR / csv_filename
    if not src_csv.exists():
        return None
    existing = db.query(Dataset).filter(Dataset.name == name).first()
    if existing:
        return existing

    dest_dir = settings.upload_path / "datasets"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src_csv.name
    if not dest.exists():
        shutil.copy2(src_csv, dest)

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
    db.add(ds)
    db.flush()
    for prof in profiles:
        db.add(DatasetColumnProfile(dataset_id=ds.id, **prof))
    db.commit()
    db.refresh(ds)
    return ds


def _seed_datasets(db: Session) -> tuple[Dataset | None, Dataset | None]:
    """Seed both the Item Master and Sales Order legacy extracts."""
    item_ds = _seed_one_dataset(
        db,
        csv_filename="legacy_item_master.csv",
        name="Legacy Item Master Extract",
        description=(
            "60-row legacy NetSuite Item extract: 34 columns, intentional data "
            "quality issues across dates, UOMs, currencies, country codes, and "
            "duplicate keys — exercises every transformation flavour."
        ),
    )
    so_ds = _seed_one_dataset(
        db,
        csv_filename="legacy_sales_orders.csv",
        name="Legacy Sales Order Extract",
        description=(
            "180-row legacy NetSuite Sales Order extract referencing the Item "
            "Master by ITEM_NUM. Contains references to items that fail in the "
            "upstream Item conversion — surfaces the dependency cascade visibly."
        ),
    )
    # Backwards-compat: also seed the original tiny extract if it exists,
    # so older saved projects that referenced it still resolve.
    _seed_one_dataset(
        db,
        csv_filename="legacy_item_extract.csv",
        name="Legacy Item Extract (Demo)",
        description="Original 8-column quick-start sample.",
    )
    return item_ds, so_ds


def _seed_fbdi_template(db: Session) -> FBDITemplate | None:
    src = SEED_DIR / "ScpItemImportTemplate.xlsm"
    if not src.exists():
        return None
    existing = db.query(FBDITemplate).filter(FBDITemplate.name == "Item Master (SCM Items)").first()
    if existing:
        return existing

    dest_dir = settings.upload_path / "fbdi"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists():
        shutil.copy2(src, dest)

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
    db.add(tpl)
    db.flush()

    sheet_id_by_name: dict[str, int] = {}
    for s in parsed["sheets"]:
        sheet = FBDISheet(
            template_id=tpl.id,
            sheet_name=s["sheet_name"],
            sequence=s["sequence"],
            field_count=s["field_count"],
        )
        db.add(sheet)
        db.flush()
        sheet_id_by_name[s["sheet_name"]] = sheet.id

    for f in parsed["fields"]:
        sheet_id = sheet_id_by_name.get(f.pop("sheet_name", ""))
        if sheet_id is None:
            continue
        db.add(FBDIField(template_id=tpl.id, sheet_id=sheet_id, **f))
    db.commit()
    db.refresh(tpl)
    return tpl


def _seed_sales_order_template(db: Session) -> FBDITemplate | None:
    """Seed a Sales Order FBDI template stub with hand-crafted fields so the
    Sales Order conversion has a real (if simplified) target to map against."""
    existing = db.query(FBDITemplate).filter(
        FBDITemplate.name == "Sales Order Headers (OM)"
    ).first()
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
    db.add(tpl)
    db.flush()

    sheet = FBDISheet(template_id=tpl.id, sheet_name="OrderHeaders", sequence=1, field_count=18)
    db.add(sheet)
    db.flush()

    # Hand-crafted Sales Order target fields — short list but covers the
    # mapping types the user will demo.
    fields = [
        # (field_name, description, data_type, max_length, required, sequence, sample)
        ("OrderNumber",     "Source order number",          "Character", 50,  True,  1, "SO-200001"),
        ("OrderType",       "Order transaction type",        "Character", 30,  True,  2, "STANDARD"),
        ("OrderDate",       "Order header date",             "Date",      None,True,  3, "2024-07-01"),
        ("OrderStatus",     "Order header status",           "Character", 30,  False, 4, "BOOKED"),
        ("CustomerNumber",  "Sold-to customer party number", "Character", 50,  True,  5, "1001"),
        ("CustomerName",    "Sold-to customer party name",   "Character", 250, False, 6, "Northwind Industries"),
        ("InventoryItemNumber","Item number being ordered",  "Character", 100, True,  7, "AS54888"),
        ("LineNumber",      "Order line number",             "Number",    None,True,  8, "1"),
        ("OrderedQuantity", "Quantity ordered",              "Number",    None,True,  9, "10"),
        ("UnitOfMeasureCode","Order line UOM",               "Character", 10,  True,  10,"Ea"),
        ("UnitSellingPrice","Selling price per unit",        "Number",    None,True,  11,"125.00"),
        ("CurrencyCode",    "Order currency code",           "Character", 3,   True,  12,"USD"),
        ("RequestShipDate", "Customer requested ship date",  "Date",      None,False, 13,"2024-08-01"),
        ("PromisedShipDate","Promised ship date",            "Date",      None,False, 14,"2024-08-05"),
        ("ShipFromOrgCode", "Source inventory organisation", "Character", 18,  True,  15,"M1"),
        ("PaymentTerms",    "Customer payment terms",        "Character", 30,  False, 16,"Net 30"),
        ("FreightTerms",    "Freight terms code",            "Character", 30,  False, 17,"FOB Origin"),
        ("SourceSystem",    "Originating source system",     "Character", 30,  False, 18,"NETSUITE"),
    ]
    for f in fields:
        db.add(FBDIField(
            template_id=tpl.id,
            sheet_id=sheet.id,
            field_name=f[0],
            description=f[1],
            data_type=f[2],
            max_length=f[3],
            required=int(f[4]),
            sequence=f[5],
            sample_value=f[6],
        ))
    db.commit()
    db.refresh(tpl)
    return tpl


def _seed_environments(db: Session, project) -> None:
    """Create the standard DEV/QA/UAT/PROD environment ladder for a project."""
    from app.models.environment import Environment, DEFAULT_ENVIRONMENTS
    if db.query(Environment).filter(Environment.project_id == project.id).count() > 0:
        return
    for env in DEFAULT_ENVIRONMENTS:
        db.add(Environment(
            project_id=project.id,
            name=env["name"],
            description=env["description"],
            sort_order=env["order"],
            color=env["color"],
            sox_controlled=1 if env["name"] == "PROD" else 0,
        ))
    db.commit()


def _seed_demo_engagement(
    db: Session,
    item_ds: Dataset | None,
    so_ds: Dataset | None,
    item_tpl: FBDITemplate | None,
    so_tpl: FBDITemplate | None,
) -> None:
    """Seed the demo engagement with two fully-bound conversions (Item Master
    and Sales Orders) plus planned placeholders for the rest."""
    from datetime import date, datetime, time as dtime

    if db.query(Project).filter(
        Project.name == "Trinamix → Oracle SCM Cloud Phase 1"
    ).first():
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
    db.add(proj)
    db.flush()

    _seed_environments(db, proj)

    # Conversion #1 — Item Master, fully bound to real seeded data.
    if item_ds is not None and item_tpl is not None:
        db.add(Conversion(
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
        ))

    # Conversion #2 — Sales Order, fully bound. Will fail-cascade some rows
    # because legacy_sales_orders.csv references item numbers from the Item
    # extract — when those upstream items fail, dependent SO rows fail too.
    if so_ds is not None and so_tpl is not None:
        db.add(Conversion(
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
        ))

    # Other conversions — planned placeholders.
    PLANNED = [
        ("UOM Master",                  "UOM",             10, "loaded"),
        ("Inventory Organization",      "Inventory Org",   15, "loaded"),
        ("Item Class Setup",            "Item Class",      20, "loaded"),
        ("Customer Master",             "Customer",        40, "planning"),
        ("Supplier Master",             "Supplier",        50, "planning"),
        ("BOM Conversion",              "BOM",             60, "planning"),
        ("On-Hand Balance Load",        "On-Hand Balance", 70, "planning"),
        ("Open Purchase Orders",        "Purchase Order",  90, "planning"),
    ]
    for name, obj, order, status in PLANNED:
        db.add(Conversion(
            project_id=proj.id,
            name=name,
            target_object=obj,
            planned_load_order=order,
            status=status,
            created_by=settings.ADMIN_EMAIL,
        ))
    db.commit()


def run_seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        _seed_admin(db)
        _seed_dependencies(db)
        item_ds, so_ds = _seed_datasets(db)
        item_tpl = _seed_fbdi_template(db)
        so_tpl = _seed_sales_order_template(db)
        # Comprehensive 100+ template manifest
        from app.seed.fbdi_manifest import seed_fbdi_manifest
        seed_fbdi_manifest(db)
        _seed_demo_engagement(db, item_ds, so_ds, item_tpl, so_tpl)
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
    print("Seed complete.")
