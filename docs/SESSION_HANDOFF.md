# Trinamix Conversion Workbench — Session Handoff

A running log of everything built/changed in this session, plus the context a new
chat needs to continue. Hand this file to the next session.

---

## 1. What the app is

An **Oracle EBS / multi-ERP → Oracle Fusion Cloud data-migration workbench**.
Users connect to a source (live Oracle EBS over JDBC, or uploaded file extracts),
map source columns to Fusion **FBDI** target fields with AI assistance, apply
transformation rules/crosswalks, generate FBDI files, and either **download them
for manual upload** or **load them directly to Fusion** via the ERP Integration
Service. Includes monitoring, error traceback, dependency graph, governance.

- **Frontend:** React 18 + TypeScript + Vite + Tailwind, React Router, axios,
  Zustand, Recharts, ReactFlow, TanStack Table, lucide-react.
- **Backend:** Python 3.12, FastAPI, Beanie ODM (MongoDB Atlas via Motor),
  Pydantic, pandas, openpyxl, httpx, jaydebeapi + JPype + Oracle ojdbc11 (EBS JDBC).
- **DB:** MongoDB Atlas.
- **Hosting:** Render — backend = Docker web service (`trinamix-backend`),
  frontend = static site (`trinamix-frontend`). Free tier (cold starts,
  queued/one-at-a-time deploys).
- **Live URLs:** frontend `https://tx-conversion-workbench.onrender.com`,
  backend `https://trinamix-conversion-backend.onrender.com` (health at `/api/health`).
- **Repo:** `https://github.com/SubratoBiswas/trinamix-conversion-workbench` (branch `main`).
- **Login:** admin@trinamix.com / admin123 (seed admin).
- A full architecture doc is in `docs/Trinamix_Conversion_Workbench_Architecture.docx`.

---

## 2. How deploys work (IMPORTANT for the next session)

There is **no CI**. Deploys happen by running a batch file that stages specific
files, commits, and pushes; Render auto-deploys on push.

