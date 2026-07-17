# Trinamix Conversion Workbench — Session Handoff

A continuation brief. Paste the relevant parts into a new chat to pick up where we left off.

---

## 1. What the product is

**Trinamix Conversion Workbench** — an AI-assisted tool that converts legacy ERP/PLM master data (NetSuite, Infor SyteLine, Arena EBOS/Ratana Lee/Anaplan, Salesforce) into **Oracle Fusion FBDI** load files. Hosted on **Render** (2 GB paid instance), deployed by git push. It's being hardened for a NextPower client demo.

**Stack**
- Frontend: React + TypeScript + Vite + Tailwind (`frontend/src/…`, main pages `MappingReviewPage.tsx`, `ConversionDetailPage.tsx`).
- Backend: FastAPI + Beanie ODM over **MongoDB**, source files in **GridFS**. (`backend/app/…`)
- AI: Anthropic / OpenAI (model selector Haiku/Sonnet/Opus), used only as a last-resort residual mapper.
- Mapping precedence (each value comes from the highest layer that has it): **Golden record → Learnings → Mapping workbook → User rule → Deterministic → Default → AI (last)**.

**FBDI fan-out objects** (one dataset → many interface sheets/CSVs)
- **Customer Import** = 19-sheet `HZ_IMP…` load.
- **Item Import** = 17-sheet Product Hub load, main table `EGP_SYSTEM_ITEMS_INTERFACE` (399 cols); template file `FBDI_Templates/ItemImport_EGP_SYSTEM_ITEMS_INTERFACE.xlsm` (1365 fields total).
- **Supplier** = 6 objects: Supplier, Supplier Address, Supplier Site, Supplier Site Assignment, Supplier Contacts, Supplier Banks (`FBDI_Templates/[1-6]_Supplier*.xlsm`).

---

## 2. How to deploy (important)

- Deploy = run **`launch_git.bat`** (in the workspace root). It clears git locks, `git add -A`, commits with the message in the file, and `git push origin main` → Render auto-redeploys and **restarts**.
- I update the commit message in `launch_git.bat` before each deploy; the user runs it.
- **Startup seeds** run on every restart (`backend/app/main.py` → `_run_seeds_background`): FBDI templates, `ensure_customer_multisheet`, `ensure_item_multisheet`, mapping catalog, item field mappings, **supplier field mappings** (new), LOV backfill.

### Environment gotchas (don't re-learn these)
- **Flaky sandbox mount:** bash/Python frequently read a **stale/truncated** copy of recently-edited files → false `SyntaxError` (unclosed brace/string). The **Read/Grep/Edit tools are authoritative** (Windows side, what deploys). To confirm a real syntax error vs mount noise: the error is *consistent across retries AND the file is genuinely short*. When in doubt, verify the tail via the Read tool or parse a snippet off-mount in `/tmp`.
- **Render ~100s gateway timeout** surfaces as a misleading CORS/ERR_FAILED. Heavy work must be async/background or gated.
- Generation already runs as a **background job** (`operations._run_generation`, poll `generation-status`).

---

## 3. What was done this session

