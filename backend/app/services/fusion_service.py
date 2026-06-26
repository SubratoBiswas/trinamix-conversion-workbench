"""Oracle Fusion Cloud target integration.

Covers three things the Load Management UI needs:
  * which FBDI **interface tables** each conversion's object loads into,
  * a real **Test Connection** against the Fusion REST API (basic auth), and
  * a real **load** via Oracle's ERP Integration Service `importBulkData`
    (uploads the FBDI zip to UCM and submits the import ESS job in one call).

The per-object ESS job metadata below uses canonical Oracle paths; if a job
name differs on a given pod it can be overridden per object, but the request
shape is the standard ERP Integration contract.
"""
from __future__ import annotations

import base64
import io
import logging
import zipfile
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

# ── business object → FBDI interface tables it populates in Fusion ───────────
FBDI_INTERFACE_TABLES: dict[str, list[str]] = {
    "UOM":              ["MTL_UOM_INTERFACE", "MTL_UOM_CLASS_INTERFACE", "MTL_UOM_CONVERSIONS_INTERFACE"],
    "Inventory Org":    ["MTL_INV_ORG_PARAMETERS_INT", "HR_ORGANIZATION_INTERFACE"],
    "Item Class":       ["MTL_ITEM_CATEGORIES_INTERFACE", "EGP_ITEM_CLASSES_INTERFACE"],
    "Item":             ["EGP_SYSTEM_ITEMS_INTERFACE", "EGP_ITEM_REVISIONS_INTERFACE", "EGP_ITEM_CATEGORIES_INTF"],
    "Customer":         ["HZ_IMP_PARTIES_T", "HZ_IMP_ACCOUNTS_T", "HZ_IMP_ACCT_SITES_T", "HZ_IMP_CONTACTPTS_T"],
    "Supplier":         ["POZ_SUPPLIERS_INT", "POZ_SUP_ADDRESSES_INT", "POZ_SUP_SITES_INT", "POZ_SUP_CONTACTS_INT"],
    "BOM":              ["BOM_BILL_OF_MTLS_INTERFACE", "BOM_INVENTORY_COMPS_INTERFACE"],
    "On-Hand Balance":  ["INV_TRANSACTIONS_INTERFACE", "MTL_TRANSACTIONS_INTERFACE"],
    "Sales Order":      ["DOO_ORDER_HEADERS_ALL_INT", "DOO_ORDER_LINES_ALL_INT"],
    "Purchase Order":   ["PO_HEADERS_INTERFACE", "PO_LINES_INTERFACE", "PO_DISTRIBUTIONS_INTERFACE"],
    # ── SCM children that load through a parent object's import job ───────────
    "UOM Class":        ["MTL_UOM_CLASS_INTERFACE"],
    "Customer Site":    ["HZ_IMP_ACCT_SITES_T", "HZ_IMP_CONTACTPTS_T"],
    "Supplier Site":    ["POZ_SUP_ADDRESSES_INT", "POZ_SUP_SITES_INT"],
    "Sales Order Line": ["DOO_ORDER_LINES_ALL_INT"],
    # ── SCM objects with their own import job ────────────────────────────────
    "Subinventory":     ["INV_SUBINVENTORIES_INTERFACE"],
    "Locator":          ["INV_LOCATORS_INTERFACE"],
    "Price List":       ["QP_INTERFACE_LIST_HEADERS", "QP_INTERFACE_LIST_LINES"],
    "Lot Number":       ["INV_LOT_NUMBERS_INTERFACE"],
    "Serial Number":    ["INV_SERIAL_NUMBERS_INTERFACE"],
}

