# Trinamix Conversion Workbench — Project Handoff

**Last updated:** June 25, 2026  
**Project:** Oracle EBS → Oracle Fusion Cloud migration tool  
**Repo:** `C:\Users\SubratoBiswas\trinamix-conversion-workbench`  
**Live app:** https://tx-conversion-workbench.onrender.com  
**Live backend:** https://trinamix-conversion-backend.onrender.com  
**Deploy:** Render (free tier) — push to `main` branch triggers auto-deploy  
**Push tool:** Run `launch_git.bat` in the repo root to stage, commit, and push  

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI + Beanie ODM (MongoDB Atlas) |
| Frontend | React + Vite + TypeScript |
| DB | MongoDB Atlas (`conversions`, `fbdi_templates`, `fbdi_fields`, `source_connections`, etc.) |
| EBS connectivity | `jaydebeapi` + Oracle JDBC driver at `/app/ojdbc11.jar` |
| Auth | JWT (stored in `localStorage` as `trinamix.token`) |

---

## Live EBS Connection (confirmed working)

- **Host:** `130.61.47.221:1521/prod`  
- **Oracle version:** 19c Enterprise Edition  
- **Auth:** `apps` user (db_basic)  
- **Visible tables:** 298  
- **Status:** Healthy — last tested Jun 24, 2026, 07:14 PM  
- **Stored as:** `SourceConnection` document in MongoDB with `system_type: "oracle_ebs"` and `last_test_ok: true`

---

## Key Architecture Concepts

### FBDI (File-Based Data Import)
Oracle Fusion bulk import format — transposed Excel: Row 1 = field names, Row 2 = descriptions, Row 3 = data types.

### `fbdi_seed.py` `STANDARD_FIELDS`
Dictionary keyed by lowercased template name (no spaces/dashes) → list of field definition dicts. Matched via `_schema_key_for()` partial string matching. 33 schemas total covering SCM + OM.

### `auto_seed_if_empty(tpl, force=False)`
Shared utility — inserts FBDIField records from STANDARD_FIELDS if template has 0 fields. `force=True` deletes and re-seeds. Also updates `required_field_count` on the template doc.

### `source_type` on Conversion
- `"ebs"` (default for ALL new conversions) — live Oracle EBS query at runtime  
- `"dataset"` — uploaded CSV/Excel file  
- **UI rule:** `dataset_id` presence controls which card shows (not `source_type`). If `dataset_id` is set → show uploaded file card. If `dataset_id` is null → show EBS LIVE card.

### EBS column fetching for AI mapping
`_source_columns_for_ebs(table_name)` in `mapping_service.py`:
1. Finds `SourceConnection` where `system_type == "oracle_ebs"` AND `last_test_ok == True`
2. Builds JDBC URL via `_jdbc_url_from_conn(conn)` (reads `base_url` first, falls back to host/port/service_name)
3. Queries `ALL_TAB_COLUMNS` scoped to `conn.username` (APPS) for the given table name
4. Returns `SourceColumn` objects used by the AI mapping engine

---

## All Files Changed (this session)

### Backend

#### `backend/app/models/conversion.py`
- Added `source_type: str = "ebs"` (default **ebs** — all new conversions start in EBS mode)
- Added `ebs_table_hint: Optional[str] = None` (e.g. `"MTL_UNITS_OF_MEASURE"`)

#### `backend/app/schemas/conversion.py`
- Added `source_type` and `ebs_table_hint` to `ConversionUpdate` and `ConversionOut`

