"""Generate realistic Item Master + Sales Order CSVs with deliberate data
quality issues that exercise every transformation flavour the workbench
supports. Run once at build time:

    python scripts/generate_seed_csvs.py

Outputs are written to backend/app/seed/sample_files/.
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)  # deterministic — we want repeatable demo numbers

OUT = Path(__file__).resolve().parents[1] / "backend" / "app" / "seed" / "sample_files"
OUT.mkdir(parents=True, exist_ok=True)


# ─────── Item Master generation ───────

ITEM_HEADERS = [
    "ITEM_NUM",
    "ITEM_DESC",
    "LONG_DESC",
    "ITEM_CLASS",
    "ITEM_TYPE",
    "PRIMARY_UOM",
    "SECONDARY_UOM",
    "INVENTORY_ORG",
    "ITEM_STATUS",
    "EFFECTIVE_DATE",
    "EFFECTIVE_END_DATE",
    "CREATED_DATE",
    "WEIGHT",
    "WEIGHT_UOM",
    "LENGTH_CM",
    "WIDTH_CM",
    "HEIGHT_CM",
    "DIMENSION_UOM",
    "COLOR",
    "MATERIAL",
    "STD_COST",
    "AVG_COST",
    "LIST_PRICE",
    "CURRENCY",
    "COUNTRY_OF_ORIGIN",
    "HARMONIZED_CODE",
    "LEAD_TIME_DAYS",
    "MIN_ORDER_QTY",
    "REORDER_POINT",
    "SAFETY_STOCK",
    "MAKE_OR_BUY",
    "ABC_CLASS",
    "SUPPLIER_NAME",
    "CATEGORY_PATH",
]

# Lookup variations to seed crosswalk material
UOM_VARIANTS = ["EA", "PCS", "EACH", "Each", "ea", "PC"]
WEIGHT_UOM_VARIANTS = ["LB", "LBS", "KG", "KGS", "G", "GMS"]
DIM_UOM_VARIANTS = ["CM", "IN", "INCH", "Inches", "MM"]
STATUS_VARIANTS = ["A", "I", "Active", "Inactive", "X", "ACT"]
ITEM_TYPE_VARIANTS = ["FG", "RM", "COMP", "SUBASM", "FINISHED", "raw"]
ITEM_CLASS_VARIANTS = ["COMPONENTS", "Finished Goods", "ELECTRONICS", "PACKAGING", "RAW MATERIAL"]
INV_ORG_VARIANTS = ["M1", "M2", "PLANO_PLANT", "DALLAS_DC", "M-1"]
CURRENCY_VARIANTS = ["USD", "$", "USDOLLAR", "US$"]
COUNTRY_VARIANTS = ["US", "USA", "United States", "U.S.A.", "MX", "MEXICO", "Mexico", "CN", "China", "TW", "TAIWAN"]
MAKE_BUY_VARIANTS = ["M", "B", "Make", "Buy", "MAKE", "BUY"]
ABC_CLASSES = ["A", "B", "C"]

DESCRIPTORS = [
    "Industrial Bearing 6203 ZZ", "Hex Bolt M8x40", "Stainless Steel Washer", "Capacitor 100uF 25V",
    "Resistor 10k Ohm 1/4W", "PCB Assembly — Main Board", "Aluminum Heat Sink 40x40",
    "Cable Harness 12-pin", "Plastic Enclosure ABS Black", "LED Driver Module 24V",
    "Shaft Coupling 12mm", "Brushless DC Motor 24V", "Timing Belt T5 250mm",
    "Sensor Bracket Stainless", "O-Ring Buna-N 25mm", "Power Supply 250W ATX",
    "Pneumatic Cylinder 50mm", "Mounting Plate Steel", "Display Module 3.5\" TFT",
    "Connector RJ45 Shielded", "Gear Reducer 1:10", "Linear Slide 200mm",
    "Solenoid Valve 24VDC", "Coupling Flexible Jaw", "Heat Shrink Tubing 6mm",
    "Servo Driver 400W", "Pulley Aluminum 16T", "Encoder 1024 PPR",
    "Pressure Sensor 0-10 bar", "Filter Cartridge 5 micron",
]

INV_ORG_FUSION = ["M1", "M2", "M3", "PLANO_OU"]


def _random_date_format(d: datetime, allow_messy: bool = True) -> str:
    """Randomly format a date in one of several legacy variations to seed
    transformation work."""
    variants = ["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%y", "%d/%m/%Y", "%Y%m%d"]
    if allow_messy:
        variants += ["%b %d, %Y", "%Y-%m-%dT00:00:00"]
    fmt = random.choice(variants)
    return d.strftime(fmt)


def generate_item_master(rows: int = 60) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    base_dt = datetime(2020, 1, 1)
    used_numbers: set[str] = set()

    for i in range(1, rows + 1):
        item_num = f"ITM-{1000 + i:05d}-{random.choice(['A', 'B', 'C', 'D'])}"
        # Inject 3 dupes
        if i in (12, 27, 41):
            item_num = list(used_numbers)[3] if used_numbers else item_num
        used_numbers.add(item_num)

        descriptor = random.choice(DESCRIPTORS)
        # Add trailing spaces and weird casing in some descriptors
        if i % 7 == 0:
            descriptor = descriptor.upper() + "  "
        elif i % 11 == 0:
            descriptor = descriptor.lower()

        # Created date: 1-4 years ago
        created = base_dt + timedelta(days=random.randint(0, 1500))
        effective = created + timedelta(days=random.randint(0, 30))
        # 6 records have a future effective date — validation should flag
        if i % 10 == 3:
            effective = datetime(2027, 6, 1)
        # Some records: empty end date, some have valid future
        end_date_str = ""
        if random.random() < 0.4:
            end_date_str = _random_date_format(effective + timedelta(days=random.randint(365, 365 * 5)))

        weight = round(random.uniform(0.05, 50.0), 3)
        # 4 records have invalid negative weights
        if i in (5, 18, 33, 47):
            weight = -weight

        std_cost = round(random.uniform(2.0, 250.0), 2)
        avg_cost = round(std_cost * random.uniform(0.95, 1.10), 2)
        list_price = round(std_cost * random.uniform(1.4, 2.5), 2)

        # Some have currency symbol prefix
        cost_str = f"${std_cost}" if i % 8 == 0 else str(std_cost)

        # 5 records: blank ITEM_CLASS (required field issue)
        item_class = "" if i in (8, 22, 36, 49, 55) else random.choice(ITEM_CLASS_VARIANTS)

        # 4 records: blank PRIMARY_UOM (required field issue)
        primary_uom = "" if i in (15, 29, 38, 52) else random.choice(UOM_VARIANTS)

        # Harmonized code with mixed formats
        harmonized = random.choice([
            f"{random.randint(8000, 9999)}.{random.randint(10, 99)}.{random.randint(0, 99):02d}",
            str(random.randint(80000000, 99999999)),
            f"HS-{random.randint(80000000, 99999999)}",
        ])

        items.append({
            "ITEM_NUM": item_num,
            "ITEM_DESC": descriptor,
            "LONG_DESC": (descriptor + " — Plano warehouse stock") if random.random() < 0.6 else "",
            "ITEM_CLASS": item_class,
            "ITEM_TYPE": random.choice(ITEM_TYPE_VARIANTS),
            "PRIMARY_UOM": primary_uom,
            "SECONDARY_UOM": random.choice(UOM_VARIANTS) if random.random() < 0.4 else "",
            "INVENTORY_ORG": random.choice(INV_ORG_VARIANTS),
            "ITEM_STATUS": random.choice(STATUS_VARIANTS),
            "EFFECTIVE_DATE": _random_date_format(effective),
            "EFFECTIVE_END_DATE": end_date_str,
            "CREATED_DATE": _random_date_format(created),
            "WEIGHT": str(weight),
            "WEIGHT_UOM": random.choice(WEIGHT_UOM_VARIANTS),
            "LENGTH_CM": str(round(random.uniform(1, 200), 1)),
            "WIDTH_CM": str(round(random.uniform(1, 100), 1)),
            "HEIGHT_CM": str(round(random.uniform(1, 80), 1)),
            "DIMENSION_UOM": random.choice(DIM_UOM_VARIANTS),
            "COLOR": random.choice(["Black", "Silver", "White", "Red", "Blue", ""]),
            "MATERIAL": random.choice(["Stainless Steel", "Plastic ABS", "Aluminum 6061", "Carbon Steel", "Brass", ""]),
            "STD_COST": cost_str,
            "AVG_COST": str(avg_cost),
            "LIST_PRICE": str(list_price),
            "CURRENCY": random.choice(CURRENCY_VARIANTS),
            "COUNTRY_OF_ORIGIN": random.choice(COUNTRY_VARIANTS),
            "HARMONIZED_CODE": harmonized,
            "LEAD_TIME_DAYS": str(random.randint(1, 90)),
            "MIN_ORDER_QTY": str(random.choice([1, 5, 10, 25, 50, 100])),
            "REORDER_POINT": str(random.randint(10, 500)),
            "SAFETY_STOCK": str(random.randint(5, 200)),
            "MAKE_OR_BUY": random.choice(MAKE_BUY_VARIANTS),
            "ABC_CLASS": random.choice(ABC_CLASSES),
            "SUPPLIER_NAME": random.choice(["Acme Components", "Tier One Mfg", "GlobalParts SA", "Toyo Industries", ""]),
            "CATEGORY_PATH": random.choice([
                "/Components/Mechanical/Bearings",
                "Components > Electrical > Capacitors",
                "Finished Goods\\Industrial",
                "Raw Materials/Steel",
            ]),
        })
    return items


# ─────── Sales Order generation ───────

SO_HEADERS = [
    "ORDER_NUM",
    "ORDER_DATE",
    "ORDER_STATUS",
    "CUSTOMER_NUM",
    "CUSTOMER_NAME",
    "ORDER_TYPE",
    "LINE_NUM",
    "ITEM_NUM",                 # references Item Master
    "QUANTITY",
    "UOM",
    "UNIT_PRICE",
    "CURRENCY",
    "SHIP_TO_ORG",
    "REQUESTED_DATE",
    "PROMISED_DATE",
    "SOURCE_SYSTEM",
    "SALES_REP",
    "PAYMENT_TERMS",
    "FREIGHT_TERMS",
    "WAREHOUSE",
]


def generate_sales_orders(item_numbers: list[str], rows: int = 200) -> list[dict[str, str]]:
    """Sales orders that reference items by ITEM_NUM. ~75% reference real
    items; 25% reference unknown / future / typo items so the cascade failure
    story is visible after Item Master fails."""
    orders: list[dict[str, str]] = []
    customers = [
        ("CUST-1001", "Northwind Industries"),
        ("CUST-1002", "Acme Manufacturing"),
        ("CUST-1003", "Pacific Distributors"),
        ("CUST-1004", "Lone Star Logistics"),
        ("CUST-1005", "BlueRidge Components"),
        ("CUST-1006", "Summit Technology"),
    ]
    statuses = ["BOOKED", "SHIPPED", "CLOSED", "CANCELLED"]
    types = ["STD", "RMA", "DROPSHIP", "INTERNAL"]

    base_dt = datetime(2024, 1, 1)
    order_no = 200001
    line_no = 1
    current_order_lines = 0
    cur_order = None

    # Reference some bad item numbers — items that intentionally won't load
    # (the demo's data quality issues will cause those Item rows to fail).
    bad_items = [
        "ITM-99999-Z", "ITM-DELETED", "ITM-12345-Q",  # totally non-existent
    ]

    for i in range(rows):
        # Group lines by order — vary 1-5 lines per order
        if current_order_lines == 0:
            order_no += 1
            cust = random.choice(customers)
            cur_order = {
                "num": f"SO-{order_no}",
                "date": _random_date_format(base_dt + timedelta(days=random.randint(0, 365))),
                "status": random.choice(statuses),
                "cust_num": cust[0],
                "cust_name": cust[1],
                "type": random.choice(types),
                "rep": random.choice(["E. Vasquez", "M. Patel", "J. Lin", "R. Kim", "B. O'Connor"]),
                "warehouse": random.choice(["PLANO_DC", "DALLAS_DC", "M1", "M2"]),
            }
            current_order_lines = random.randint(1, 5)
            line_no = 1

        # Decide whether this line references a real or a bad item
        bad = (i % 7 == 0) or (random.random() < 0.18)
        if bad:
            item_num = random.choice(bad_items)
        else:
            item_num = random.choice(item_numbers)

        req = base_dt + timedelta(days=random.randint(60, 240))
        promised = req + timedelta(days=random.randint(-3, 14))

        orders.append({
            "ORDER_NUM": cur_order["num"],
            "ORDER_DATE": cur_order["date"],
            "ORDER_STATUS": cur_order["status"],
            "CUSTOMER_NUM": cur_order["cust_num"],
            "CUSTOMER_NAME": cur_order["cust_name"],
            "ORDER_TYPE": cur_order["type"],
            "LINE_NUM": str(line_no),
            "ITEM_NUM": item_num,
            "QUANTITY": str(random.randint(1, 250)),
            "UOM": random.choice(UOM_VARIANTS),
            "UNIT_PRICE": str(round(random.uniform(2, 400), 2)),
            "CURRENCY": random.choice(["USD", "$", "USD"]),
            "SHIP_TO_ORG": cur_order["warehouse"],
            "REQUESTED_DATE": _random_date_format(req),
            "PROMISED_DATE": _random_date_format(promised),
            "SOURCE_SYSTEM": "NETSUITE",
            "SALES_REP": cur_order["rep"],
            "PAYMENT_TERMS": random.choice(["NET30", "NET60", "NET 30", "Due on receipt", "PREPAID"]),
            "FREIGHT_TERMS": random.choice(["FOB Origin", "FOB Destination", "FOB", "DDP"]),
            "WAREHOUSE": cur_order["warehouse"],
        })

        line_no += 1
        current_order_lines -= 1

    return orders


# ─────── Write to disk ───────

def main() -> None:
    items = generate_item_master(rows=60)
    item_numbers = [it["ITEM_NUM"] for it in items if it["ITEM_NUM"]]
    orders = generate_sales_orders(item_numbers, rows=180)

    item_path = OUT / "legacy_item_master.csv"
    with open(item_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ITEM_HEADERS)
        w.writeheader()
        w.writerows(items)
    print(f"✓ {item_path} ({len(items)} rows × {len(ITEM_HEADERS)} cols)")

    so_path = OUT / "legacy_sales_orders.csv"
    with open(so_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SO_HEADERS)
        w.writeheader()
        w.writerows(orders)
    print(f"✓ {so_path} ({len(orders)} rows × {len(SO_HEADERS)} cols)")


if __name__ == "__main__":
    main()