1. Edit files under `C:\Users\SubratoBiswas\trinamix-conversion-workbench\`.
2. Edit **`launch_git.bat`** (repo root): add each changed file to its `git add`
   list, and set the commit message.
3. Run it via the Windows **Run** dialog (computer-use): open "Run", enter
   `C:\Users\SubratoBiswas\trinamix-conversion-workbench\launch_git.bat`, click OK.
   A "GitPush" PowerShell window shows the commit + push result.
4. Backend redeploys ~4–5 min; frontend static ~2–3 min. Verify a push reached
   origin with `git fetch origin main && git log origin/main --oneline`.

**Sandbox caveat:** the Linux bash sandbox serves a **stale/truncated** copy of
recently-edited files, so `vite build` / full `py_compile` / `esbuild` on the whole
file often fail with bogus "unexpected end of file" mid-line. Workaround used all
session: **isolate-check** new code by extracting the changed function/JSX into a
`/tmp` fixture and parsing that with a `/tmp`-installed `esbuild` (JS/TS) or
`py_compile` (Python). The host files (edited via the Read/Edit/Write tools) are
correct; only the sandbox mirror lags. `npm`/`pip` platform binaries in the
mounted `node_modules` are Windows, so install fresh in the sandbox when needed.

---

## 3. Work completed this session (by feature, with key commits)

### 3.1 Mapping Review
- **Per-conversion delete** with cascade (mappings/rules/crosswalks/outputs/load-runs). `9fbe991`
- **Resilience**: conversion-list load wrapped so a slow/timed-out backend shows
  an error + Retry instead of an infinite spinner / uncaught AxiosError. `0368765`
- **Project-wise landing**: no longer auto-jumps into the first conversion (which
  caused a misleading "No FBDI template linked"). Shows an engagement dropdown +
  clickable conversion cards; opening one drills into the canvas; a "Mapping list"
  button returns. `b3d3cca`
- **Inline value-mappings (crosswalk) panel** in the selected-field inspector —
  list/add/remove crosswalks scoped to that target object/field, reading/writing
  the same Crosswalk Library. `3180f9c`

### 3.2 Fusion connection, load, verification
- **Configure Fusion modal**: warns when a launchpad/SSO URL (`fa-launchpad` /
  `?params=`) is pasted instead of the REST host. `3b3b80f`
- **Load verification + processing UX**: poll ERP Integration `getESSJobStatus`;
  per-run "Check status" (Succeeded/Warning/Error/Running); store request id on
  each LoadRun; Load Runs table; per-object "View in Fusion" + work-area hint;
  full-screen submit spinner. `ec6d850`
- **Surface results**: `LoadRunOut` was stripping the new fields → added them; a
  Load result summary card (interface tables, request id, where to verify, live
  status). `200e479`
- **`-1` handling**: Oracle `importBulkData` returns `-1` when it accepts the call
  but can't queue the job → now treated as a real failure (not "completed"); the
  card explains `DocumentId: null` = file not staged to UCM = missing ERP
  Integration/UCM privilege; full Oracle response shown (base64 stripped). `7b77ecc`, `ebc25c7`
- **Pre-flight pod check** on conversion select: two probes — read an SCM data
  resource AND reach the ERP Integration endpoint. "reachable — submit not
  verified" (info) vs "not authorized"/"module not provisioned" (red). Soft-gates
  Load with a confirm when not ready. `41a5c7d`, `2784d33`

### 3.3 Global processing indicator
- axios client tracks in-flight requests (`apiActivity`); root `GlobalActivityBar`
  shows an indicator for ANY API call. `508c1a2`
- **Redesigned aesthetic** (demo): glowing indigo comet progress bar + frosted-glass
  pill with a conic-gradient ring, pulsing dot, animated ellipsis, breathing glow. `c3e65a2`
- **12s auto-hide cap** so it never lingers on one slow background request. `88df70d`

### 3.4 Rule & Crosswalk libraries
- **Manual add/edit** for both (backend `PATCH /learned-mappings/{id}` +
  `LearnedMappingUpdate`; `LearningApi.update`; shared `LearnedEntryModal`; Add
  buttons + per-row edit). Crosswalk Library was empty because crosswalks are only
  auto-captured from approved value-mappings — now populatable manually. `88df70d`
- **Crosswalk reach indicators**: top summary, per-category coverage, per-row
  "Used by N fields" badge. `ea70dfd`

### 3.5 File-driven conversion flow (biggest piece)
- **AI file classifier + learning** (backend): `detect_source_system` (Oracle EBS /
  NetSuite / Infor SyteLine / Arena PLM — added SyteLine + Arena to the source
  catalog) + `column_signature`; `GET /datasets/{id}/classify` and
  `POST /datasets/{id}/classify-learn` (learns by column signature); reuses existing
  `detect_dataset_type` for the target FBDI template; `Dataset.source_system` field. `6554d85`
- **"Convert a File" page** (sidebar, `/convert`): upload **multiple** files →
  AI detects source system + target FBDI template per file (editable dropdowns,
  confidence, "learned" badge) → creates one conversion per file → mapping. `6554d85`, `88ca2de`
- **Setup Wizard is now file-driven**:
  - Details "Target environment" is a **dropdown**: Oracle Fusion SCM Cloud |
    FBDI Template (manual upload). `88ca2de`
  - Connection step has an **Upload source files** card; files upload +
    AI-classify **on selection** (per-file ready/analyzing/error status). `88ca2de`, `b239f10`
  - On finish, **one conversion is created per uploaded file** (files take
    precedence over module auto-populate). `88ca2de`
  - Scope step shows a **"Conversions from your files"** card listing each file →
    its AI-detected FBDI target (files ARE the conversions; module catalog optional). `176e269`
- **Robust upload/parsing**: dataset upload returns a clear reason instead of a
  bare 400; CSV parser tolerates ragged rows (`on_bad_lines="skip"`), sniffs the
  delimiter, friendly message for unreadable Excel. `b239f10`
- **Per-conversion output type**: `Conversion.output_mode` (`fbdi_download` |
  `fusion_load`); file conversions default to `fbdi_download` when the engagement
  target is FBDI. Project Conversion Objects table shows an **Output** selector +
  a **⬇ FBDI** download action (generate + download). Changing a conversion's
  target FBDI template also **learns** (classify-learn by signature). Added
  `source_type` to `ConversionCreate`. `c53f021`

**Latest commit on `main`: `176e269`.**

---

## 4. Key backend files touched
- `app/services/fusion_service.py` — FBDI interface tables + ERP import-job map for
  all 17 SCM objects; `test_fusion_connection`, `load_to_fusion` (importBulkData),
  `get_load_status` (getESSJobStatus), `preflight_fusion` (two-probe), work-area map.
- `app/routers/fusion.py` — connection/test, `fusion-targets`, `fusion-preflight`,
  `load-to-fusion`, `load-runs/{id}/status`.
- `app/services/dataset_service.py` — `detect_dataset_type`, `detect_source_system`,
  `column_signature`.
- `app/routers/datasets.py` — `/classify`, `/classify-learn`, hardened `/upload`.
- `app/parsers/tabular_parser.py` — tolerant CSV/Excel parsing.
- `app/models/{conversion,dataset,load,learned}.py`, `app/schemas/{conversion,learned,runtime}.py`,
  `app/source_systems.py` (added syteline, arena).

## 5. Key frontend files touched
- `src/api/{client.ts,index.ts}` — activity tracker; `FusionApi`, `DatasetsApi.classify/classifyLearn`,
  `LearningApi.update`.
- `src/components/GlobalActivityBar.tsx`, `src/components/LearnedEntryModal.tsx`.
- `src/pages/{MappingReviewPage,LoadDashboardPage,ConvertFilePage,ProjectOverviewPage,
  ConversionDetailPage,RuleLibraryPage,CrosswalkLibraryPage}.tsx`.
- `src/components/setup/SetupWizard.tsx`, `src/components/layout/Sidebar.tsx`, `src/App.tsx`.

---

## 6. Open items / known constraints
- **Fusion live load blocked on the demo pod.** The user's pod
  `https://fa-euth-saasfademo1.ds-fa.oraclepdemos.com` with user `scm01.student`
  can *read* SCM data but **`importBulkData` returns request id `-1` with
  `DocumentId: null`** — the `scm01.student` user lacks the **ERP Integration /
  UCM-upload privilege** (a read/demo "student" account). Fix is Fusion-side: use a
  user with an ERP-Integration role (e.g. Supply Chain Application Administrator)
  and a valid document account, then the same load returns a real request id.
  The **FBDI-download path works regardless** (no Fusion privilege needed).