### A. Item conversion — made it actually produce the 18-sheet FBDI
The item conversions were generating a **flat single 26-column CSV** (the `itemmasterimport` fallback schema in `backend/app/routers/fbdi_seed.py`) instead of the real 17-sheet FBDI. Root cause + fixes:
- **`ensure_item_multisheet()`** (`template_seed_service.py`) — force-seeds the real 17-sheet Item template and re-points flat-template conversions onto it, clearing stale mappings. Mirrors the customer repair.
- **Broadened `_is_item`** — matches source-prefixed flats like `SyteLine ERP Item Import`, `NetSuite Item Master`, while excluding child objects (cost/categories/revisions/…).
- **Map-time self-heal** — `run_mapping_suggestions` now calls `ensure_item_multisheet()` at the start of **AI Auto Map**, so conversions created *between restarts* also get re-pointed (startup-only wasn't enough). ⟵ this was the final fix for the flat output.
- **Heavy-template AI gate** — `_heavy = len(targets) > 300`; AI Auto Map SKIPS the LLM residual for Item(1365)/Customer(1254) so the ~1200 sourceless EFF/structural slots don't blow the gateway ("not mapping at all" = 0 saved). Deterministic + learnings/gold only.
- **Over-population suppression extended to Item** — only `EGP_SYSTEM_ITEMS_INTERFACE` (backbone) always emits; the 16 child sheets emit only when a real source column maps in, else headers-only.
- **NULL-sentinel cleanse** — `_blank_null_sentinels` blanks whole-cell `NULL/N/A/NONE/#N/A` at generate (SyteLine source is ~38% literal NULL). Verified working (output NULL count now 0).
- **Hyphen-strip fix** — the REMOVE_HYPHEN auto-transform was corrupting `Item Class 50-1020→501020` and part numbers (`SH-2.10.RBK`). Now blocked for item/part/class/category/name/description/status/type; unchanged for Supplier/Customer/PO/Serial numbers. (Gold shows Item Class "Prefab-Build" — hyphen preserved.)
- **53 Item standard-field mappings seeded** (`item_field_mappings.json`) from the "000 NXT Item Field Mapping Document" — Arena EBOS/Ratana Lee/Anaplan, SyteLine, NetSuite → Item Number, Description, Primary UOM, Revision, Lifecycle Phase, Make or Buy, Item Class, Unit Weight, etc.

### B. Supplier — seeded analyst mappings + value-mapped the constants (latest)
From the **"Suppliers Field Mapping - Updated v3"** doc (NetSuite "SS Vendors" + Arena eBOS → 6 supplier objects, 645 fields, 65 Must-Have):
- **138 clean 1:1 mappings seeded** (`supplier_field_mappings.json`, `seed_supplier_field_mappings()`), all 6 objects, both sources. Transformation-flagged rows (address coalesce, phone parse, subsidiary→BU) intentionally excluded.
- **Tax Org Type / Supplier Type / Business Relationship** turned from forced constants into **value-mapped from source**: removed from `_AUTHORITATIVE` in `output_service.py`; `Business Relationship ← Vendor Approval Status` has a real VALUE_MAP (`Approved→SPEND_AUTHORIZED` else `PROSPECTIVE`). Constants stay in `_CONTROL_DEFAULTS` as fallback. Catalog seeder now honors per-row `rule_type`/`rule_config`.
- Earlier this engagement (already deployed): supplier control defaults corrected to CORPORATION/SUPPLIER/SPEND_AUTHORIZED; Supplier Number ← "Number"; matcher vocab so Taxpayer ID/Payment Method/Remittance Fax auto-map; exact-identity matcher bonus.
- Also earlier: the **Fixed Value** UI control in the Mapping Inspector (pin a constant into any field).

### C. EFF (Extensible Flexfields) — scoped, not built
Most NextPower **item** attributes migrate as **EFF** (`EGO_ITEM_INTF_EFF_B/_TL`, positional `ATTRIBUTE_CHAR/NUMBER/DATE/TIMESTAMP` slots keyed by `Attribute Group Code`), not standard columns. Full design in **`EFF_Support_Scope.md`**. **Blocked on a client input** — need the Manage-EFF export (group codes, segment positions, datatypes). Client-ask drafted in **`Client_Ask_Item_EFF_Export.md`**.

### D. Diagrams / deck
- Architecture + Process-flow diagrams in Trinamix slide style → **`Trinamix_Conversion_Workbench_Diagrams.pptx`** (2 slides, full-bleed).

---

## 4. Deployment state (verify at start of next session)

- **Confirmed live:** null-sentinel cleanse, broadened `_is_item`, heavy AI gate (commit `f0365d0`).
- **Pending push (run `launch_git.bat`):** the **map-time Item self-heal**, the **138 supplier mappings + `seed_supplier_field_mappings`**, and the **3-field value-map change**. The bat's commit message is already set for the supplier wave and `git add -A` bundles everything uncommitted.
- To check: `git log --oneline -3` and compare local vs `origin/main`.

---

## 5. Open items / next steps

1. **Confirm the latest deploy landed**, then re-test an Item and a Supplier conversion end to end (AI Auto Map → Generate → inspect the multi-sheet zip).
2. **Wrong dataset on the Arena item conversion** — it was bound to the SyteLine dataset (260 cols) not the Arena PLM file (58 cols). Fix in the UI: Source Dataset → Replace → the Arena file (`2,662 × 58`).
3. **EFF support** — get the client's Manage-EFF export (see `Client_Ask_Item_EFF_Export.md`), then build per `EFF_Support_Scope.md`. This is the single biggest gap for item fidelity.
4. **Supplier value crosswalks** — Tax Org Type ← Entity Type and Supplier Type ← Category need the real source-value → Oracle-code pairs (share distinct NetSuite Entity Type / Category values and I'll seed the crosswalks).
5. **Supplier transformation rows** (37 skipped): address Billing/Shipping coalesce, phone-string parsing, Primary Subsidiary → BU crosswalk — implement as rules if needed.
6. **Root cause worth fixing:** new conversions get a flat single-sheet template from `fbdi_seed.auto_seed_if_empty` (the `itemmasterimport`/`supplierimport` schemas). The self-heals repair Item; consider fixing the conversion-creation flow to route object files to the real bundled template directly (and do the same self-heal for Supplier/Customer at map time).

---

## 6. Key files

**Backend**
- `backend/app/services/output_service.py` — generate: fan-out, `_CONTROL_DEFAULTS`, `_AUTHORITATIVE`, `_blank_null_sentinels`, backbone suppression (`_is_item`/`_CUST_BACKBONE`), streaming write.
- `backend/app/services/mapping_service.py` — `run_mapping_suggestions` (map-time self-heal + `_heavy` gate).
- `backend/app/services/template_seed_service.py` — `ensure_customer_multisheet`, `ensure_item_multisheet`, `_BUNDLED`.
- `backend/app/services/catalog_seed_service.py` — `_seed_catalog_file`, `seed_item_field_mappings`, `seed_supplier_field_mappings`.
- `backend/app/ai/rule_based.py` — deterministic matcher (`SEMANTIC_DICT`, identity bonus, hyphen rule, email tokenization).
- `backend/app/routers/fbdi_seed.py` — the flat fallback schemas (`itemmasterimport` etc.) + `auto_seed_if_empty`.
- `backend/app/data/item_field_mappings.json`, `supplier_field_mappings.json` — seeded learnings.
- `backend/app/main.py` — startup seeds.

**Deliverables in workspace root**
- `launch_git.bat` (deploy), `EFF_Support_Scope.md`, `Client_Ask_Item_EFF_Export.md`, `Trinamix_Conversion_Workbench_Diagrams.pptx`, this file.

---

## 7. Client source/gold files (in uploads)
NetSuite vendor: `All Vendors - SS All Vendors - Phoenix.xlsx`. SyteLine item: `SyteLine ERP.xlsx`. Arena item: `EBOS - arena_ebos_item_attributes_7-6-2026 SB.xlsx`. Mapping docs: `000 NXT _ Item Field Mapping Document_SB.xlsx`, `Suppliers Field Mapping - Updated v3 1 (2) SB.xlsx`. Gold: `ItemImportTemplate (1) (1) 1.xlsm`, `NCR CustomerImportTemplate…`, `Customers Template BATCH 21…`.