#### `backend/app/routers/conversions.py`
- Updated `_auto_map` to allow EBS mode (no `dataset_id` required when `source_type == "ebs"`)
- Added `POST /api/conversions/project/{project_id}/use-ebs-source` endpoint:
  - Imports `MODULES` from `fusion_modules.py` (NOT `ALL_MODULES` — that name doesn't exist)
  - Parses first EBS table from `source_extracts["oracle_ebs"]` hint per conversion's `target_object`
  - Sets `source_type="ebs"`, `dataset_id=None`, `ebs_table_hint=<table>` on all conversions in project
  - Triggers `_auto_map` background task for each

#### `backend/app/services/mapping_service.py`
- Added `_source_columns_for_ebs(table_name)` function
- Fixed query: `SourceConnection.system_type == "oracle_ebs"` and `SourceConnection.last_test_ok == True` (NOT `source_type`/`status` — those fields don't exist on the model)
- Uses `_jdbc_url_from_conn(conn)` from `discovery.py` for URL construction
- Queries `ALL_TAB_COLUMNS` with both `table_name` and `owner` filters
- Updated `run_mapping_suggestions` to dispatch on `source_type`: EBS → `_source_columns_for_ebs`, dataset → `_source_columns_for`

#### `backend/app/routers/mapping.py`
- `_require_conversion`: now allows EBS conversions without `dataset_id`
- Pattern: `if not c.template_id or (not is_ebs and not c.dataset_id)`

#### `backend/app/routers/quality.py`
- `profile_cleansing`: removed hard block on missing `dataset_id` for EBS mode

#### `backend/app/routers/operations.py`
- `generate_output`: EBS-mode conversions allowed through
- `output_preview`: same
- Dataflow `ai_auto_map` step: same guard fix

#### `backend/app/routers/fbdi_seed.py` (917 lines)
- Expanded from 18 → 33 canonical Oracle FBDI schemas
- 15 new schemas: itemrelationshipsimport, itemcostimport, itemcategoryimport, routingimport, itemrevisionsimport, itemspecimport, catalogimport, counttypesimport, discountimport, backorderimport, dropshipmentimport, customerreturnimport, commissionimport, shipmentimport, orderholdimport
- Aliases: `itemclassesimport`, `invonhandquantitiesimport`, `orderimport`
- `auto_seed_if_empty` now accepts `force=True`, updates `required_field_count`

#### `backend/app/seed/seed_data.py`
- Added `_reseed_scm_om_templates()` — force-reseeds ALL SCM/OM templates on every startup
- Called at end of `run_seed()` after `_repair_zero_field_templates()`

#### `backend/app/fusion_modules.py`
- Exhaustive Oracle Fusion module + conversion catalog
- Exported as `MODULES` (tuple) — **NOT** `ALL_MODULES`

### Frontend

#### `frontend/src/types/index.ts`
- `Conversion` interface: added `source_type?: string` and `ebs_table_hint?: string | null`

#### `frontend/src/api/index.ts`
- `ConversionsApi.switchProjectToEbs(projectId)` → `POST /conversions/project/{id}/use-ebs-source`

#### `frontend/src/pages/ConversionDetailPage.tsx`
- `loadAll`: loads dataset only if `c.dataset_id` is set (not gated on source_type)
- Source card: shows EBS LIVE card when `!conv.dataset_id`, dataset card otherwise
- EBS card: green pulsing LIVE badge, shows `ebs_table_hint` with monospace font

#### `frontend/src/pages/ProjectOverviewPage.tsx`
- Added `ebsBusy` state and **⚡ Use EBS Source** button in Conversion Objects card header
- Source column: shows green EBS pill with table name when `!c.dataset_id`
- Button works on ALL projects (not just EBS2)

---

## Trinamix EBS 2 Project

**URL:** https://tx-conversion-workbench.onrender.com/projects/6a3c16822018985578997b6f  
**10 conversions** — currently still have uploaded datasets linked (need to click "⚡ Use EBS Source" after deploy)

| # | Conversion | FBDI Template | EBS Table |
|---|-----------|---------------|-----------|
| 1 | Units of Measure | UomImport | MTL_UNITS_OF_MEASURE |
| 2 | Inventory Organization | InventoryOrgImport | MTL_PARAMETERS |
| 3 | Item Catalog / Class | ItemClassImport | MTL_ITEM_CATALOG_GROUPS_B |
| 4 | Item Master | Item Master (SCM Items) | MTL_SYSTEM_ITEMS_B |
| 5 | Customer Master | ArCustomerImport | HZ_PARTIES |
| 6 | Supplier Master | ApSuppliersImport | AP_SUPPLIERS |
| 7 | Bills of Material | BomImport | BOM_BILL_OF_MATERIALS |
| 8 | On-Hand Inventory Balances | OnHandBalanceImport | MTL_ONHAND_QUANTITIES_DETAIL |
| 9 | Open Sales Orders | Sales Order Headers (OM) | OE_ORDER_HEADERS_ALL |
| 10 | Open Purchase Orders | PurchaseOrderImport | PO_HEADERS_ALL |

---

## Deployment Procedure

1. Run `launch_git.bat` from `C:\Users\SubratoBiswas\trinamix-conversion-workbench`
2. Wait ~2-3 min for Render to build and deploy
3. Backend auto-runs `run_seed()` on startup which includes `_reseed_scm_om_templates()`

---

## Pending / Next Steps

1. **Run `launch_git.bat`** — the latest fixes (mapping.py, quality.py, operations.py, conversions.py fix for MODULES name) need to be pushed
2. **Click "⚡ Use EBS Source"** on Trinamix EBS 2 project to switch all 10 conversions from uploaded datasets to live EBS
3. **Click "AI Auto Map"** on each conversion — will now query `ALL_TAB_COLUMNS` from live EBS and suggest mappings against FBDI template fields
4. **Approve mappings** in Mapping Review page
5. **Generate Output** — produces FBDI-format CSV/Excel ready for Oracle Fusion import

### Known Limitations (future work)
- `Run Cleansing` and `Generate Output` for EBS mode currently run against `None` dataset — the output service needs to be updated to fetch actual rows from EBS via JDBC for EBS-mode conversions
- `/rules/preview` endpoint still requires `dataset_id` (transformation rule preview needs file data)
- `required_field_count` was previously showing "0" for some templates — fixed by `_reseed_scm_om_templates()` on startup

---

## Common Bugs Already Fixed (do not re-introduce)

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| "Network Error" on Use EBS Source | `ALL_MODULES` doesn't exist in fusion_modules.py | Changed to `MODULES` |
| EBS mapping silently returns empty | Query used `source_type`/`status` fields that don't exist on SourceConnection model | Changed to `system_type`/`last_test_ok` |
| Wrong JDBC URL for EBS columns | Used `conn.database` (field doesn't exist) | Now uses `_jdbc_url_from_conn(conn)` |
| "Conversion needs both dataset and template" on AI Auto Map | `_require_conversion` required `dataset_id` unconditionally | Now checks `source_type` first |
| CORS error on use-ebs-source | Endpoint crashed before CORS middleware attached headers (ImportError on ALL_MODULES) | Fixed import name |
| "0 required" on FBDI templates | `auto_seed_if_empty` never set `required_field_count` | Now sets it after seeding |
| duplicate DiscoveryApi in index.ts | Two exports of same name | Deduplicated |

---

## Key File Locations

```
backend/
  app/
    models/
      conversion.py       ← source_type + ebs_table_hint fields
      v10.py              ← SourceConnection model (system_type, last_test_ok, base_url, etc.)
    routers/
      conversions.py      ← use-ebs-source endpoint; uses MODULES (not ALL_MODULES)
      mapping.py          ← _require_conversion; EBS guard fix
      quality.py          ← profile_cleansing; EBS guard fix
      operations.py       ← generate_output, output_preview, dataflow; EBS guard fixes
      discovery.py        ← _jdbc_url_from_conn(); test_connection(); _live_oracle_discovery()
      fbdi_seed.py        ← STANDARD_FIELDS (33 schemas); auto_seed_if_empty(force=)
    schemas/
      conversion.py       ← ConversionUpdate + ConversionOut with source_type/ebs_table_hint
    seed/
      seed_data.py        ← _reseed_scm_om_templates() runs on every startup
    services/
      mapping_service.py  ← _source_columns_for_ebs(); run_mapping_suggestions()
    fusion_modules.py     ← MODULES tuple (exported as MODULES, not ALL_MODULES)

frontend/
  src/
    api/
      client.ts           ← axios instance; baseURL = VITE_API_URL + /api
      index.ts            ← ConversionsApi.switchProjectToEbs()
    pages/
      ConversionDetailPage.tsx   ← EBS LIVE source card; !conv.dataset_id controls display
      ProjectOverviewPage.tsx    ← ⚡ Use EBS Source button; source column EBS badge
    types/
      index.ts            ← Conversion interface with source_type + ebs_table_hint
```