- **5 of 17 SCM ERP import job paths** (Subinventory, Locator, Price List, Lot
  Number, Serial Number) use canonical ESS paths that may need per-pod tuning.
- **Free-tier**: cold starts surface as transient CORS/network errors during the
  redeploy window; blocking JDBC means keep concurrent EBS ops modest.
- **CORS** is currently `allow_origins=["*"]` — tighten for production.
- Suggested next enhancements offered but not built: "Download all FBDI" button on
  the project; route "Create conversions & map" straight into Mapping Review;
  let Convert-a-File pick an existing dataset (not just fresh upload).

---

## 7. Quick "continue here" checklist for the next chat
1. Confirm latest `main` = `176e269` (or later): `git log origin/main --oneline -1`.
2. Deploys: edit files → update `launch_git.bat` (git add list + message) → run it
   via the Run dialog → verify push.
3. Verify code with isolate-checks in `/tmp` (the sandbox mount of recent edits is
   truncated — don't trust whole-file builds there).
4. For the demo, use the **FBDI-download** flow (Convert a File / file-based
   engagement); live Fusion load needs a privileged pod user.

---

## 8. Architecture (full reference — embedded so a new chat has it)

### 8.1 Layers
```
Browser — React 18 SPA (Vite/TS, Tailwind)
        |  HTTPS · REST/JSON · JWT (axios; global activity tracker)
        v
FastAPI backend (Python 3.12, Render/Docker)
   ├── API routers (thin: validate + auth + delegate)
   └── Domain services (business logic)
        |
        ├── MongoDB Atlas  (Beanie ODM / Motor)   — system of record (~30 collections)
        ├── Oracle EBS     (JDBC · ojdbc11 / jaydebeapi) — source reads
        ├── Oracle Fusion  (REST · ERP Integration importBulkData → UCM+ESS) — target
        └── Anthropic API  (optional AI Copilot)
```

### 8.2 Component layers
- **Client (frontend):** React + TS + Vite + Tailwind; React Router; axios (one
  client: base URL, JWT header, 401 redirect, activity interceptor); Zustand (auth);
  Recharts (charts); ReactFlow (dependency/dataflow graphs); TanStack Table. UI is
  organised by lifecycle: Projects, Conversion Objects, Mapping Review,
  Recommendations, Output Preview, Load Management/Migration Monitor/Error Traceback,
  Convert a File, plus AI Engine (Learning Center, Rule Library, Crosswalk Library)
  and Governance (Audit, Approvals).
- **API layer (FastAPI):** REST under `/api`. Principal routers: auth;
  projects/conversions; datasets/fbdi/fbdi_seed; discovery/source_connections;
  mapping/learned; operations (output, load, workflow, dependency, dashboard);
  fusion; copilot/coa/governance/audit.
- **Domain services:** mapping (live EBS columns/rows + AI matcher), output
  (build_converted_dataframe → FBDI layout, from file or live EBS), fusion (targets,
  test, pre-flight, importBulkData, status), dataset, learning, quality, dashboard,
  project.
- **Data store:** MongoDB Atlas via Beanie (async/Motor); all document models
  registered at startup; no relational migrations.
- **External systems:** Oracle EBS (JDBC; synonym-exposed objects resolved via a
  describe-style `SELECT * ... WHERE 1=0`); Oracle Fusion Cloud (REST + ERP
  Integration); optional Anthropic.

### 8.3 Core workflows / data flow
1. **Discovery / source onboarding** — connect to EBS over JDBC (or upload file
   extracts as datasets); resolve canonical tables + live counts.
2. **Project & conversion setup** — Setup Wizard scopes by module and auto-creates
   one conversion per catalog object; OR (file-based) upload files → one conversion
   per file, AI-classified.
3. **AI-assisted mapping** — rule-based matcher (acronym-aware tokenizer) proposes
   source→target field mappings with confidence; reuses learned mappings + KB;
   analyst approves/edits/drag-maps; rules persist.
4. **Transformation & output** — build converted dataframe (EBS rows via JDBC or
   file), apply mappings + transformation rules + crosswalks → FBDI column layout
   (ConvertedOutput / Output Preview).
5. **Delivery** — per conversion `output_mode`: **FBDI download** (generate → download
   the file for manual Fusion upload) OR **Load to Fusion** (zip + base64 →
   `importBulkData` → UCM stage + ESS import job; poll `getESSJobStatus`).
6. **Monitoring & governance** — Migration Monitor, Load Runs, Error Traceback
   (root-cause/dependency), dependency graph, audit/approvals/cutover/reconciliation.

### 8.4 Data model (key Beanie collections)
Project; Conversion (source_type ebs|dataset, template_id, output_mode, status);
Dataset + DatasetColumnProfile (source_system, detected_object_type);
FBDITemplate/FBDISheet/FBDIField; MappingSuggestion; LearnedMapping (kind =
`rule` | `crosswalk` | `file_signature`); TransformationRule; Crosswalk;
ConvertedOutput; LoadRun (fusion_request_id, fusion_state, fusion_tables,
fusion_work_area, fusion_response) + LoadError; SourceConnection (EBS + Fusion,
password stored `PLAIN:`+pw); plus discovery/audit/COA/cutover/governance docs.

### 8.5 Integration details
- **Oracle EBS (source, JDBC):** container ships Java + ojdbc11; jaydebeapi/JPype
  open the connection. Schema resolved by describe (`SELECT * ... WHERE 1=0`) to
  handle APPS synonyms; rows streamed with bounded selects.
- **Oracle Fusion (target, ERP Integration):** basic-auth REST. FBDI CSV → zip →
  base64 → POST `.../erpintegrations` `importBulkData` with the object's document
  account + ESS job name. Request id `-1` (with `DocumentId: null`) = accepted but
  not queued → UCM/ERP-Integration privilege issue. `getESSJobStatus` polls the phase.
  Pre-flight probes an SCM data resource + the erpintegrations endpoint.
- **AI:** deterministic rule-based matcher always on; Anthropic optional (Copilot).

### 8.6 Deployment topology
- **Backend:** Render Docker web service (Python 3.12 + Java + ojdbc11), Uvicorn;
  env vars: `MONGODB_URI`, JWT secret, seed admin, optional `AI_PROVIDER`/
  `ANTHROPIC_API_KEY`.
- **Frontend:** Render static site (Vite build → `dist`), SPA rewrite, `VITE_API_URL`
  → backend.
- **DB:** MongoDB Atlas.

### 8.7 Security / cross-cutting
JWT (HS256) bearer; secrets server-side; graceful degradation (retry on cold start,
actionable EBS/Fusion diagnostics); global activity indicator; load runs capture
raw Oracle responses; audit events. Production hardening: restrict CORS, rotate
seed admin, move long EBS/Fusion ops to background workers before scaling out.

---

## 9. Continuation session — 2026-07-24 (multi-source merge efficiency, single-file download fix, filled Oracle templates, docs & tests)

### 9.1 Efficient merged "Download all" (multi-source → one file per interface)
- **Problem:** with multiple sources per interface, download-all regenerated every
  merged object synchronously and timed out; a later deploy still showed the old
  per-source files (stale download).
- **Backend (`routers/operations.py`):** new `POST /conversions/project/{id}/generate-merged-all`
  builds every interface's merged file in the BACKGROUND (grouped by `target_object`,
  carrier = lowest load-order conversion; `output_status` polled via `/generation-status`).
  `GET /download-all` now **reuses** the merged artifact already written for each
  interface (scans every conversion in the group, newest artifact whose file exists,
  format-family matched) and just fast-zips them; `?regenerate=true` forces inline.
  Returns 409 if nothing generated yet.
