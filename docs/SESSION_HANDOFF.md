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

## 9. Continuation session — 2026-07-24

Scope of this session: multi-source merged Download-all efficiency (9.1) + single-file
download corruption fix (9.2) + filled-in Oracle FBDI Excel templates (9.3) + docs &
tests incl. an Excel test report (9.4) + the full AI-differentiator build #1–#8 (9.7).
Consolidated new-endpoint and files-touched references are in 9.8–9.9.

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
- **`docs/Conversion_Workbench_Unit_Tests.xlsx`** — Excel test report: Summary sheet
  (live COUNTIF pass/fail + coverage-by-area) + Test Cases matrix (11 cases: id, area,
  module under test, what it verifies, setup, expected, result, type). Built with
  openpyxl + recalc'd (0 formula errors).

### 9.6 Continue-here checklist
1. **Deploy:** run `launch_git.bat`. Backend + frontend redeploy on push. (User has
   been deploying after each feature; #1–#8 slice-1 are all deployed as of end of session.)
2. **Verify live** (multi-source project): Download-all returns one merged file per
   interface (fast, background); single FBDI download opens cleanly as `.zip`; filled
   Excel templates populate the right sheet/rows; the AI tabs/cards render (Duplicate
   suspects, Anomalies, Proven by other clients, Cutover readiness, Ask the copilot,
   Agentic plan preview).
3. **Run all new tests:** `python3 -m pytest backend/tests/test_conversion_workbench.py
   backend/tests/test_entity_resolution.py backend/tests/test_anomaly_service.py
   backend/tests/test_cross_client.py backend/tests/test_readiness.py
   backend/tests/test_synthetic_data.py backend/tests/test_copilot_grounding.py
   backend/tests/test_agentic_planner.py -q` (all pure, no DB/network).
4. **Watch memory** on very wide filled templates (Customer 19 sheets / Item 18) —
   fill runs in the background; if a small instance OOMs, cap rows or stream.
5. **AI is optional everywhere:** every new AI feature falls back to deterministic
   logic when `AI_PROVIDER`/`ANTHROPIC_API_KEY` is unset — none of them can break the flow.

### 9.7 AI data-intelligence features (roadmap build, in list order)
Building the AI-differentiator list the user prioritised, highest-impact first.
- **#1 Fuzzy duplicate / entity resolution — DONE.** `services/entity_resolution.py`
  (pure, unit-tested): identity-field detection per object, blocking + token/difflib
  similarity + union-find clustering with confidence + field evidence; optional AI
  adjudication of borderline clusters (fallback deterministic). `GET conversions/{id}/
  duplicate-candidates` (over the merged frame). UI: Output Preview → "Duplicate
  suspects" tab (+ "Adjudicate with AI"). Tests: `tests/test_entity_resolution.py` (7).
- **#2 Source-data anomaly / outlier detection — DONE.** `services/anomaly_service.py`
  (pure, unit-tested): high-null, leading/trailing spaces, mixed types, numeric
  outliers (IQR), embedded units, casing/whitespace variants, non-printables,
  duplicate rows — severity/count/examples; optional AI risk notes. `GET datasets/
  {id}/anomalies`. UI: Dataset detail → "Anomalies" tab (+ "Explain risks with AI").
  Tests: `tests/test_anomaly_service.py` (10).
- **#3 Cross-client mapping/crosswalk auto-suggestion — DONE.** `services/
  cross_client_service.py` (pure `aggregate_cross_client` + Beanie `suggest_for_object`):
  aggregates client-scoped LearnedMappings across ALL tenants for the same object,
  ranked by supporting-client count + reuse, excluding the current client (advisory,
  tenant-isolated). `GET conversions/{id}/cross-client-suggestions`. UI: Conversion
  detail → "Proven by other clients" card. Tests: `tests/test_cross_client.py` (5).
- **#4 Source-profiling → target-module recommendation — ALREADY EXISTS.** `datasets`
  router `/classify` + `/suggest-template` (detect_dataset_type / detect_source_system /
  column_signature) recommend the source system + target Fusion object and learn from
  confirmations. No rebuild; could add an AI adjudication layer if wanted.
- **#5 Object-readiness / effort scoring — DONE.** `services/readiness_service.py`
  (pure `score_readiness` + `assess_conversion`/`assess_project`): rolls required-field
  coverage + DQ + gold + output + last-load into a 0-100 score, band and effort
  estimate. `GET conversions/{id}/readiness` and `conversions/project/{id}/readiness`.
  UI: Conversion detail → "Cutover readiness" card. Tests: `tests/test_readiness.py` (8).
- **#6 Synthetic test-data generation — DONE.** `services/synthetic_data_service.py`
  (pure `synthetic_frame`): type-valid sample rows per FBDI interface honouring
  required/type/max-length/LOV/date-mask/name heuristics + unique keys, seeded.
  `GET fbdi/templates/{id}/synthetic-data?rows&fmt` (CSV / .zip multi-sheet / xlsx).
  UI: FBDI Templates page "Sample data" (flask) button. Tests: `tests/test_synthetic_data.py` (8).
- **#7 Conversational copilot — SLICE 1 DONE (read-only grounded Q&A).**
  `services/copilot_grounding.py`: `build_conversion_facts` (mappings w/ provenance +
  DQ + readiness), pure `answer_from_facts` (intents: field provenance / unmapped /
  DQ-reject / readiness / summary, with citations — works with no model),
  `answer_grounded` (LLM layer over the same facts, deterministic fallback; never
  mutates). `POST conversions/{id}/copilot`. UI: Conversion detail → "Ask the copilot"
  card. Tests: `tests/test_copilot_grounding.py` (8). NEXT for #7: confirmable ACTION
  tools (regenerate, toggle header, apply a rule) behind explicit confirmation.
- **#8 Agentic end-to-end conversion — SLICE 1 DONE (plan step + checkpoint).**
  `services/agentic_planner.py`: pure `plan_steps_for` (per-object ordered steps:
  bind source [blocker] → auto-map N required (gold→learnings→workbook→deterministic→AI)
  → generate merged → pre-load validate → resolve DQ hard errors [blocker], each
  naming its precedence LAYER), + `build_object_plan`/`build_project_plan` from real
  state. Read-only, no execution. `GET conversions/project/{id}/agentic-plan`. UI:
  ProjectOverview → "Agentic conversion plan (preview)" card with a disabled
  "Approve & run (coming soon)" checkpoint. Tests: `tests/test_agentic_planner.py` (6).
  NEXT for #8: execute-with-approval (per-object, at plan/dry-run/pre-load gates,
  reusing the map/generate/validate endpoints; approvals become learnings; never auto-load).
- **Still to build:** #7 copilot ACTION tools, #8 plan EXECUTION with approval gates.
  (Roadmap in `docs/AI_Differentiators_Roadmap.md`.)
- New AI-intelligence unit suites (all pure, no DB/network): `test_entity_resolution.py`
  (7), `test_anomaly_service.py` (10), `test_cross_client.py` (5), `test_readiness.py`
  (8), `test_synthetic_data.py` (8), `test_copilot_grounding.py` (8),
  `test_agentic_planner.py` (6) — 52 total.

### 9.8 New API endpoints (this session)
Conversions/output router (`routers/operations.py`, mounted under `/api/conversions`):
- `POST /project/{id}/generate-merged-all` — background-generate every interface's merged file.
- `GET  /project/{id}/download-all?fmt=csv|xlsx|template&regenerate=` — fast reuse-zip of merged files.
- `POST /{id}/generate-merged`, `GET /{id}/merged-preview` — merged generate + preview (per interface).
- `GET  /{id}/duplicate-candidates?threshold&use_ai` — #1 fuzzy entity resolution.
- `GET  /{id}/cross-client-suggestions?limit` — #3 cross-client suggestions.
- `GET  /{id}/readiness`, `GET /project/{id}/readiness` — #5 cutover-readiness (object + project rollup).
- `POST /{id}/copilot` `{question}` — #7 grounded copilot Q&A.
- `GET  /project/{id}/agentic-plan` — #8 agentic plan step (checkpoint, read-only).
- Also present from earlier: `GET /{id}/preload-report`, `GET /{id}/reconciliation`,
  `POST /load-runs/{id}/explain-errors`.
Datasets router (`routers/datasets.py`, `/api/datasets`): `GET /{id}/anomalies?use_ai&max_rows` — #2.
FBDI router (`routers/fbdi.py`, `/api/fbdi`): `GET /templates/{id}/synthetic-data?rows&fmt` — #6.
Output generation (`services/output_service.py`): new `fmt="template"` across generate-output,
generate-merged(-all) and download-all (#9.3).

### 9.9 All files touched this session (complete)
**Backend — new services:** `template_fill_service.py` (9.3), `entity_resolution.py` (#1),
`anomaly_service.py` (#2), `cross_client_service.py` (#3), `readiness_service.py` (#5),
`synthetic_data_service.py` (#6), `copilot_grounding.py` (#7), `agentic_planner.py` (#8).
**Backend — edited:** `routers/operations.py` (merged download-all + all #1/#3/#5/#7/#8 endpoints),
`routers/datasets.py` (#2 endpoint), `routers/fbdi.py` (#6 endpoint),
`services/output_service.py` (`fmt="template"` + materialize), `main.py` (CORS expose
`Content-Disposition`).
**Backend — new tests:** `test_conversion_workbench.py`, `test_template_fill.py`,
`test_entity_resolution.py`, `test_anomaly_service.py`, `test_cross_client.py`,
`test_readiness.py`, `test_synthetic_data.py`, `test_copilot_grounding.py`,
`test_agentic_planner.py`.
**Frontend — edited:** `api/index.ts` (all new API methods + `ReadinessObject` type +
`download` uses server filename), `pages/ProjectOverviewPage.tsx` (merged download-all +
Excel-template buttons + Agentic plan card), `pages/OutputPreviewPage.tsx` (Duplicate
suspects tab), `pages/DatasetDetailPage.tsx` (Anomalies tab), `pages/ConversionDetailPage.tsx`
(Proven-by-other-clients + Cutover-readiness + Ask-the-copilot cards),
`pages/FbdiTemplatesPage.tsx` (Sample-data button).
**Docs / config:** `docs/Conversion_Workbench_User_Guide.docx`,
`docs/Conversion_Workbench_Feature_List.docx`, `docs/Conversion_Workbench_Unit_Tests.xlsx`,
`docs/AI_Differentiators_Roadmap.md` (referenced), this handoff, `.gitignore`
(doc-gen scratch + render byproducts), `launch_git.bat` (commit messages per deploy).
Note: a stray `docs/node_modules` + `docs/_gen_docs.js` from the docx build remain on
disk (folder deletes are permission-gated; user declined) but are git-ignored.

### 9.10 CW_Issues.xlsx (analyst-reported) — status
- **Issue #3 — banded mapping export — DONE.** `services/mapping_export_service.py`
  (pure `build_workbook` + `band_for`, 5 tests) + `POST conversions/{id}/mapping-export`
  + Mapping Review "Export mapping (Excel)" button. Replaces the flat
  `field_mapping.csv` with Summary + per-confidence-band sheets (matches
  `Item_NetSuite_Field_Mapping_Clean.xlsx`).
- **Issue #1 — Customer multi-sheet upload — DONE (prompt at upload).**
  Root cause: `_read_excel_robust` read only the LARGEST sheet. Fix: `tabular_parser`
  gains `list_excel_sheets` + a `sheet=` param on `parse_tabular`; `POST datasets/
  peek-sheets` lists a workbook's sheets; `POST datasets/upload` accepts a `sheet`
  form field; `create_dataset_from_upload(sheet=...)` extracts that sheet into its
  OWN single-sheet CSV (so every downstream read is correct with no sheet tracking).
  UI: `CreateDatasetModal` peeks on file-pick and, for a multi-sheet workbook, shows
  a checkbox picker — each selected sheet is imported as its own source dataset
  (Customer + Address → two datasets, each bound to its own interface). Tests:
  `tests/test_multisheet_parser.py` (3).
- **Issue #4 — low suggestion confidence — DONE (better tokenization).**
  Genuine matches scored low because cryptic NetSuite names didn't tokenize.
  `app/ai/rule_based.py::_tokenize` now strips custom-field noise prefixes
  (`custitem_`/`custentity_`/…) and splits glued id-suffixes on curated domain stems
  (`itemid`→`item id`) with no false splits (`valid`/`void` unaffected). Result:
  `custitem_lifecycle_phase`→`Lifecycle Phase` 41%→80%, `itemid`→`Item Number`
  surfaces ~51%, unrelated `createddate` stays 14% (no inflation). Tests:
  `tests/test_scorer_tokenization.py` (6). Further gains (LOV/synonym weighting)
  possible; verify against analyst-expected mappings before pushing weights higher.
- **Issue #2 — approved defaults missing in FBDI output — FIXED (verify live).**
  Root cause: the UI + mapping export show default = `mapping.default_value` OR
  `controlDefaultFor` OR `effectiveDefaults` (from `defaults_service.
  compute_effective_defaults`), but GENERATION only applied `output_service`'s own
  static `_CONTROL_DEFAULTS`. A default living only in the effective-defaults layer
  (e.g. the 4 Customer profile booleans) therefore showed in the UI/export but was
  blank in the file. Fix: `generate_output_artifact` computes
  `compute_effective_defaults(conversion, use_ai=False)` and `_apply_control_defaults`
  now fills any BLANK, non-suppressed column from that dict (same key normalization),
  so output matches the UI. Logic unit-verified; **live-verify after deploy**:
  regenerate the Customer output and confirm the 4 fields are populated. (Sandbox
  can't import beanie modules due to an OpenSSL/pymongo mismatch — env issue, not code.)
  (The #4 status is recorded above under "Issue #4 — DONE".)

### 9.11 Feature Verification Guide — live screenshots embedded
- `docs/Conversion_Workbench_Feature_Verification_Guide.docx` (built by `docs/_gen_verify_guide.js`)
  is a UAT checklist: Setup (2) + Everyday (11) + AI (10) feature blocks, each with numbered
  steps, an Expected line, and a screenshot box. AI features are marked green.
- Screenshots were captured live from the deployed app (Claude in Chrome, standalone
  `computer` screenshots with `save_to_disk:true` → `outputs/screenshot-*.jpg`) and embedded
  into each block via `docx` `ImageRun` (type `jpg`, aspect ratio preserved by a small
  `jpegSize` JPEG-SOF parser). 20 image instances (17 unique; a few shots are reused where
  one screen documents two features).
- Embedded shots: dashboard, Clients (scoping), Datasets list, dataset profiling/anomalies,
  FBDI Templates, Projects/conversions table (multi-source), Project exec-summary + download-all
  buttons, conversion detail pipeline, Mapping Review canvas + table (confidence/reasons/Export),
  Duplicate suspects, Output Preview (converted + zip/csv), Load Management, Gold Standards,
  Recommendations (AI DQ rules), Cutover-readiness + Ask-the-copilot (with a live grounded
  answer), and the Agentic conversion plan.
- 3 interaction-only steps are intentionally left as a dashed box with a "capture live during
  UAT" note (they can't be staged without side effects): Value crosswalks panel, Plain-English
  rule author, Explain load errors (needs a failed load run).
- The generator (`docs/_gen_verify_guide.js`) hardcodes the sandbox `outputs` path for the
  jpgs; it is a scratch build script (matches `.gitignore` `docs/_*`) — only the built .docx
  is a deliverable. Re-running it elsewhere needs the jpgs on the same path or a new SHOTS dir.

### 9.12 Mapping confidence improvement (Item avg < 50%) + vetted values in export
Analyst note (CW_Issues, Item): "confidence scores average below 50 … most only check type
compatibility and keyword." Three-part fix — deterministic scorer, AI residual, export.

**(A) Deterministic scorer — `app/ai/rule_based.py`.**
- All fuzzy logic uses the Python **stdlib `difflib`** (no external deps), applied fully
  BEFORE any AI call:
  - `_tok_sim(a,b)`: exact / prefix ≥3 / suffix ≥4 / embedded ≥5 / `SequenceMatcher.ratio()`
    (≥0.86 full, 0.75–0.86 graded partial ×0.7 — catches typos `adress`→`address`,
    `custmer`→`customer`).
  - `_soft_coverage`: fraction of TARGET tokens covered by their best fuzzy source token.
  - `_whole_ratio`: whole-name `SequenceMatcher` ratio (overall closeness / word-order safety net).
  Column-name score = `0.30*Jaccard + 0.55*soft_coverage + 0.15*whole_ratio`, so a short/
  cryptic source contained in a longer target name (`descr`→`description`, `rev`→`revision`)
  is rewarded instead of punished by Jaccard. The suffix/embedded rule handles **glued
  compound columns in every module** — `hiredate`(hire+date), `remitemail`(remit+email),
  `billtocity`(bill+to+city), `componentitem`(component+item), `effstartdate`. Measured typo/
  fuzzy lift: `adress`→Address 79%, `custmer_name`→Customer Name 84%, `descriptn`→Description
  78%. Edge tradeoff: a literal suffix like `city`⊂`capacity` can give a rare review-band false
  hit — contained by best-per-field selection + value/LOV penalties. Unrelated controls ~22%.
- `_semantic_score` is fuzzy-aware (alias hit by exact membership OR prefix/fuzzy).
- Semantic now also runs against the target DESCRIPTION tokens.
- **Root fix for the < 50% average:** the composite is now NORMALISED by the weight of the
  signals that actually APPLY. Previously value/LOV weight (0.18+0.10) was unreachable when a
  field had no list-of-values and no samples, capping/diluting every score. Now name/semantic
  always count; description/type/value/LOV count only when present; `match_quality = Σ(score·w
  applicable)/Σ(w applicable)`. Fill stays a light additive (can't carry an unrelated column);
  negative value/LOV signals stay as penalties. Result (measured): `custitem_lifecycle_phase`→
  Lifecycle Phase 96%, `item_description`→Item Description 100%, `itemid`→Item Number 71%,
  `mfg_name`→Manufacturer Name 71%, `base_uom`→Primary Unit of Measure 46%; unrelated
  (`createddate`→Item Number) stays 20%. Added Item-master synonyms (revision, weight, volume,
  planner, buyer, lead, template, primary, serial, lot, manufacturer, unit, measure).
- Tests: `tests/test_scorer_tokenization.py` extended to 9 (fuzzy abbrev, partial coverage,
  missing-LOV-no-dilution, higher genuine floor, unrelated stays low). All pass.
- **Module-wide (not Item-only):** `score_pair` is the shared scorer for every conversion, so
  the lift applies across modules. Measured (name+type only, no samples/LOV — AI residual lifts
  the rest): Supplier vendor_name→Supplier Name 71%, remitemail→Remittance Email 48%; Customer
  creditlimit→Credit Limit 76%, companyname→Party Name 65%, billtocity→City 41%; BOM
  qtyper→Quantity 54%, effstartdate→Start Date 33%; Employee workemail→Work Email 42%,
  hiredate→Hire Date 34%. Unrelated controls stay 20%.

**(B) AI residual now runs on WIDE templates — `app/services/mapping_service.py`.**
- Root cause found: `_heavy = len(targets) > 300` **fully disabled the LLM residual**, so Item
  (300+ attributes) relied on deterministic scoring ALONE — exactly the analyst complaint.
- Fix: heavy templates still send the low-confidence residual (< 0.60) to Claude, but CAPPED
  required-first at `_AI_CAP = 120` so the batched call stays bounded (`anthropic_suggest_batched`
  already chunks 24 / concurrency 5 / per-chunk fallback). Narrow templates unchanged.
- Architecture confirmed (no change needed elsewhere): deterministic-first → weak residual to
  LLM → learned/gold override → item guard → do-not-map exclusions; on-demand `vet-candidates`
  adds an AI verdict+reason. ANTHROPIC_API_KEY is live on Render (the copilot answered live).
- **Live-verify after deploy:** re-run AI Auto Map on an Item conversion; confidence should rise
  and required fields get an AI suggestion. (Beanie path — not unit-testable in the sandbox.)

**(C) Export now carries the AI-vetted values — `app/services/mapping_export_service.py` +
`frontend/.../MappingReviewPage.tsx`.** Per analyst request (Both, inline):
- Two new columns on every confidence-band sheet: "Vetted Alternatives (AI-checked)" (ranked
  source candidates, each `source (conf%) — verdict: reason`) and "Value Crosswalks (legacy →
  Oracle)" (`legacy → CODE (how)`), rendered by pure `_fmt_alternatives` / `_fmt_crosswalks`.
- Frontend `exportMapping` enriches each record with `alternatives` (from `r.alts`, top 4, incl.
  `ai_verdict`/`ai_reason`) and `crosswalks` (fetched once from `MappingApi.codedValues`,
  non-identity resolved pairs keyed by target field).
- Tests: `tests/test_mapping_export.py` → 7 (added vetted-columns render + blank-when-absent).
- Files touched this wave: `app/ai/rule_based.py`, `app/services/mapping_service.py`,
  `app/services/mapping_export_service.py`, `frontend/src/pages/MappingReviewPage.tsx`,
  `tests/test_scorer_tokenization.py`, `tests/test_mapping_export.py`.

### 9.13 Flat "All Fields" export sheet + glued-token precision fix (2026-07-27)

**(A) Export is now "Both" — flat sheet AND confidence bands —
`app/services/mapping_export_service.py` + `frontend/.../MappingReviewPage.tsx`.**
- Analyst context: the downloaded `field_mapping.csv` was the OLD flat CSV (11 columns,
  "Other options" showing bare probabilities like `custentity_flex_cust_id (33%)`). The
  reasons/AI-vetting work from 9.12 only existed on the banded .xlsx, so the familiar
  single-table view had lost the per-option reasons.
- Fix: `build_workbook` now writes a flat **"All Fields"** sheet (immediately after Summary,
  one row per field, original order) alongside the existing per-band sheets. Columns mirror the
  old CSV plus the new evidence: Source Field / Target FBDI Field / Required / How it's mapped /
  Transform / Confidence % / Status / Needs confirmation / Why / **Other options (AI-vetted,
  with reasons)** / **Value Crosswalks (legacy → Oracle)** / Excluded / Notes.
- Reuses `_fmt_alternatives` / `_fmt_crosswalks`, so an option now reads
  `custentity_flex_cust_id (33%) — unlikely: a Salesforce id, not the account source ref`
  instead of just `(33%)`.
- `exportMapping` enriches each record with `required`, `how_mapped`, `transform`, `status`,
  `needs_confirmation`, `notes` so the flat sheet matches the on-screen table exactly.
- All extra record keys are OPTIONAL — `build_workbook` degrades gracefully if absent.
- Verified: workbook builds with sheets `[Summary, All Fields, 100pct_-_Exact_Match, 50-75pct,
  0pct_-_No_Match_Found]`; per-option reasons and `USA → US (vetted)` crosswalks render.
  `tests/test_mapping_export.py` 7/7 pass.

**(B) Precision fix in the glued-token matcher — `app/ai/rule_based.py`.**
- Bug introduced by 9.12's suffix/interior rule: credit was given for ANY substring, so
  `capacity` scored **57%** against `City` ("city" is literally the tail of "capacity").
  Same class of error: `surname`→Name, `paid`→Employee Id, `velocity`→City.
- Fix: a glue match now requires BOTH halves to be meaningful — the matched part must be in the
  new curated `_WORD_PARTS` vocabulary AND the residue must decompose into known parts via
  `_is_wordy()` (depth-bounded, allows `billto` = bill+to, `effstart` = eff+start).
  `capacity` − `city` leaves `capa`, not a word → no credit.
- Measured: noise collapsed (capacity→City 57%→**10%**, velocity→City 10%, surname→Name 11%,
  paid→Employee Id 6%) with **zero loss** on genuine matches — hiredate→Hire Date 62%,
  componentitem→Component Item 63%, effstartdate→Eff Start Date 61%, invoicenumber 63%,
  billtocity 46%, shiptocountry 46%, and typo cases custmer_name 78% / adress 65% all held.
- Note: figures above are the deterministic NAME component only (`0.30·jaccard +
  0.55·soft_coverage + 0.15·whole_ratio`), not final `score_pair` confidence, which adds
  semantic/type/value-affinity — e.g. vendor_name→Supplier Name is 46% on name alone because
  vendor/supplier is a SYNONYM hit, scored by `_semantic_score`.

**Caveats / still open**
- Sandbox cannot run the full pytest suite (`test_app.py`, `test_ebs_output.py` fail to collect
  on a pyOpenSSL/cryptography clash in pymongo; others hang on DB). Only environmental.
- `tsc` still reports the pre-existing `pid` ObjectId-vs-string errors and a `COAEngine.tsx`
  `crosswalks` error — both unrelated to this wave; Vite/esbuild build is unaffected.
- **Live-verify after deploy:** Export mapping (Excel) on a Customer conversion → confirm the
  "All Fields" sheet appears and its "Other options" column carries reasons, not bare percentages.

### 9.14 QA issue list of 27/07/2026 — audit + issue 6 fix

Audit of the 8-row QA sheet (Tejaswini / Aryan / debayon). Verdict per row, then the fix made.

| # | Module | Issue | Verdict |
|---|--------|-------|---------|
| 1 | Customer | Two-sheet workbook → only first sheet's columns; two conversions | PARTIAL |
| 2 | Customer | Approved defaults not in FBDI output | FIXED (verify live) |
| 3 | Item | AI mapping file grouped into confidence bands | FIXED |
| 4 | Item | Confidence scores average below 50 | IMPROVED |
| 5 | Supplier | Deleted Learning Center items reappear | **NOT FIXED** |
| 6 | Supplier-NetSuite | Saved custom transformation rule not shown on reopen | **FIXED (this wave)** |
| 7 | EBOS Supplier | Default values not populated in Learning Centre | **NOT FIXED** |
| 8 | EBOS Supplier | Address Name mapped to city in UI, static PRIMARY in file | **NOT FIXED** |

**(A) Issue 6 — FIXED.** Root cause was purely frontend: `RuleAuthorModal`'s open-effect
unconditionally reset the form (`setType("VALUE_MAP")`, `setConfig(VALUE_MAP.defaultConfig())`)
and never fetched existing rules — so a saved rule was invisible even though the backend had
stored it AND learned it (`routers/mapping.py:434 record_learning_from_rule`), which is why the
output was correct. Second, latent defect: `save()` always POSTed, so re-saving stacked a
DUPLICATE rule on the same target field.
- `backend/app/routers/mapping.py` — new `PUT /rules/{rule_id}` (`update_rule`) that edits in
  place and re-runs `record_learning_from_rule` so the EDITED definition is what future
  conversions inherit.
- `frontend/src/api/index.ts` — `MappingApi.updateRule`.
- `frontend/src/components/transforms/RuleAuthorModal.tsx` — on open, `MappingApi.rules()` loads
  the conversion's rules; any rule on the current target field is pre-loaded into the form
  (type, config, source column, description; latest by `sequence` wins). New green banner lists
  every saved rule for the field — click to load, trash to delete, "+ Add another instead" to
  author a second. Title and button switch to "Edit…" / "Update & learn" when editing.
  `save()` PUTs when `editingRuleId` is set, POSTs only for genuinely new rules.
- Also cleaned the pre-existing `conversionId: number` vs ObjectId-string mismatch in this file
  (prop widened to `string | number`, call sites `String()`-wrapped) — `tsc` clean for this file.

**(B) Issues still open — root causes located, NOT yet fixed (need a decision).**
- **#8 Address Name → PRIMARY.** `output_service.py:419` `_CONTROL_DEFAULTS["address name"]="PRIMARY"`,
  and `"address name"` is in `_AUTHORITATIVE` (`:480`), whose branch at `:522-525` does
  `df[col] = _CONTROL_DEFAULTS[key]` with **no check for an explicit mapping**. The only escape
  is `suppressed_keys`, built at `:648-656` solely from `status == "not_applicable"`. So the
  seeded eBOS mapping (`data/supplier_field_mappings.json`, Address Name * ← city) is written
  then overwritten. UI/preview disagree because `get_output_preview` (`:1025-1028`) never calls
  `_apply_control_defaults`. Same defect class: `supplier site`, `pay`, `ordering`, `rfq or bidding`.
  *Fix shape:* pass the set of target fields having an explicit non-suppressed `source_column`
  into `_apply_control_defaults` and skip the `_AUTHORITATIVE` branch for them (mirrors
  `defaults_service.py:163`).
- **#5 Deleted learnings reappear.** No tombstone exists anywhere (`grep tombstone|is_deleted`
  → nothing). `routers/learned.py:500-509` hard-deletes. Three paths recreate it: startup seeds
  (`main.py:103-121`, find-or-insert), auto-capture on every Generate
  (`output_service.py:968-973` → `learning_service.py:111-135`; supplier is never `_heavy`, so
  this always runs), and approve/override (`routers/mapping.py:338`). *Fix shape:* a
  `deleted`/tombstone flag on `LearnedMapping` respected by `_upsert` and the seeders.
- **#7 Defaults absent from Learning Centre.** UI and API are correct; the rows are never
  written. `MappingReviewPage.tsx:551-562 setFixedValue()` sends `source_column: null`, and
  `routers/mapping.py:336-339` only learns `if ... and m.source_column`, with
  `learning_service.py:192-193` bailing on no source column — so a default-only mapping is
  learned only after a Generate. Also `learning_service.py:119-134` puts `not_applicable` +
  default under `suppress_field` instead of `example_default`, and the whole `_CONTROL_DEFAULTS`
  set is never persisted as learnings.
- **#1 Multi-sheet.** Sheet picking exists (`tabular_parser.py:117 list_excel_sheets`,
  `POST /datasets/peek-sheets`) but is wired ONLY into `CreateDatasetModal.tsx:62`; the
  Convert-a-file and Setup-wizard paths upload with no `sheet`, and
  `tabular_parser.py:163-166` then picks the **largest** sheet. No column union anywhere
  (`mapping.py:297-299` reads one `dataset_id`); `PUT /conversions/{id}/sources` has no UI caller.
  So "one conversion spanning both sheets" is not currently reachable.

**Live-verify after deploy (issue 6):** open a Supplier-NetSuite conversion → a field with a
saved rule → "Add custom transformation rule" → the saved rule loads, banner shows it, Save
updates rather than duplicating.

### 9.15 QA issues #1, #5, #7, #8 — the four that were still open

**(A) #8 Address Name shipped "PRIMARY" over an explicit mapping — FIXED.**
`output_service._apply_control_defaults` gains an `explicitly_mapped` parameter.
The `_AUTHORITATIVE` branch (`df[col] = _CONTROL_DEFAULTS[key]`) now skips fields the
analyst deliberately bound to a source column; a new fallback branch still writes the
constant when the mapped source produced nothing, so no control column ships empty.
`explicitly_mapped_keys` is built next to `suppressed_keys` in `generate_output_artifact`
from `_best_m`, restricted to `status in {approved, overridden}` with a non-blank
`source_column` — "suggested" is deliberately excluded, since auto-map guessing is exactly
what the authoritative constants exist to correct. Applied learnings get `status="approved"`
(`learning_service.py:390`), so the seeded eBOS `Address Name <- city` row qualifies.
Verified by replay: no explicit map -> `PRIMARY` (unchanged); explicitly mapped -> `Austin/
Dallas`; mapped-but-blank -> `PRIMARY`; `suppressed` still wins; `Pay` unaffected.
Same fix covers Supplier Site, Pay, Ordering, RFQ or Bidding.

**(B) #5 Deleted learnings reappeared — FIXED with a tombstone.**
`LearnedMapping` gains `is_deleted` / `deleted_at` / `deleted_by`. Rather than patching ~40
query sites across 18 modules (one miss = the bug survives), `find` / `find_one` / `find_all`
are overridden ON THE MODEL to inject `{"is_deleted": {"$ne": True}}`, with an
`include_deleted=True` escape hatch. `$ne: True` also matches documents that predate the
field, so existing rows stay visible — no migration needed. Query-shape behaviour verified
against Beanie 2.1 (plain dicts, multi-arg `$and`, `$in`, sort/limit chains, kwargs).
`DELETE /learned-mappings/{id}` now tombstones (`?purge=true` still hard-deletes), plus
`POST /{id}/restore` and `GET /retired/list` so a deletion is reviewable, not a black hole.
All seven resurrection paths were closed — `_upsert` (auto-capture / approve / override, via
a new `revive` flag), the four `catalog_seed_service` seeders, `defaults_service`'s AI-default
cache, and `example_learning_service` (re-uploading the same gold file would otherwise have
silently undone every deletion).

**(C) #7 Defaults missing from the Learning Centre — FIXED.**
Two independent causes. (1) `record_learning_from_mapping` returned early on
`not mapping.source_column`, and both call sites in `routers/mapping.py` gated on
`m.source_column` — so a default-only decision was learned ONLY after a Generate Output.
Both gates now also accept a non-blank `default_value`, and the service records an
`example_default` for the default-only case. (2) In `capture_learnings_from_conversion` the
`not_applicable` branch ran first, filing `not_applicable + default` under "left blank on
purpose" — contradicting `output_service` and `learning_service`, which both treat that shape
as *populate*. It now requires a blank default, and the `example_default` branch accepts
`not_applicable`. Verified by decision table: analyst default / Set-fixed-value / NA+default
all now capture as `example_default`; genuine blanks still `suppress_field`.

**(D) #1 Two-sheet workbook only imported one sheet — FIXED on the Convert-a-file path.**
`ConvertFilePage.analyzeAll` now peeks sheets before upload and expands a multi-sheet
workbook into one row per data sheet (rows > 1), uploading each with its `sheet` argument so
BOTH sheets' columns become available; the row shows a sheet chip. Previously these paths
uploaded with no `sheet` and `tabular_parser.py:163-166` silently kept only the LARGEST
sheet. Non-xlsx and single-sheet files are untouched, and the peek is best-effort.

**Deliberately NOT done — needs a product decision.**
The QA note also says "generates two conversions when two sheets are uploaded". That remains
true and is arguably correct (Oracle Customer Import genuinely separates account from
address). Making ONE conversion span both sheets would need a column-wise join on a shared
key; `PUT /conversions/{id}/sources` exists for multi-source binding but has no UI caller and
`output_service` currently row-concats rather than joins. Guessing a join key would silently
corrupt data, so I left it. `SetupWizard` and `ConversionDetailPage` still upload without a
sheet argument — same one-line change if you want it there too.

**Verification:** backend `ast.parse` clean on 8 edited modules; `tests/test_mapping_export.py`
+ `test_scorer_tokenization.py` 16/16 pass; `tsc` shows no new errors in edited files (the
`ConvertFilePage` / `TransformationStudioPage` id-type errors are pre-existing). Full pytest
still can't run in the sandbox (pyOpenSSL/cryptography clash in pymongo).

**Live-verify after deploy:** (#8) generate an eBOS Supplier Address and confirm Address Name
holds city, not PRIMARY. (#5) delete a supplier learning, restart the backend, confirm it
stays gone and appears under retired. (#7) set a fixed value in Mapping Review and check the
Learning Centre "Default values" tab BEFORE generating. (#1) upload a two-sheet workbook on
Convert a file and confirm two rows appear with sheet chips.

### 9.16 One conversion, one FBDI bundle, from a multi-sheet input (2026-07-27)

Driven by the real file `Customer Dump Latest available (2).xlsx`: 2 sheets — Customer
(5,489 rows x 19 cols) and Address (22,505 rows x 16 cols), joined on `internalid` with
**100% referential integrity** (no orphans either way, 1..355 addresses per customer).

**Why the previous design could not do this.** `build_converted_dataframe` produces ONE wide
frame that `_finalize(sfields)` then slices per interface sheet. That cannot represent two row
grains: the party sheets need 5,489 rows and the address sheets 22,505. The existing
multi-source path made it worse — `_merge_dedupe_frames` ROW-CONCATENATES sources, so binding
both sheets would stack 5,489 customer rows underneath 22,505 address rows in one frame.
And before any of that, the parser kept only the LARGEST sheet (Address, 450,120 cells vs
104,310), so the Customer tab was silently discarded entirely.

**What was built — per-interface-sheet source routing.**
1. `output_service.build_converted_dataframe(..., collect_frames=dict)` — optional. When
   passed, it records `{dataset_id: (converted_frame, source_columns)}` for each bound source
   BEFORE the merge. Every existing caller is untouched and still gets the merged frame.
2. `generate_output_artifact` collects those frames; if fewer than 2 it clears the dict, so
   single-source generation takes exactly the old path.
3. New `_frame_for(sfields)` picks, per interface sheet, the bound source that supplies the
   MOST of that sheet's mapped source columns; ties or zero evidence fall back to the merged
   frame (i.e. previous behaviour). `_finalize` now reindexes off `_frame_for(sfields)`.
4. `mapping_service` source columns are now the UNION across `source_dataset_ids` (primary
   first, de-duplicated by name). Without this the mapper only saw the primary sheet, so
   address-only fields were never mapped and the address interface sheets shipped empty.
5. `ConvertFilePage.generateSetForRow` sends `dataset_ids` for every sheet of the SAME
   workbook, so one conversion set is bound to all sheets. `POST /conversions/generate-set`
   already accepted `dataset_ids` and bound all sources — no backend change needed there.

**Verified by replaying the routing against the real column lists:**

| Oracle interface sheet | Routed to | Column hits | Rows |
|---|---|---|---|
| HZ_IMP_PARTIES_T | Customer tab | 4/4 | 5,489 |
| HZ_IMP_ACCOUNTS_T | Customer tab | 3/3 | 5,489 |
| HZ_IMP_CONTACTPTS_T | Customer tab | 3/3 | 5,489 |
| HZ_IMP_ADDRESSES_T | Address tab | 6/6 | 22,505 |
| HZ_IMP_ADDRESSUSES_T | Address tab | 3/3 | 22,505 |

Clean separation, no ambiguity. Backend `ast.parse` clean; 16/16 tests pass; no new tsc
errors in the edited region.

**NOT yet verified — needs deploy + a live run.** None of this has been exercised against the
running app: the routing was proven by replaying the real decision function over the real
column lists, not by generating an actual bundle. Specifically still to confirm live:
the two sheets import as two datasets and bind to ONE conversion set; the mapper maps
address-only columns now that it sees the union; the generated bundle has the row counts in
the table above; and `internalid` lands as the Original System Reference that links the
address rows back to their party (the Customer-glue pass at `_is_customer` should supply this,
but it has not been checked against a two-source conversion).

**Also fixed:** `launch_git.bat` now removes `_qa/` renders and `~$` Excel lock files before
`git add -A` — the same class of stray-file commit that broke a previous deploy.

### 9.17 Vet options with AI + Export mapping (Excel) in Canvas view

Both buttons were table-only, so an analyst working in canvas had to switch views to reach
them. First attempt — copying the JSX into the shared header — does NOT work and was reverted:
they live inside `MappingTableView`, a separate component that is only mounted when
`viewMode === "table"`, so the parent header cannot see `vetWithAi` / `exportMapping`
(ten TS2304 "Cannot find name" errors). This is why the earlier "Fill blanks with AI" move
was easy and this one was not — that handler already lived in the parent.

**What was done.** `runVetPage` / `vetWithAiPage` / `exportMappingPage` now live in
`MappingReviewPage` itself, alongside `fillBlanksPage`:
- `vetWithAiPage` vets the currently visible targets (falls back to all), chunked at 150 ids
  so a 1,254-field Customer template cannot overrun the gateway, and folds the verdicts into
  the existing shared `aiVerdicts` cache via `mergeAiVerdicts`.
- `exportMappingPage` fetches the ranked candidates (`MappingApi.candidates`) AND the value
  crosswalks (`MappingApi.codedValues`) on click, then auto-vets, so a canvas export carries
  the same "Vetted Alternatives (AI-checked)" and "Value Crosswalks" columns as a table one
  rather than shipping a workbook with those columns silently empty.

**Deliberate design point — the header pair renders only when `viewMode !== "table"`.**
The table keeps its own richer pair, built from the row models it already holds (method
labels from `classifyLayer`, `isGap`, `confirm`). Replacing those with the parent version
would have been a quiet regression in the Excel deliverable, and showing both at once would
put two Export buttons on screen. So: table view uses its originals, canvas uses these.
The cost is two implementations of the export record shape — if one is changed, check the
other (`MappingReviewPage.exportMappingPage` and `MappingTableView.exportMapping`).

Verified: `tsc` clean for this file apart from the pre-existing numeric-id noise; JSX balance
confirmed. NOT verified live — needs a click-through in canvas after deploy to confirm the
buttons appear, the vet message renders, and the downloaded workbook has populated reason and
crosswalk columns.

### 9.18 Docs refreshed for this session's work (2026-07-27)

`docs/_gen_docs.js` (the Feature List + User Guide generator) gained the new capabilities, and
the Feature List was regenerated:
- New H2 **"Multi-sheet source workbooks"** — every sheet imported, one conversion / one bundle,
  per-interface-sheet routing, different row grains respected, union of source columns.
- New H2 **"Mapping documentation & export"** — banded workbook, flat All Fields sheet, reasons
  per alternative, value-crosswalk column, available in both canvas and table.
- **Output generation** gained "Explicit mapping outranks constants" and "Approved defaults
  reach the file".
- **Learning & reuse library** gained "Retired learnings stay retired", "Restore a retired
  learning", "Defaults learned on save", "Editable saved rules".

Content verified by reading `word/document.xml` directly — note `markitdown` does NOT return
docx table cells, so it reports these as missing; don't use it to check this file.

**PDF regeneration is blocked by file locks.** Six `docs/.~lock.*.pdf#` files exist, i.e. the
PDFs are open in a viewer on the Windows side, and LibreOffice fails with
`Io Class:Abort Code:27` when overwriting. The refreshed copy was therefore written as
`docs/FeatureList_27Jul2026.{docx,pdf}`. Close the open PDFs and re-run
`node docs/_gen_docs.js` + the soffice convert to refresh the canonical filenames.

**Screenshots — NOT added, and why.** The request was to include screenshots. That needs two
things this session could not satisfy: (1) the deployed app is still the pre-fix build, so the
new surfaces (canvas Vet/Export, multi-sheet import rows, retired-learnings list) do not exist
to photograph yet; and (2) the app sits at the sign-in screen and entering credentials is
out of scope — the user signs in. Chrome IS connected (`Browser 1`, Windows) and the frontend
at `https://trinamix-conversion-frontend.onrender.com` responds, so once the user has deployed
and signed in, capture is a short task: navigate, screenshot each surface, drop the JPEGs in
`docs/_shots/`, and add `ImageRun` entries to `_gen_docs.js` beside the matching feature rows.

### 9.19 Header row: format-driven default + a toggle on Conversion Objects

**Default changed from object-driven to FORMAT-driven** — `output_service.py`:
```
_hdr = include_header if include_header is not None else fmt in ("xlsx", "template")
```
was `... else (not _is_supplier)`. That old rule meant only SUPPLIER csvs were headerless;
every other object (Customer, Item, BOM, Employee…) shipped its FBDI CSV **with** a header
row, which the Oracle loader reads as a data record and rejects — so those bundles had to be
hand-edited before loading. Now the filled Excel templates keep their column labels (they are
for humans) and every CSV / zipped CSV bundle is headerless (it is the machine-readable load
file). The explicit user toggle still wins over both.

**New Header Auto / On / Off control** on the Conversion Objects card in
`ProjectOverviewPage.tsx`, sitting with "Generate all & download (.zip)" and "Filled Excel
templates (.zip)" and applying to both. `headerMode` maps to `headerFlag`
(`auto -> undefined`, i.e. let the backend rule decide). Threaded through
`OutputApi.downloadAll(..., onTick, includeHeader)` -> `generateMergedAllAndWait` ->
`generateMergedAll`, which already accepted `include_header`; `downloadAll` had been
hard-coding `undefined`, so the toggle had no route to the backend from this screen.

Verified: backend parses; `tsc` clean for the new identifiers (the two remaining
`ProjectOverviewPage` errors are pre-existing `conversions`-possibly-null checks on untouched
lines). NOT verified live — after deploy, confirm a CSV bundle opens with data on row 1 and an
Excel template still shows its header row, then that On/Off actually flips both.

### 9.20 NextPower Supplier Conversion Strategy seeded as the governing authority

Source: `Next_Power_Supplier_Conversion_Strategy.docx` v1.0 (Sandeep Singhal, 13-Jul-2026),
section 7 "Defaulting Rules". Distilled into `backend/app/data/supplier_strategy_defaults.json`
and seeded by `seed_supplier_strategy_defaults()` (registered in `main.py` AFTER
`seed_supplier_default_values`, so on a clash the strategy wins). Seeded as client-scoped
`example_default` learnings, which places it ahead of the deterministic control constants and
ahead of AI in the mapping precedence — "followed first", as requested. Tombstone-aware.

**13 constants seeded** — Supplier: Tax Organization Type CORPORATION, Supplier Type SUPPLIER,
Business Relationship SPEND_AUTHORIZED, Payment Method G-Treasury (blank-only). Address:
Ordering Y, Pay Y. Site: Purchasing Y, Pay Y, Payment Terms Net 30 (blank-only), Payment
Method G-Treasury (blank-only), Invoice Match Option Receipt, Receipt Routing Direct,
Match Approval Level 3-Way.

**The strategy CONTRADICTED two hardcoded constants — now corrected.**
`_CONTROL_DEFAULTS` had `address name = "PRIMARY"` and `supplier site = "PRIMARY"`, and both
sat in `_AUTHORITATIVE`, so they overwrote whatever was mapped. The strategy states
**Address Name = City Name** (e.g. Austin) and **Supplier Site = BU + City** (e.g. US-Austin).
Both were removed from `_AUTHORITATIVE`; they remain in `_CONTROL_DEFAULTS` purely as a
last-resort fill when the source column is entirely empty. Replayed: mapped values
`Austin/Dallas` and `US-Austin/US-Dallas` now survive; a blank column still falls back to
PRIMARY rather than shipping empty. **This is the documentary root cause of QA issue #8** —
the earlier fix was right, and the signed spec now confirms it.

**Deliberately NOT seeded** (recorded in `open_items` in the JSON):
- **Procurement BU** — derived from Primary Subsidiary via a Subsidiary-to-Procurement-BU
  crosswalk the strategy itself says is "to be finalized before the Supplier Site FBDI is
  generated". It does not exist yet; inventing it would put wrong BUs into a file that looks
  correct.
- **Business Relationship = PROSPECTIVE** for pending-approval suppliers — needs the
  approval-status source column confirmed. Only the SPEND_AUTHORIZED default is seeded, so
  pending suppliers currently get the approved value and must be reviewed.
- **Address Name / Supplier Site derivations** are mappings, not constants (`derive` rows are
  counted and skipped by the seeder). Address Name <- city is already a seeded eBOS
  column_mapping; the `BU + City` concatenation for Supplier Site still needs a CONCAT rule
  once the Procurement BU crosswalk lands.

**Mapping workbook `NXT Supplier Mapping (1).xlsx` — read, NOT uploaded.** Sheets:
`Source Mapping`, `Oracle-NetSuite-SyteLine` (164 rows x 92 cols — the real
Oracle-field / NetSuite-column / eBOS-column grid, header on row 0-2 with a "Proposed to be
inactive" + "Assignee" band), `AllVendorsTestPhoenixResults` (7,496 rows). Uploading it into
the tool needs the running app (Mapping Documents > Upload document) and the session is at the
sign-in screen — credentials are the user's to enter. Once signed in, that upload produces a
reviewable proposal with conflict detection against these strategy learnings.

### 9.21 Analyst rules from the 28-Jul live review (supplier header)

Captured in `supplier_strategy_defaults.json` under `analyst_rules`, seeded by the same
`seed_supplier_strategy_defaults()`:

| Field | Rule | Status |
|---|---|---|
| Alternate Name | Blank it when it duplicates Supplier Name | **Implemented** — new engine rule `BLANK_IF_EQUALS` |
| Tax Organization Type | CORPORATION | Already seeded (strategy 7.1) |
| Business Relationship | SPEND_AUTHORIZED | Already seeded (strategy 7.1) |
| Inactive Date | Always blank | Seeded as `suppress_field` |
| Customer Number | Always blank | Seeded as `suppress_field` |
| Parent Supplier | Parent Vendor Id → row where Internal Id matches → that row's Name | **NOT implemented** |

`BLANK_IF_EQUALS` (engine.py) compares case/whitespace-insensitively, so
`"ACME  Inc "` vs `"acme inc"` is treated as a duplicate. Replayed against the real workbook
values: `Advantage Electric Supply`/`Allied Electronics` blank out, a genuine alternate
(`Trading As Northwind` vs `Northwind Traders Ltd`) is preserved.

**Parent Supplier needs a new rule type.** It is a SELF-JOIN across the extract — resolve the
parent's *name* by looking up another row — and `apply_rule` is row-local: it only sees the
current row, so it cannot reach the row whose Internal Id matches. Implementing it means a
`SELF_LOOKUP` rule resolved at frame level in `_transform_frame` (build an
`Internal Id -> Name` map once per frame, then map the `Parent Vendor Id` column), not inside
`apply_rule`. Config is already recorded in the JSON.

**Analyst wrote "Corporation", seeded as CORPORATION** — that is the Oracle lookup CODE and it
matches strategy 7.1. Flag if the target instance actually expects mixed case.

**Needs a further deploy.** 9.20 and 9.21 both landed after the last `launch_git.bat`, so the
strategy + analyst learnings are NOT yet on the running backend. The mapping workbook was
copied to `NXT_Supplier_Mapping.xlsx` in the repo root so the browser can reach it, and the
Mapping Documents screen already lists an older `NXT Supplier Mapping.xlsx`
(75 new / 17 same / 25 conflicting, awaiting review) whose analysis PREDATES the strategy seed
— re-analyse after deploying so conflicts are judged against the strategy.

### 9.22 "Filled Excel templates" silently fell back to CSV column structure

**Root cause of "the FBDI template download follows the CSV column structure".**
`generate_output_artifact` materializes the bundled Oracle workbook for `fmt="template"`;
if there is no stored file it did `fmt = "xlsx"` with only a comment. That fallback builds a
FRESH workbook whose columns are the MAPPED set — i.e. exactly the CSV column structure —
instead of the Oracle template's own layout, and the user got no signal that "Filled Excel
templates (.zip)" had not used a template at all.

`fill_template()` itself is correct: it opens the real .xlsm/.xlsx, matches df columns to
template columns by normalised header, clears the shipped sample rows, and preserves macros.
So the fix is not in the fill logic — it is that the template FILE is missing for that object.

Now logs a WARNING naming the conversion, object and template, and sets `_template_fallback`.
**Still to do:** surface that flag on the ConvertedOutput record so the UI can show
"template not used — re-upload the FBDI template", rather than only appearing in server logs.
**Action for the user:** re-upload the supplier FBDI templates (FBDI Library > Templates) —
without a stored workbook the template download can never match the original layout.

### 9.23 Delivery Method rule (captured, NOT yet seeded — conflicts with an existing rule)

`Delivery Method`: if the remittance EMAIL column has a value -> `EMAIL`; else if the
remittance FAX column has a value -> `FAX`; else blank. EMAIL takes preference, encoded as
CASE_WHEN branch order with `notblank` ops (both already supported by the engine).

**Conflict — do not seed blind.** `supplier_transform_mappings.json` ALREADY seeds Delivery
Method / Delivery Channel from the Email/Fax **transaction flags**. This new rule keys off the
remittance email/fax **value columns**. Two CASE_WHEN rules on one target field will fight;
reconcile which is authoritative first. The two source column names also need confirming
against the actual extract before this can be seeded.

---

## 10. Continuation session — 2026-07-28

Everything in section 10 is **deployed** (user ran `launch_git.bat` after `npm run build`
in `frontend/`, not the repo root — `package.json` does not exist at the root).

### 10.1 The central lesson repeated: enforce at WRITE time, verify on REAL output

Two rounds of live testing this session both ended the same way: a rule that looked applied in
the UI/registry was **not** in the generated file. The fix is always to enforce it at the point
every value passes through, then replay the real output to prove it. Do not trust a seeded
learning to reach the FBDI — check the file.

### 10.2 Strategy blanks were being resurrected downstream (FIXED)

`strategy_overlay` blanked fields correctly inside `_transform_frame`, then **two later stages
put values back**:

* `_SEQ_FIELDS` auto-numbered Customer Number -> `100000, 100001…`
* `_CONTROL_DEFAULTS` refilled RFQ Or Bidding -> `Y`, because "column is entirely empty" is
  exactly what a *successfully blanked* column looks like.

Fix: `strategy_overlay.blank_fields(obj)` returns the control-default key spellings, merged
into `_apply_control_defaults(suppressed=…)`. Verified on the real 5,831 / 7,099 / 7,339-row
files: `supplier name new`, `customer number`, `rfq or bidding`, `enable b2b messaging`,
`invoice amount limit` all went from fully populated to 0 populated.

### 10.3 BLANK_IF_EQUALS could never have worked (FIXED)

Configured as `other_column: "Supplier Name"` — a **target** field — but the engine's per-row
context holds **source** columns. The lookup missed on every row and all **3,407** duplicate
Alternate Names survived. Fixed with `strategy_overlay.apply_frame_rules(df, obj)`, a
frame-level pass where both sides are output columns. Now 0 duplicates, 2,278 genuine aliases
preserved.

### 10.4 CONCAT emitted a bare separator (FIXED)

`Supplier Site*` was `CONCAT("Country Code", "City")`; **neither column exists in the NetSuite
extract**, so all 8,561 rows shipped the literal `"-"` into a required, must-be-unique key.
`engine.py` now returns the incoming value when every CONCAT input is missing, so the
misconfiguration is visible instead of manufacturing a bad key. **The real source columns still
need naming by Sandeep.**

### 10.5 The FBDI templates bundle shipped 2 of 7 objects (FIXED — three stacked causes)

1. `regenerate=False` was read as *"omit any object with no file in this format yet"* rather
   than *"don't rebuild what exists"*. All 7 objects had csv-family artifacts; only 2 had
   xlsx/xlsm. Missing objects are now built inline. Response carries `X-Files-Expected` and
   `X-Objects-Skipped` so a short bundle is machine-detectable.
2. Of the 2 delivered, **only `06_Supplier_Banks.xlsm` was a real Oracle workbook**.
   `03_Supplier_Site.xlsx` was a synthesised fallback — correct columns (they come from the
   parsed field records) but no "Instructions and CSV Generation" sheet, no macros, `.xlsx`
   not `.xlsm`. Nothing in the file said so; it is convincing enough to be mistaken for real.
   **Diagnostic: compare `wb.sheetnames` against the bundled template — a missing Instructions
   sheet means it is synthesised.**
3. Root cause: template records with no stored file (ephemeral Render disk after redeploy).
   `materialize_template_file` now falls back to the workbook bundled in
   `app/data/fbdi_templates/`, matched on business object or interface table, before degrading.
   All 9 resolve, including messy spellings; unknown objects return `None` with a warning
   rather than a wrong file. **This supersedes §9.22's "re-upload the templates" action — that
   would have created duplicate records.**

### 10.6 Auto-generated key numbers removed (analyst instruction)

> "due to that number we have so many duplicate fields, please do not generate it if its not
> mapped" … "same for other auto generated fields as well, if there is no input in the tool or
> no mapping, do not generate auto number."

`_apply_control_defaults` no longer invents values for **any** of `_SEQ_FIELDS`
(`suppliernumber`, `supplierpartynumber`, `partynumber`, `customernumber`,
`customeraccountnumber`). Mapped values are kept; gaps stay blank; the `_SEQ_PREFER_SOURCE`
split is gone. Three reasons this mattered:

* the extract leaves Supplier Number empty on every row, so every supplier got a fabricated
  number that would have become its **permanent Fusion supplier number** with no legacy link;
* Supplier Number is the **de-dup business key** — distinct values per row made genuine
  duplicates look unique and the golden-record collapse could never fire;
* reviewers saw five "3X Motion Technologies" rows numbered 100005-100009 and read them as
  five different suppliers.

`Batch ID` (`900001`) is deliberately **kept** — one required constant identifying the load
batch, not a per-row invented identity.

**WATCH:** `Party Number` / `Customer Account Number` previously fed the Customer 19-sheet
parent/child linkage. The customer glue block generates its own `ORIG_SYSTEM` keys separately
so breakage is not expected, but **the customer load has not been exercised since this change.**

### 10.7 NEW FEATURE — duplicate + cleansing review with user decisions

User asked for: duplicates and cleansing issues highlighted, user chooses what they want, and
the CSV/FBDI generated accordingly. Chosen design (user-confirmed): **both** surfaces (Output
Preview tabs + a pre-generation gate), verdicts **pick survivor / merge / keep all / exclude**,
and decisions **learn across conversions**.

**Key design decision — stable identity hashes, never row numbers.** `/duplicate-candidates`
reports `member.row` as a positional index into the frame it built for that request;
generation builds a different frame. A decision stored against a position would eventually drop
the wrong supplier. Keys are sha1 over normalised identity-field values (case/whitespace
insensitive); cluster keys sort members first so a re-scan does not appear to lose decisions.

New files:

* `backend/app/models/row_decision.py` — `RowDecision` (collection `row_decisions`), registered
  in `database.py`.
* `backend/app/services/decision_engine.py` — pure/pandas-only, unit-testable: `row_key`,
  `row_keys_for`, `cluster_key`, `golden_record`, `apply_decisions`.
* `backend/app/services/decision_service.py` — Mongo-aware wrapper, `annotate_clusters`,
  `load_learned_keep_all`.

Wiring: applied in `build_converted_dataframe` right after `_merge_dedupe_frames` — the single
point the CSV bundle, xlsx, filled template and both project-level zip downloads all read from.
**Also applied to each `collect_frames` entry** (which is a `(frame, source_columns)` tuple),
otherwise an excluded supplier survives on the Address/Site sheets of a multi-source conversion.

Endpoints on `output_router` (prefix is `/api/conversions`, **not** `/api/output/conversions`):
`GET /{id}/review`, `POST /{id}/decisions`, `DELETE /{id}/decisions?decision_key=`, plus
`/{id}/duplicate-candidates` now returning `cluster_key`, `member_keys`, `decision`,
`identity_columns`, `decided_count`, `undecided_count`.

Cross-conversion learning uses `RowDecision` itself (client_id + target_object + `keep_all`),
**not** `LearnedMapping` — that is field-grain and its reseed/tombstone rules would fight a
row-grain verdict.

Frontend: `OutputPreviewPage.tsx` reworked (nested ternary -> `renderTab()`), cluster cards with
survivor radios, 4 actions, undo, bulk "merge all ≥0.95", new **Cleansing** tab from
`reviewBundle`, generation-gate confirm modal, and a "decisions changed — re-generate" banner.

Verified: 19/19 engine unit tests + 12/12 against the real 5,831-row supplier file
(**54 clusters / 114 duplicate rows** found). Merge produces a golden record no less populated
than the best single row; keys survive a full frame reshuffle; no rows invented across a bulk
merge of all 54.

**Known gap:** a *learned* `keep_all` cannot be undone from this screen — `DELETE /decisions`
only clears the current conversion's rows, so Undo is hidden for learned clusters. Needs a
management surface.

### 10.8 Customer field mapping seeded from the analyst workbook

`NXT Customer Field Mapping (6).xlsx` -> "Source Files Mapping" tab. 49 rows have an Oracle
Field Name but only the **26 Green/Mapped, Bring-to-Oracle=Yes** rows were seeded (the 23
Yellow rows are notes — "contact ?", "creditr hold", "part relation ship" — not field names).
29 pairs written to `customer_field_mappings.json`; verified every source column exists in the
real extract tabs and no target has competing sources.

**The workbook contradicted 4 existing seeds**, so `_seed_catalog_file` gained
`replaces_source_field` — it retires superseded learnings instead of leaving two competing
column_mappings per target (the QA #6 defect class):

| Target | Was | Now |
|---|---|---|
| Customer Account Source System Reference | `entitynumber` | `entityid` |
| Account Number | `entitynumber` | `entityid` |
| Party Original System Reference | `entityid` | `id` |
| Account Description | `custentity_enl_legalname` | `companyname` |

Not seeded, recorded as `_open_items` in the JSON: `datecreated` (workbook maps both it and
`startdate` to Account Established Date), `externalid -> "DFF"` (a category, not a field),
`fax -> "Phone Line Type/Number"` (a separate contact-point ROW in `HZ_IMP_CONTACTPTS_T`, needs
row fan-out), and **`Party Number`** — still fed by `entityid`, which is now the *account* key,
so Party OSR and Party Number derive from different columns. Incoherent; needs a decision.
Also: the workbook's `addressinternalid` does not exist — the Address tab header is `internalid`.

### 10.9 UNVERIFIED — live Output Preview hung

After deploy, `/conversions/6a68dd690f420d1177f743bd/output` was **still spinning after ~40s**.
Could be a Render cold start; could be a regression. **First suspect: the `collect_frames`
tuple unwrap in §10.7** — newest, least-exercised edit in the request path. Reload once; if it
hangs again, check Render logs for a traceback around `apply_conversion_decisions` /
`collect_frames`. Everything verifiable offline passes; only the live round-trip is unconfirmed.

### 10.10 Open items carried forward

**Blocked on people:**

* `Supplier Site*` — real source columns for the country/city concat (Sandeep).
* **`Address Name` disagrees between sheets** — city on the Address file, street address
  (`Rua Pará, 126`) on the Site file. That is the FK between them; **the site load will fail on
  every row.** Highest-severity open item.
* `Third_Party_Pay_Relationships` — 12,630 auto-populated junk rows, `Remit-to Supplier*` = `Y`
  where a supplier *name* belongs. Nothing in scope asked for this sheet.
* `Procurement BU` ships the raw NetSuite subsidiary path
  (`Nextracker Consolidated : Brazil Consolidation : Nextpower Brasil Ltda`) — crosswalk still
  missing, and it is now visible in a client-facing file.
* Supplier Number strategy (Raja) — now blank; decide legacy `id` vs Fusion auto-assign.
* Match Approval Level code (Ramanjaneyulu — `3-Way` is still a guess); payment-terms naming
  (Finance); bank sheet review.

**Code:**

* Learned `keep_all` needs an undo/management surface (§10.7).
* Customer load unexercised since the auto-number change (§10.6).
* Third Party Pay Relationship absent from all seeds.
* Parent Supplier `SELF_LOOKUP` — frame-level self-join; engine is row-local.
* Taxpayer Country needs routing through `COUNTRY_ISO2` (overlay alone won't fix).
* Allowed-value validation from template cell comments.
* Surface `_template_fallback` in the UI.
* **187 pre-existing TypeScript errors repo-wide** (`npm run lint` = `tsc --noEmit`; `npm run
  build` = `vite build`, which does NOT typecheck). 5 in `api/index.ts` are types imported from
  `@/types` that exist nowhere — fixing them means inventing API contracts.

---

## 11. Continuation session — 2026-07-29 → 2026-08-03

The long one. Sections 11.1–11.4 are the theme; 11.5–11.14 are the individual
changes; 11.15 is the state of the repo, which needs attention before anything
else; 11.16 is the agreed next change.

### 11.1 The finding that explains most of the week: recorded and never read

Almost every "the screen says one thing and the file says another" report this
session resolved to the same shape — a value is **written by one path and read by
none**, or written into a copy that the reader never consults. Confirmed instances,
all fixed, all independently reported as different bugs:

| Symptom the analyst saw | Actual cause |
|---|---|
| Rule saved, output unchanged | stale flag written by every path, read by none |
| Mapping edited, other conversion unaffected | `mapping_sync` declared nowhere, so `response_model` silently stripped it |
| Client rule never applied | a `cconv is not None` guard that could never fire — the resolver substituted the default client id upstream |
| Generation "just stops", no error | `output_status` / `output_error` recorded on the document, absent from the response schema |
| Wrong template used | 17 template records claiming one object, none carrying a notion of which was current |
| Rule reaches 1 conversion of 6 | object-scoped fan-out, fixed in **three separate call sites** because there was no single place to fix it |

**The lesson to carry:** a fix is not done when the value is stored. It is done when
you have traced the read path to the artifact and seen the value arrive. This is
§10.1 restated with a week of new evidence, and it is why §11.16 exists.

### 11.2 Client-scoped rules — `CLIENT_RULE` (the analyst's own solution)

The analyst cut through the scoping debate:

> "whatever user is saving or changing in mapping, store it or save it as mapping
> rule from client perspective (in this case NextPower), so it will correctly
> propagate through older projects and conversions and newer projects and conversions"

Implemented in `learning_service.py` as a sentinel:

```python
CLIENT_RULE = None          # target_object is None  ->  belongs to the CLIENT,
                            # not to one business object
```

* `object_keys_with_client_rules(obj)` returns `[*object_keys_for_object(obj), CLIENT_RULE]`.
* `apply_learned_to_conversion._q()` widened from `target_object == obj` to
  `target_object: {"$in": _obj_keys}` — one change that made every existing
  conversion see client rules without touching the callers.
* `record_learning_from_mapping` and `record_learning_from_rule` now write a
  **single** `target_object=CLIENT_RULE` row instead of one row per object.
* `propagate_learning_to_open_conversions` skips the business-object filter entirely
  for client rules, and records a **reason string per skipped conversion**
  (`fan_skipped`) — previously a rule that reached nothing looked identical to a rule
  that reached everything.

Covered by `backend/tests/test_global_rule_setter.py` (in-memory Beanie fake, no DB).

### 11.3 Both authoring surfaces write the same kind of row

The analyst named the two places precisely:

> "there are two places to change the rule or mapping using plain text, one is the
> yellow global location, one is inside custom transformation section for each
> column mapping"

Both now converge on `CLIENT_RULE`. In `steering_service.py`, `_learn` /
`_upsert` / `_suppress` **return the row they wrote**, and `touched` carries
`(kind, field, lm)` so the fan-out operates on that exact row rather than
re-querying and hoping to find it — the re-query was ambiguous whenever two rules
touched the same field in one save.

`_upsert` also **unions** the `sheets` list instead of replacing it (CW row 30):
scoping a rule to a second sheet used to silently unscope it from the first.

### 11.4 Latest-wins precedence, applied to templates as well as learnings

> "Mappings, learnings and user inputs should be stored in the same place with date
> (with respect to client and source), whichever is latest … and the same will be
> used for existing projects and future projects"

* `_effective_of(lm)` is the single ordering key; sorts are `(-effective_date, …)`.
* `FBDITemplate.updated_at` added (`models/fbdi.py`); HDL template resolution sorts
  candidates by `updated_at`, then by sheet count as a tiebreak.
* On the deliberate-override question — "what happens when an analyst maps one
  conversion differently from the client standard" — the analyst was explicit:
  **"that's fine, the analyst mapping wins as that's the latest mapping as per
  date."** No per-conversion scope was added, and none should be.

**Trap, already hit once:** `effective_date` must never move on a read. A startup
seed that finds nothing to do must not re-stamp — `captured_at` did exactly that and
inverted precedence on every redeploy.

### 11.5 Supplier mapping v3 — the source column is the LAST column

Per Debayon Mallik: the mapping workbook's **"Source Table Column name"** (last
column) is the real source column, not the friendlier label earlier in the row.
Seeded from `NXT Supplier Mapping 3.xlsx` + `Tracker_Netsuite_Supplier_5.csv` into
`backend/app/data/supplier_source_mapping_31jul.json`.
Tests: `test_supplier_mapping_v3.py`.

### 11.6 Employee / Workday runs on HDL, not FBDI

Employee is **HCM Data Loader**, a different format end to end: pipe-delimited
`.dat` files with `METADATA` / `MERGE` lines, one zip per object. Delivered:

* `hdl_seed_service.ensure_employee_hdl` **reconciles** instead of skipping when a
  template already exists; `consolidate_employee_hdl` rebinds conversions to the
  current template and retires duplicates.
* `hdl_output_service._as_book` writes one sheet per object for the workbook path.
* UI wording is source-aware — `isHdl(c)` drives **"HDL download"** vs "FBDI
  download" and **"DAT files"** vs "CSV" (`ProjectOverviewPage.tsx`).
* Current template: `backend/app/data/hdl_templates/HDL_Template_Workday_Employee.xlsx`
  — **6 objects** (Location, Job, Position, Position Hierarchy, and two Worker tabs).
  Output shipping only 2 tabs was the symptom that led to the consolidation work.

**Reversible action taken:** `consolidate_employee_hdl` retired **17** template
records, including one named `Employee HDL Template (6 sheets)`. They are tombstoned,
not deleted — `include_retired=true` brings them back.

### 11.7 Supplier Site and Site Assignment never generated — `_RowWithTargets.__iter__`

Two of six supplier files were **absent from the bundle with no error on any screen**
for about a week. Cause:

```
KeyError: 0 | at engine.py:729 apply_pipeline <- engine.py:574 apply_rule
             <- engine.py:564 _first <- output_service.py:341 __getitem__
```

`_first` does `{norm(k): k for k in row}`. `_RowWithTargets` defined `get`,
`__getitem__` and `__contains__` but **not `__iter__`**, so Python fell back to the
legacy sequence protocol and called `row[0]` — `KeyError(0)` on the first step.
Fixed with `__iter__` / `keys()` / `__len__`, source columns shadowing targets exactly
once. Regression test: `test_row_wrapper_iteration.py`.

Two things made it expensive out of proportion to the fix, both worth remembering:
generation runs in a **background worker**, so it surfaced as "no output" rather than
an exception; and the wrapper had been verified through `get()` only, which is how the
*preview* path uses it — so every rule that ITERATES a row was broken from the moment
it shipped and nothing exercised that.

`operations.py::_run_generation` now records the exception **type plus the last four
frames** into `output_error`, which is what made this diagnosable at all.

### 11.8 Generated keys are generated once (CW row 23)

> "write a logic in which this should be generated once and next time onwards the
> same should be repeated, not different number each run"

A positional counter satisfies "unique" and "increments" and is still wrong: the
extract gets re-sorted and re-uploaded constantly, and each of those renumbers
everybody — which, for Party Number, means a second load creates **duplicate parties
instead of updating the first**. So the number is a function of a **natural key**,
persisted in `SequenceAllocation` (`models/sequence.py`, `services/sequence_service.py`).

Guaranteed by `test_sequence_stability.py`: same run → identical; reordered →
identical; rows added → existing untouched; rows **removed → the freed number is not
re-issued** (`MAX(seq)+1`, never `count+1`, because that number is already sitting in
a loaded Oracle record). People are numbered under their organisation
(`NXT000001_C1`), so the child counter is per parent.

### 11.9 Client is mandatory on project creation

`client_service.resolve_client_for_project` — required, case-insensitive name dedupe,
and `explicit_client_id_for_conversion` which returns **`None`** when a conversion is
untagged rather than silently substituting the default id (that substitution was what
made the §11.1 client guard unfireable). The create-project form is a **dropdown plus
a "create new client" link**, not a free-text box. Tests:
`test_project_requires_client.py`.

### 11.10 Download buttons renamed

"FBDI" → **CSV**, "Excel" → **FBDI Excel**; and for HDL sources, "HDL download" /
"DAT files" (§11.6).

### 11.11 Production breakage introduced and fixed in-session — read this before editing the frontend

`const isHdl = …` was defined **inside `downloadAllFbdi`** and referenced at component
render level. Result: `ReferenceError: isHdl is not defined` — **every project page in
production went blank.** Reported as "not able to open any project".

Why it got through: **`npm run build` is `vite build`, which does not type-check**
(§10.10 says this too), and `tsc` was run *before* the edit rather than after. Rule for
the next session: run `npx tsc --noEmit` **after** the last frontend edit, not before.

Also fixed: deep links 404'd on the static site → `frontend/public/_redirects`.
And `/api/health` now returns the deployed commit (`RENDER_GIT_COMMIT`, `main.py:304`),
because "is this actually deployed?" cost real time twice.

**Related self-correction:** a blind `sed` on `fbdi.py` broke a conditional —
`"status": "parsed" if parsed["fields"] else "manual"` became
`"status": "parsed", "updated_at": datetime.utcnow() if parsed["fields"] else "manual"`.
Do not `sed` across a ternary.

### 11.12 Client-facing architecture overview (2026-08-03)

`docs/Trinamix_Architecture_Overview.docx` — two pages, landscape. Page 1 is the
diagram; page 2 is a four-stage table and the two claims (decisions made once;
runs repeatable).

Written to be **shareable without being replicable**: it names capabilities, never
construction. No framework, datastore, host, model provider, module, collection or
endpoint appears in it, and the caption states the boundaries are indicative so the
boxes cannot be read as a component decomposition. Verified by extracting the text and
scanning for implementation vocabulary — zero hits. **Re-run that scan after any
edit.**

Sources: `docs/Architecture_Overview.html` (diagram source of truth) →
screenshot the `.card` element → `docs/architecture_diagram.png` →
`node docs/build_architecture_docx.js`.

### 11.13 Test suite

**62 test files, all green** (~2,030 individual checks). Run them with:

```bash
cd backend && for f in tests/test_*.py; do PYTHONPATH=. python "$f" || echo "FAIL $f"; done
```

`pandas` is pinned at `/tmp/pin/bin/python` in the sandbox. **Nit:**
`test_template_fill.py` is the only file without the `sys.path.insert` preamble, so it
needs `PYTHONPATH=.`; give it the preamble when convenient.

New this session: `test_global_rule_setter.py`, `test_supplier_mapping_v3.py`,
`test_project_requires_client.py`, `test_sequence_stability.py`,
`test_template_recency.py`, `test_row_wrapper_iteration.py`,
`test_latest_decision_wins.py`, `test_tombstone_guards.py`,
`test_hdl_template_conformance.py`, plus ~25 more.

### 11.14 Live verification performed

Three projects (Supplier, Customer, Employee) were created against the live
deployment via browser control, using the analyst's real extracts, and the generated
bundles were checked against the CW_Issues list. Findings fed §11.6 and §11.7.

### 11.15 REPO STATE — do this first

At the close of this session the working tree carried **84 modified files
(+8,075 / −1,503) and ~60 untracked files**, and `HEAD` was **14 commits ahead of
`origin/main`**. `git push` from the cloud sandbox fails — no credentials — so the
push has to happen from the laptop.

Untracked and worth committing: `backend/app/services/template_comments.py`,
`backend/app/data/{customer_rules_nextpower,customer_sheet_scope,customer_fbdi_column_order,hcm_source_mapping,supplier_corrections_30jul,supplier_source_mapping_30jul}.json`,
`backend/app/data/hdl_templates/`, all 35 new `backend/tests/test_*.py`,
`docs/CODEBASE_GUIDE.md`, `docs/ONE_DATED_STORE.md`, `docs/Trinamix_Codebase_Guide.docx`.

Untracked and **should not** be committed: `_arch/`, `_docs_build/` (build scratch,
rendered page JPEGs), `COMMIT_MSG.txt`, `launch_git.bat.txt`, `STATUS_30Jul.md`,
`docs/lu105gr309.tmp`, `docs/.~lock.*#` (LibreOffice locks).

### 11.16 THE NEXT CHANGE — one dated store

Specified in full in **`docs/ONE_DATED_STORE.md`**. Agreed with the analyst on
02-Aug-2026 and committed to as the next piece of work, before anything else.

In one line: **every statement about how a field should be mapped becomes a dated
entry keyed `(client_id, source_erp, target_field)`; newest wins; per-conversion
mapping rows become a view regenerated from it.**

Order of work is in that document and matters — resolver first (pure, unit-tested,
no callers), then backfill, then route all six write paths through one function, then
reads, and **only then delete the copy paths**. That deletion is the proof it landed.
Do not merge writes before backfill or existing projects lose their history.

The reason not to re-litigate it is §11.1: today there are two stores — `LearnedMapping`
(dated, client+source scoped) and `MappingSuggestion` (per-conversion rows, which
generation actually reads) — and the library is **copied** into the rows. Every
disagreement between screen and file this week was those two copies diverging. No
amount of fan-out fixing closes that, because the copying is the defect.

### 11.17 Open items carried forward

**Still open from §10.10** — all of it, unless listed as fixed above. In particular
the `Address Name` FK disagreement between the Address and Site files remains the
highest-severity data item, and it is blocked on people, not code.

**New this session:**

* Customer `LEGACY` override — control default still beats the analyst default
  (CW rows 28/29/34/36).
* Supplier Site / Site Assignment generation **confirmed fixed in tests, not yet
  re-confirmed on a live run** after §11.7.
* Learning Centre: deleted items reappear; a saved custom transformation does not
  populate the dialog when it is reopened.
* Rule dialog must **render** the `target_fields` group — the backend half of CW row
  35 is done (`/source-columns` returns it), the frontend half is not.
* Sheet-picker editor in Learning Centre (CW row 11) — unlocks rows 12/13/14/25/33.
* Source-provenance tracking (CW rows 17, 37, 38).
* A `SEQUENCE` rule type, to wire Party Number to the §11.8 allocator from the rule
  editor rather than only from code.
* General (non-HDL) template resolution should order by `updated_at` — only the HDL
  path does today.
* Surface decision dates in Mapping Review, so "latest wins" is visible rather than
  merely true.

---

## 12. Continuation session — 2026-08-03 (one dated store, run report, Output Preview)

Four changes shipped and deployed (backend commit `b424068c` and later). Test
suite: **900 passing, 13 skipped, 0 failing** (747 at the start of the session).

### 12.1 The one dated store — `docs/ONE_DATED_STORE.md` steps 1–4

`services/mapping_store.py` is the whole rule.

* Every statement about how a field should be mapped is a dated entry, keyed
  **`(client_id, source_erp, target_field)`**. No object scope, no project scope,
  no per-conversion override.
* Four decisions: `source_column`, `default_value`, `suppress`, `rule`.
* Workbook, gold, learning capture, steer box, grid edit and custom rule all
  write through **one function**, `record_decision`.
  `tests/test_one_dated_store_writes.py` walks the AST of every module and fails
  if anything else constructs a mapping-decision `LearnedMapping`. Three
  exceptions remain, each asserted to be a kind that is NOT a mapping decision:
  `crosswalk` (one row per source VALUE), `file_signature`, and the Learning
  Centre's own "add" button.
* **Newest wins.** `captured_from` / `captured_by` are provenance and are read by
  nothing that picks a winner.
* `effective_date` never moves on a read. A bundled file with no date carries
  **no** date rather than today's — a seed stamping itself `now` on every boot
  would out-rank every instruction ever given, which is what `captured_at` did.

**Six precedence tiers deleted**, each of which competed with the date and
disagreed with the others: suppression-loses-to-mapping; `_candidate_order`
(strong-transform ranking); a second date ordering for constant defaults;
object-key spelling widening (`$in` over five spellings); `>` vs `>=` disagreeing
on an exact tie between propagation and the blank/rule corrections; and
`_eligible`, which had **no date test at all**, so a human approval was
permanently immune.

**Backfill** — `services/mapping_store_backfill.py`. Every human-decided
`MappingSuggestion` becomes an entry carrying its existing `approved_at`; every
undated library row takes `effective_date` from `captured_at`, once. Runs at
startup after the seeders and at `POST /api/learned-mappings/backfill-dated-store`.
Idempotent; a run with nothing to do writes nothing.

**Reads** — generation resolves through the store before building the file and
writes only rows whose content actually changes. The `_heavy` gate (objects with
>300 fields skipped the apply pass entirely) is **gone**: it made the 19-sheet
Customer and Item loads the most likely to ship against a stale copy.
`MappingSuggestion` gained `derived` / `derived_from`; a person's edit clears the
flag and becomes its own dated entry.

**⚠ Behavioural consequence, measured not estimated.** Against the JSON in
`app/data/`: 675 seeded entries, **67 target-field names claimed by more than one
object, and 41 where the objects disagree** — those now resolve to one winner.
Affected names include Address Line 1/2/3, City, Country, Postal Code, State,
Phone, Email, Fax, Account Name, Account Number, Payment Terms, Payment Method,
First/Middle/Last Name, Supplier Name. What limits it: the apply pass only writes
a decision whose source column exists in that conversion's extract, so a Supplier
entry naming `address_1` is skipped on a Customer extract that has `addr1`.
**If a merged field is wrong, re-date the losing document** — that is a data
change, not a code change.

Sheet scope (`sheets` / `exclude_sheets`) is kept as an applicability predicate:
it is part of what the analyst said, not a tier competing with the date.

### 12.2 Output report — "what the tool did to the input file"

`services/conversion_report_service.py`, a pure openpyxl builder (dicts in, bytes
out), same palette as `mapping_export_service`.
`GET /api/conversions/project/{id}/conversion-report` collects and streams it.
Frontend: `OutputApi.conversionReport`, buttons in **both** places the bundle can
be pulled from on Project Overview.

Seven sheets: Summary (with a "how to read this" block), Mappings (with the
**authority** behind each decision), Cleansing (real before/after per rule),
Duplicates, Validation, Required fields, Run log.

**It reports the run, not a recomputation** — the `dq_report` persisted on
`ConvertedOutput`, the mapping rows as they stand, the row counts on disk. Tests
assert the collector calls none of `find_duplicate_clusters`, `apply_cleansing`,
`validate_frame`, `generate_output_artifact` or `generate_merged`. An unknown
figure prints a dash, never a zero. A section that found nothing still gets a
sheet saying so.

### 12.3 Output Preview went blank — React #310

`OutputPreviewPage` declared `useState` for `fixBusy`/`fixNote` ~470 lines
**below** `if (!project) return <PageLoader />`. First render (project loading)
ran two fewer hooks than the second; React throws "Rendered more hooks than
during the previous render". It took the page down on **every** load.

Both hooks moved above the guard. `tests/test_hook_order.py` now walks every
`.tsx` and fails on any hook below a component-level return; it treats two-space
indentation as the component's own scope so a `return` inside a callback is
ignored, and two of its cases feed it a known-broken and known-good component.
It passes across the whole frontend — Output Preview was the only violation.

**`components/ErrorBoundary.tsx`** now wraps every route. There was none before
(the CODEBASE_GUIDE flagged this), so the symptom of any render bug was a white
page — indistinguishable from a failed build, a failed deploy or a static-host
404, all three of which were investigated before the real cause. It names the
component from `componentStack`, translates React's numbered production errors
(#310 → "a hook is running conditionally, or sits after an early return"), resets
on navigation, and puts component + both stacks behind a "Copy details" button.

### 12.4 Cleansing tab now runs on open

Both checks on that tab are lazy (each builds the sheet frames). Column rules
already auto-ran but rendered **nothing** while working, so the panel doing the
most work looked the most broken; cleansing findings did not auto-run at all.
Both now run on tab open from one effect, once each, and both say what they are
doing ("Checking every column…", "Profiling the source file…", "Not checked yet").

### 12.5 `launch_git.bat` — deploy only

* **No test step.** Every patch has the suite run against it before it is cut;
  this machine has no Windows venv or backend deps, so the step could only print
  "cannot run" — and when it misfired it said "tests failed", which was false.
* **`--ignore-whitespace` on every `git apply`.** The working tree is CRLF,
  patches are cut on Linux with LF, and `git apply` matches context byte for
  byte — so hunks failed against visually identical lines. **This cost most of an
  afternoon; do not remove the flag.**
* `GIT_PAGER=cat` + `git --no-pager`. A long `diff --cached --name-only` opened
  `less` and stopped the script dead at a `:` prompt that looked like completion.
* Self-locating via `%~dp0`; commit verified before push; a patch that will not
  apply prints git's actual objection and distinguishes "partly applied" from
  "diverged".

---

## 13. OPEN ITEMS — start here next session

### 13.1 ~~Duplicate suspects reports nothing~~ — FIXED this session

Reported live on Supplier Import, NextPower: four rows reading
"Nanjing Roytek & 3X Motion Technologies Co., LTD" — byte-identical — under
supplier numbers 1416, 3567106, 3567111, 3792588, and the panel said
*"Scanned 3813 records — no near-duplicate entities above the match threshold."*

**Cause.** `_pair_score` was a weighted average over every identity field
non-blank in both rows. The name scored 1.0; the supplier NUMBER, a strong id
that differed, scored 0.0 **and kept its weight in the denominator**. A perfect
name match was averaged down to ≈0.5/0.8 = 0.63 against a 0.86 threshold and the
pair was discarded. Two rows under different supplier numbers is the *definition*
of the duplicate this function exists to find, so the scorer was treating the
very thing that makes it a duplicate as proof it was not one.

**Fix** (`services/entity_resolution.py`):

1. An exact normalised-name match short-circuits to confidence 1.0. Legal
   suffixes and punctuation are still normalised away first, so "ACME  Inc " and
   "acme, inc." match. A blank name never matches another blank name.
2. A strong id (taxid / number) that DISAGREES now **abstains** — it leaves both
   numerator and denominator — rather than voting against. One that AGREES still
   counts fully as positive evidence. Fuzzy fields are unchanged and still
   contribute in both directions.
3. Corroborating evidence is still reported on a short-circuit, so the UI keeps
   listing which fields matched.

Trade accepted: a few more false pairs on genuinely different companies with
near-identical names. A suspect is dismissed in one click; a missed duplicate
ships to Oracle and creates a second supplier record.

`tests/test_duplicate_scoring.py` — 13 tests, pure. Includes the four Roytek rows
verbatim, and a test that reconstructs the OLD formula and asserts it would have
failed, so the suite cannot pass by luck.

**Not verified against live data.** Re-scan Supplier Import and confirm the four
Roytek rows now cluster.

### 13.1a Drop Third_Party_Pay_Relationships from the Supplier Site workbook (NEXT)

Analyst, 03-Aug: the generated `03_Supplier_Site` filled-template workbook
carries a worksheet tab `Third_Party_Pay_Relationships`. It must not be there.
Third Party Pay Relationships is its OWN interface — `supplier_fbdi_file_names
.json` maps `thirdpartypayrelationships -> PozSupThirdPartyInt` — so it belongs
in its own file, not as a tab inside Supplier Site.

Already captured as data and read by nothing — the "shipped and inert" pattern
(CODEBASE_GUIDE §7.1). `app/data/supplier_strategy_defaults.json`:

    "blank_sheets": { "sheets": ["Third_Party_Pay_Relationships"],
      "status": "CAPTURED - NOT yet enforced. output_service._sheet_carries_data()
                 decides headers-only per sheet from whether any field is mapped;
                 it has no explicit always-blank list." }

Note the ask is STRONGER than the captured note, which assumed headers-only:
remove the tab entirely from the Supplier Site workbook.

Where to change it:
* `strategy_overlay` — expose `blank_sheets` (a `sheets_to_drop(object)` reader),
  so the list is honoured rather than decorative.
* `output_service.py:1889 _sheet_carries_data` / the template branch around
  `:1969-2099` — an explicit always-drop list, checked BEFORE the
  is-anything-mapped heuristic, so an accidental auto-map cannot resurrect it.
* `template_fill_service.fill_template` — needs to delete the worksheet from the
  openpyxl workbook, not merely leave it empty.

Test: assert the generated Supplier Site workbook's sheet names do NOT include
`Third_Party_Pay_Relationships`, and — per §7.1 — assert the DATA list is what
drives it, so the mechanism cannot go inert again.

### 13.2 Item Import shows 0 converted rows

"Syteline source → Item Import": Converted Data badge `0`, headers render, no
rows. Lineage 260, Cleansing 0. **Uninvestigated** — the browser call diagnosing
it timed out. Check whether the source dataset has rows, whether an artifact was
ever generated, and whether §12.1's 41 merged fields point Item mappings at
columns absent from a SyteLine extract.

### 13.3 Static site has no SPA rewrite (not code)

`render.yaml` carries the rule but is bound to services named
`trinamix-backend` / `trinamix-frontend`; the live sites are
`tx-conversion-workbench` and `trinamix-conversion-workbench`, created by hand,
so the manifest never governed them. Any refresh or pasted deep link returns
**404 with a blank body**, which looks exactly like a broken page.

Fix in the Render dashboard → Redirects/Rewrites → source `/*`, destination
`/index.html`, action **Rewrite**.

### 13.4 `derived` flag under-reports

`apply_learned_to_conversion` only writes when the store's answer differs from
the row, so a row that already matches never gets `derived=True`. Behaviour is
right (no needless writes) but the flag lies by omission, and the run report's
**Authority** column falls back to the approver instead of the dated entry.

### 13.5 Step 5 of `docs/ONE_DATED_STORE.md` — delete the copy paths

`apply_learned_to_conversion`, `propagate_learning_to_open_conversions` and the
fan-out plumbing still run; they now populate the view from the store instead of
implementing their own precedence. The doc calls their deletion "the proof the
change landed". Deliberately deferred — it invalidates ~40 tests.

### 13.6 The plan's own verification, never run

Regenerate Supplier, Customer and Employee and diff against the 02-Aug bundles.
Needs MongoDB and the bundles. Expect any difference to land on the 41 fields in
§12.1.

### 13.7 Files touched this session

`services/mapping_store.py` (new), `services/mapping_store_backfill.py` (new),
`services/conversion_report_service.py` (new), `learning_service.py`,
`catalog_seed_service.py`, `output_service.py`, `defaults_service.py`,
`example_learning_service.py`, `mapping_import_service.py`,
`mapping_ingest_service.py`, `steering_service.py`, `routers/operations.py`,
`routers/mapping.py`, `routers/learned.py`, `routers/manual_map.py`,
`models/mapping.py`, `schemas/mapping.py`, `main.py`;
`frontend/src/components/ErrorBoundary.tsx` (new), `App.tsx`,
`pages/OutputPreviewPage.tsx`, `pages/ProjectOverviewPage.tsx`, `api/index.ts`,
`types/index.ts`.

New tests: `test_one_dated_store.py`, `test_one_dated_store_writes.py`,
`test_one_dated_store_reads.py`, `test_conversion_report.py`,
`test_error_boundary.py`, `test_hook_order.py`. Twelve existing seam tests moved
to the new address rather than being weakened.

New endpoints: `POST /api/learned-mappings/backfill-dated-store`,
`GET /api/conversions/project/{id}/conversion-report`.