# ── business object → ERP Integration import job metadata ────────────────────
# document_account = the UCM account the zip is staged to; JobName = ESS path,name.
FBDI_LOAD_META: dict[str, dict[str, str]] = {
    "UOM":            {"document_account": "scm$/item$/import$",      "job_name": "/oracle/apps/ess/scm/productHub/items/uom/,UnitOfMeasureImport"},
    "Inventory Org":  {"document_account": "scm$/inventory$/import$", "job_name": "/oracle/apps/ess/scm/inventory/setup/,InventoryOrgImport"},
    "Item Class":     {"document_account": "scm$/item$/import$",      "job_name": "/oracle/apps/ess/scm/productHub/items/itemClass/,ItemClassImport"},
    "Item":           {"document_account": "scm$/item$/import$",      "job_name": "/oracle/apps/ess/scm/productHub/items/itemImport/,ItemImportJob"},
    "Customer":       {"document_account": "hz$/customer$/import$",   "job_name": "/oracle/apps/ess/cdm/hz/dataImport/,CustomerImport"},
    "Supplier":       {"document_account": "prc$/supplier$/import$",  "job_name": "/oracle/apps/ess/prc/poz/supplierImport/,SupplierImport"},
    "BOM":            {"document_account": "scm$/bom$/import$",       "job_name": "/oracle/apps/ess/scm/inventory/bom/,BomImport"},
    "On-Hand Balance":{"document_account": "scm$/inventory$/import$", "job_name": "/oracle/apps/ess/scm/inventory/transactions/,OnHandQuantityImport"},
    "Sales Order":    {"document_account": "scm$/order$/import$",     "job_name": "/oracle/apps/ess/scm/doo/import/,ImportSalesOrders"},
    "Purchase Order": {"document_account": "prc$/po$/import$",        "job_name": "/oracle/apps/ess/prc/po/import/,ImportPurchaseOrders"},
    # ── children: submitted through the parent object's ESS import job ────────
    "UOM Class":      {"document_account": "scm$/item$/import$",      "job_name": "/oracle/apps/ess/scm/productHub/items/uom/,UnitOfMeasureImport"},
    "Customer Site":  {"document_account": "hz$/customer$/import$",   "job_name": "/oracle/apps/ess/cdm/hz/dataImport/,CustomerImport"},
    "Supplier Site":  {"document_account": "prc$/supplier$/import$",  "job_name": "/oracle/apps/ess/prc/poz/supplierImport/,SupplierImport"},
    "Sales Order Line":{"document_account": "scm$/order$/import$",    "job_name": "/oracle/apps/ess/scm/doo/import/,ImportSalesOrders"},
    # ── objects with their own ESS import job (job paths may need per-pod tuning)
    "Subinventory":   {"document_account": "scm$/inventory$/import$", "job_name": "/oracle/apps/ess/scm/inventory/setup/,SubinventoryImport"},
    "Locator":        {"document_account": "scm$/inventory$/import$", "job_name": "/oracle/apps/ess/scm/inventory/setup/,LocatorImport"},
    "Price List":     {"document_account": "scm$/pricing$/import$",   "job_name": "/oracle/apps/ess/scm/pricing/priceList/,PriceListImport"},
    "Lot Number":     {"document_account": "scm$/inventory$/import$", "job_name": "/oracle/apps/ess/scm/inventory/setup/,LotNumberImport"},
    "Serial Number":  {"document_account": "scm$/inventory$/import$", "job_name": "/oracle/apps/ess/scm/inventory/setup/,SerialNumberImport"},
}

_FUSION_REST = "/fscmRestApi/resources/11.13.18.05"

# A conversion's object can arrive as the template's display name ("Unit of
# Measure") OR the conversion target_object ("UOM"). Resolve both to the
# canonical key used by the maps above.
_OBJECT_ALIASES: dict[str, str] = {
    "uom": "UOM", "unit of measure": "UOM", "units of measure": "UOM",
    "inventory org": "Inventory Org", "inventory organization": "Inventory Org",
    "item class": "Item Class", "item catalog / class": "Item Class", "item class setup": "Item Class",
    "item": "Item", "item master": "Item", "item master conversion": "Item",
    "customer": "Customer", "customer master": "Customer",
    "supplier": "Supplier", "supplier master": "Supplier",
    "bom": "BOM", "bills of material": "BOM", "bill of materials": "BOM", "bom conversion": "BOM",
    "on-hand balance": "On-Hand Balance", "on hand balance": "On-Hand Balance",
    "on-hand inventory balances": "On-Hand Balance", "on-hand balance load": "On-Hand Balance",
    "sales order": "Sales Order", "open sales orders": "Sales Order", "sales order backlog": "Sales Order",
    "purchase order": "Purchase Order", "open purchase orders": "Purchase Order",
    # newly-wired SCM objects (children + own-job)
    "uom class": "UOM Class", "units of measure classes": "UOM Class", "unit of measure class": "UOM Class",
    "customer site": "Customer Site", "customer sites": "Customer Site",
    "customer sites (bill-to / ship-to)": "Customer Site",
    "supplier site": "Supplier Site", "supplier sites": "Supplier Site",
    "sales order line": "Sales Order Line", "open sales order lines": "Sales Order Line",
    "subinventory": "Subinventory", "subinventories": "Subinventory",
    "subinventories (storage locations)": "Subinventory",
    "locator": "Locator", "locators": "Locator", "locators (bin / rack / row)": "Locator",
    "price list": "Price List", "price lists": "Price List",
    "lot number": "Lot Number", "lot numbers": "Lot Number",
    "serial number": "Serial Number", "serial numbers": "Serial Number",
}