- **Frontend (`api/index.ts`, `ProjectOverviewPage.tsx`):** `OutputApi.downloadAll`
  orchestrates `generateMergedAllAndWait` (background generate + poll all carriers)
  then the fast reuse-zip, with progress. Download-all maps each source (own mapping)
  then merges+generates one file per interface.

### 9.2 Single-file FBDI download opened as "corrupt" in Excel — fixed
- **Root cause:** a supplier/multi-sheet output is a **.zip** (Oracle FBDI bundle,
  e.g. `PozSuppliersInt.zip`) but the client force-saved it as `Supplier Import.csv`,
  so Excel saw ZIP bytes behind a `.csv` name and threw the recover-content dialog.
- **Fix:** CORS now exposes `Content-Disposition` (prod is cross-origin, so the
  browser was hiding the real filename from JS — `main.py`), and `OutputApi.download`
  saves under the server's real filename/extension (`.zip`), passed name is fallback.
- Same `.zip`-vs-`.csv` family mismatch had caused the download-all **409** — reuse
  lookup now accepts the whole csv family (`csv`/`zip`), not an exact `.csv` match.

### 9.3 Filled-in Oracle FBDI Excel templates (NEW output format, alongside CSV)
- **What:** in addition to the headerless CSV bundle, the tool can now emit the
  **real Oracle FBDI workbook filled in** (macros/instructions preserved) for
  Supplier, BOM, Customer and Item. (Employee stays HDL `.dat` — no Excel FBDI
  template exists for it.)
