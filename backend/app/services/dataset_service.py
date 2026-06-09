"""Dataset upload + profiling service."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models.dataset import Dataset, DatasetColumnProfile
from app.parsers import parse_tabular, profile_dataframe


ALLOWED_DATASET_EXTS = {".csv", ".xlsx", ".xls"}


def save_upload(upload: UploadFile, subdir: str = "datasets") -> tuple[Path, str]:
    target_dir = settings.upload_path / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.filename or "upload").name
    target = target_dir / safe_name
    counter = 1
    while target.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        target = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    with open(target, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    upload.file.close()
    return target, target.name


def create_dataset_from_upload(
    db: Session, upload: UploadFile, name: str | None, description: str | None
) -> Dataset:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_DATASET_EXTS:
        raise ValueError(f"Unsupported file extension: {ext}")
    file_path, stored_name = save_upload(upload)

    df = parse_tabular(file_path, file_type=ext.lstrip("."))
    profiles = profile_dataframe(df)

    ds = Dataset(
        name=name or Path(upload.filename or stored_name).stem,
        description=description,
        file_name=stored_name,
        file_path=str(file_path),
        file_type=ext.lstrip("."),
        row_count=int(len(df)),
        column_count=int(len(df.columns)),
        status="profiled",
    )
    db.add(ds)
    db.flush()
    for prof in profiles:
        db.add(DatasetColumnProfile(dataset_id=ds.id, **prof))
    db.commit()
    db.refresh(ds)
    return ds


def get_dataset_preview(ds: Dataset, limit: int = 50) -> dict[str, Any]:
    df = parse_tabular(ds.file_path, file_type=ds.file_type)
    head = df.head(limit)
    return {
        "columns": list(head.columns.astype(str)),
        "rows": head.fillna("").to_dict(orient="records"),
        "total_rows": int(len(df)),
    }


# ─── Dataset-type auto-detection ─────────────────────────────────────────────

# Keyword hints keyed by business-object name.
# Each entry: (filename_keywords, column_keywords)
_OBJECT_HINTS: dict[str, tuple[list[str], list[str]]] = {
    "Item": (
        ["item", "sku", "product", "material", "catalog", "part"],
        ["item_num", "item_number", "sku", "part_number", "inventory_item",
         "item_desc", "uom", "unit_of_measure", "item_class", "item_type"],
    ),
    "Customer": (
        ["customer", "cust", "client", "buyer", "account"],
        ["customer_num", "customer_number", "cust_id", "account_num",
         "customer_name", "bill_to", "sold_to", "party_name"],
    ),
    "Supplier": (
        ["supplier", "vendor", "supp"],
        ["supplier_num", "vendor_id", "supplier_name", "vendor_name",
         "supplier_site", "payment_terms"],
    ),
    "Sales Order": (
        ["sales_order", "salesorder", "so_", "order_header"],
        ["order_number", "order_date", "customer_number", "order_type",
         "ordered_quantity", "selling_price", "ship_date", "order_status"],
    ),
    "Purchase Order": (
        ["purchase_order", "po_", "purchaseorder"],
        ["po_number", "supplier_number", "ordered_quantity",
         "need_by_date", "buyer_name", "po_line"],
    ),
    "BOM": (
        ["bom", "bill_of_material", "component"],
        ["assembly_item", "component_item", "component_quantity",
         "bom_type", "effectivity_date"],
    ),
    "On-Hand Balance": (
        ["onhand", "on_hand", "inventory", "balance", "stock"],
        ["organization_code", "item_number", "subinventory",
         "transaction_quantity", "lot_number"],
    ),
    "UOM": (
        ["uom", "unit_of_measure"],
        ["uom_code", "unit_of_measure_code", "description", "base_uom"],
    ),
    "Inventory Org": (
        ["inventory_org", "org_", "organization"],
        ["organization_code", "organization_name", "legal_entity", "location_code"],
    ),
}

import re as _re
_NORMALIZE = _re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _NORMALIZE.sub("_", s.lower()).strip("_")


def _kw_score(tokens: list[str], keywords: list[str]) -> float:
    hits = sum(1 for kw in keywords if any(kw in tok for tok in tokens))
    return hits / len(keywords) if keywords else 0.0


def detect_dataset_type(
    filename: str,
    column_names: list[str],
    templates: list,          # list of FBDITemplate ORM objects
) -> list[dict]:
    """Infer the most likely FBDI business object for a dataset.

    Returns up to 3 candidates sorted by descending confidence as
    [{template_id, template_name, business_object, confidence, reason}].
    The caller converts to JSON for the API response.
    """
    fname_tokens = [_norm(t) for t in _re.split(r"[\W_]+", filename) if t]
    col_tokens   = [_norm(c) for c in column_names]

    # Index templates by business_object for quick lookup
    by_obj: dict[str, object] = {}
    for tpl in templates:
        obj = (tpl.business_object or tpl.name or "").strip()
        if obj and obj not in by_obj:
            by_obj[obj] = tpl

    results: list[dict] = []
    for obj, (fname_kws, col_kws) in _OBJECT_HINTS.items():
        fname_s = _kw_score(fname_tokens, fname_kws)
        col_s   = _kw_score(col_tokens,   col_kws)
        confidence = round(min(1.0, fname_s * 0.45 + col_s * 0.55), 3)
        if confidence < 0.10:
            continue
        tpl = by_obj.get(obj)
        if not tpl:
            continue
        reasons: list[str] = []
        if fname_s >= 0.2:
            reasons.append(f"filename contains '{obj.lower()}' keywords")
        if col_s >= 0.2:
            reasons.append(f"{int(col_s * 100)}% column-name keyword match")
        results.append({
            "template_id":      tpl.id,
            "template_name":    tpl.name,
            "business_object":  obj,
            "confidence":       confidence,
            "reason":           "; ".join(reasons) if reasons else f"weak signal for {obj}",
        })

    results.sort(key=lambda x: -x["confidence"])
    return results[:3]