def resolve_object_key(business_object: Optional[str]) -> Optional[str]:
    """Map any of the object's names to the canonical map key (or None)."""
    if not business_object:
        return None
    if business_object in FBDI_INTERFACE_TABLES:
        return business_object
    return _OBJECT_ALIASES.get(business_object.strip().lower())


def interface_tables_for(business_object: Optional[str]) -> list[str]:
    return FBDI_INTERFACE_TABLES.get(resolve_object_key(business_object) or "", [])


def load_meta_for(business_object: Optional[str]) -> dict[str, str]:
    return FBDI_LOAD_META.get(resolve_object_key(business_object) or "", {})


def _password(conn) -> str:
    pw = conn.encrypted_password or ""
    return pw[6:] if pw.startswith("PLAIN:") else pw


async def test_fusion_connection(base_url: str, username: str, password: str) -> dict[str, Any]:
    """Basic-auth GET against the Fusion REST resource catalog. 200 = reachable
    with valid credentials; 401 = reached the pod but creds rejected."""
    url = base_url.rstrip("/") + _FUSION_REST + "/"
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            r = await client.get(url, auth=(username, password))
        if r.status_code == 200:
            return {"ok": True, "status": 200, "message": "Connected to Oracle Fusion REST API."}
        if r.status_code in (401, 403):
            return {"ok": False, "status": r.status_code, "message": "Reached Fusion, but the credentials were rejected."}
        return {"ok": False, "status": r.status_code, "message": f"Fusion returned HTTP {r.status_code}."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": None, "message": f"Could not reach Fusion: {exc}"}


async def load_to_fusion(conn, business_object: Optional[str], csv_bytes: bytes,
                         base_filename: str) -> dict[str, Any]:
    """Zip the FBDI CSV, base64 it, and submit Oracle ERP Integration
    `importBulkData` — which stages the file in UCM and runs the import job.
    """
    zip_name = (base_filename.rsplit(".", 1)[0] or "fbdi") + ".zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(base_filename, csv_bytes)
    content_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    meta = load_meta_for(business_object)
    if not meta:
        return {"ok": False, "status": None,
                "message": f"No Fusion import job is mapped for object '{business_object}'. "
                           f"Add it to FBDI_LOAD_META to enable loading.",
                "request_id": None}

    body = {
        "OperationName": "importBulkData",
        "DocumentContent": content_b64,
        "ContentType": "zip",
        "FileName": zip_name,
        "DocumentAccount": meta["document_account"],
        "JobName": meta["job_name"],
        "ParameterList": meta.get("parameter_list", ""),
        "NotificationCode": "10",
        "CallbackURL": "",
    }
    url = conn.base_url.rstrip("/") + _FUSION_REST + "/erpintegrations"
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            r = await client.post(url, json=body, auth=(conn.username, _password(conn)))
        ok = r.status_code in (200, 201)
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            data = {"raw": r.text[:500]}
        req_id = (data.get("ReqstId") or data.get("RequestId") or data.get("DocumentId")) if isinstance(data, dict) else None
        return {
            "ok": ok,
            "status": r.status_code,
            "message": "Submitted to Fusion ERP Integration." if ok else f"Fusion rejected the load (HTTP {r.status_code}).",
            "request_id": req_id,
            "response": data,
            "file_name": zip_name,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning(f"load_to_fusion failed: {exc}")
        return {"ok": False, "status": None, "message": f"Could not reach Fusion: {exc}", "request_id": None}