- **`services/template_fill_service.py` (new):** opens the bundled workbook
  (`keep_vba`), detects each sheet's header + first data row with the SAME logic as
  `parsers/fbdi_parser.py` — **tabular** (Supplier/BOM/Customer: title/`* Required`/
  header row 4/data row 5, cols from A) and **Oracle-transposed** (Item: col-A label
  column Name/Description/Data Type/Technical Name, headers on the Name row from col
  B, data below the metadata block, e.g. row 9), wipes the shipped sample rows, and
  writes finalized per-sheet frames into the matching columns by normalised header.
- **`services/output_service.py`:** `fmt="template"` materializes the source file
  (`materialize_template_file` — disk or rehydrated from Mongo `FBDITemplateFile`),
  builds finalized frames (no supplier reorder/END — the template owns column order),
  fills, saves `<OracleName>.xlsm`; degrades to a fresh xlsx if no source file.
- **Flows through** generate-output, generate-merged, generate-merged-all and
  download-all (reuse family `template → {xlsm,xlsx}`); fmt query patterns widened to
  `csv|xlsx|template`.
- **Frontend:** per-conversion **Excel** button + **"Download all (Excel templates)"**
  / "Filled Excel templates (.zip)"; `OutputApi` fmt widened to include `template`.
- Minor: openpyxl drops one decorative WMF image on save (macros/data intact).

### 9.4 Tests & documentation (deliverables)
- **`backend/tests/test_conversion_workbench.py` (new):** one consolidated,
  dependency-light pytest suite (no DB/network) — template fill (both layouts),
  supplier FBDI layout/END/naming, merge/dedup + survivorship, DQ cleanse/validate.
  Runs via pytest or `python3 …`. 11/11 pass. (Plus `tests/test_template_fill.py`.)
- **`docs/Conversion_Workbench_User_Guide.docx`** — end-to-end user guide.
- **`docs/Conversion_Workbench_Feature_List.docx`** — feature catalogue with a
  separate **Part 2 — AI-powered features** table (feature / what the AI does /
  deterministic fallback) covering: AI Auto-Map, candidate vetting, value crosswalks,
  control-default inference, plain-English steering, NL rule authoring, fill-blanks,
  AI-drafted DQ rules, AI DQ review, load-error remediation, reconciliation narrative,
  Copilot. `.gitignore` excludes the doc-gen scratch (`docs/node_modules`,
  `docs/_gen_docs.js`, `docs/_preview`, generated PDFs).

### 9.5 Key files touched this session
- Backend: `routers/operations.py`, `services/output_service.py`,
  `services/template_fill_service.py` (new), `main.py` (CORS expose header),
  `tests/test_conversion_workbench.py` (new), `tests/test_template_fill.py` (new).
- Frontend: `api/index.ts`, `pages/ProjectOverviewPage.tsx`.
- Docs: two `.docx` deliverables + this handoff; `.gitignore`.

### 9.6 Continue-here checklist
1. **Deploy:** run `launch_git.bat` (message already set for the filled-template
   feature). Backend + frontend redeploy on push.
2. **Verify live** on a multi-source project: Download-all returns one merged file
   per interface (fast, background-generated); a single FBDI download opens cleanly
   as `.zip`; "Download all (Excel templates)" and the per-row Excel button produce
   populated Oracle workbooks (Supplier `POZ_SUPPLIERS_INT` merged eBOS+NetSuite,
   BOM, Customer, Item) with data in the right sheet/rows and samples removed.
3. **Watch memory** on very wide filled templates (Customer 19 sheets / Item 18) —
   fill runs in the background; if a small instance OOMs, cap rows or stream.
