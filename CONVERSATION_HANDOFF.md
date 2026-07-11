# Trinamix Conversion Workbench — Conversation Handoff

> Context document to continue this work in a new chat. Covers what the product is,
> the architecture, everything built/verified in the recent sessions, current state,
> known gaps, deliverables, and open next steps.
> Last updated: 2026-07-09.

---

## 1. What this is

**Trinamix Conversion Workbench** — an AI-assisted data-migration tool that converts
legacy ERP/CRM source extracts into **Oracle Fusion Cloud FBDI** (File-Based Data
Import) load files. Built for a client demo. Hosted on **Render**.

Primary proven flow: a single **Supplier** source extract → the full **6-file FBDI
supplier load** (Import → Address → Site → Site Assignment → Contacts → Banks),
auto-mapped and generated as load-ready CSV/FBDI, verified against a client
"gold-standard" output.

---

## 2. Stack, repo, deploy

- **Backend:** FastAPI + Beanie/Motor (MongoDB Atlas) + GridFS (durable file storage on
  Render's ephemeral disk); pandas + openpyxl for parsing/generation.
- **Frontend:** React + Vite + TypeScript, Tailwind, lucide-react, axios (`src/api/index.ts`).
- **Repo:** `github.com/SubratoBiswas/trinamix-conversion-workbench` (branch `main`).
- **Local path:** `C:\Users\SubratoBiswas\trinamix-conversion-workbench`
- **Deploy:** push to `main` → Render auto-builds. Deploys are run via `launch_git.bat`
  (cleans git locks, drops tracked bytecode, `git add -A`, commit, push). The commit
  message in the .bat is edited before each deploy.
- **Demo creds:** `admin@trinamix.com` / `admin123` (acceptable per user).

### Deploy gotchas (important)
- The Linux sandbox **truncates large files on read**, so `py_compile`/`tsc`/`ast` in
  the sandbox produce **false "unterminated"/"no closing tag" errors at file tails**.
  Verify real code via the **Read tool** (reads full file) and trust the Edit tool;
  Render builds from the real (untruncated) files. Frontend `esbuild` in the sandbox is
  Windows-built and can't run in Linux.
- `/tmp` is cleared between sessions — recreate scratch scripts.
- Deploy is done through the **Windows Run box via computer-use**: write the .bat path
  to clipboard, open Run, Ctrl+A, Ctrl+V, verify path, click OK. **Never type
  credentials into any field** (a past incident pasted an API key into Run; policy:
  credentials are prohibited from being entered into fields).

---

## 3. Core architecture concepts

- **6-object supplier FBDI load** with interface sheets: `POZ_SUPPLIERS_INT`,
  `POZ_SUPPLIER_ADDRESSES_INT`, `POZ_SUPPLIER_SITES_INT`, `POZ_SITE_ASSIGNMENTS_INT`,
  `POZ_SUP_CONTACTS`, `IBY_TEMP_EXT_PAYEES`/`IBY_TEMP_EXT_BANK_ACCTS`/`IBY_TEMP_PMT_INSTR_USES`.
- **Fan-out:** `object_fanout_service.generate_object_template_set(project, dataset,
  object_type)` — one dataset → all FBDI templates for the object (supplier→6;
  customer/item→single multi-sheet workbook). Catalog via `GET /api/conversions/object-types`.
- **Layered mapping pipeline (deterministic-first, AI-last):**
  1. Deterministic Python crosswalks — country→ISO-2, currency→ISO-4217, UOM, Y/N flags,
     rule-based name matching (`deterministic.py`, `RuleBasedMapper`).
  2. Learning-first — reusable `LearnedMapping` rules (column_mapping / example_default /
     suppress_field) keyed by `target_object`, auto-applied.
  3. AI (Anthropic) only for the residual `weak` targets, batched per sheet.
- **AI provider:** `get_mapping_provider()`; gated on `AI_PROVIDER=anthropic` +
  `ANTHROPIC_API_KEY`. Model persisted in `AppSetting` (`ANTHROPIC_MODEL`), selectable in
  TopBar: Haiku `claude-haiku-4-5-20251001`, Sonnet `claude-sonnet-4-6` (default),
  Opus `claude-opus-4-8`.
- **Gold reference standards (learning DB):** uploading a client "gold" output runs
  `example_learning_service.learn_conversion_from_example` → derives, per field:
  a **constant default**, a **source→target column mapping**, or a **suppression**
  (field left blank in gold). Stored as **global** `LearnedMapping` rows (kind
  column_mapping/example_default/suppress_field), reused across projects.
- **Force-apply at generate:** `generate_output_artifact` calls
  `apply_learned_to_conversion(..., force=True)` **before building every output**, so the
  stored standard (mapped columns + constant defaults + suppressions) always wins — even
  over AI-approved mappings (human "overridden" mappings preserved). This was the fix for
  "gold saved but not applied."
- **Control defaults + authoritative constants:** `output_service._apply_control_defaults`
  fills required control fields (Import Action=CREATE, batch id, sequence numbers, etc.)
  and overrides known gold-constant fields; skips suppressed keys.

---

## 4. Feature history (recent sessions) — all deployed

Chronological highlights (commit hashes where noted):

- Defaulted-state mapping panel; AI-inferred defaults; model fix to `claude-sonnet-4-6`.
- **The int() fix** in `llm_provider._parse_response` (`str(target_field_id)` not
  `int(...)`) — this is what made AI mapping actually apply (was silently falling back to
  rule-based on ObjectId strings).
- All 4 AI features: value crosswalks, batched smarter mapping, data-quality checks,
  load-error explanations — each **deterministic-first** to cut token usage.
- Per-conversion **Generate & download**; **Generate all & download (.zip)** pipeline.
- **AI model selector** (Haiku/Sonnet/Opus) for cost control.
- **Gold/prompt suppression** overrides aggressive AI mapping (blank-in-gold → not_applicable).
- **Auto-capture learnings** after each successful generate; learning-first mapping.
- **Bulk gold upload** on the Conversion Objects (Project Overview) screen.
- Casing fix + near-constant (≥90%) capture in `example_learning_service` (`_clean`
  preserves gold's exact case; dominant value learned as default).
- **Setup wizard Source step** → featured "Custom / Other · File upload" card (selects
  `custom`, sets FBDI target). File uploads always follow FBDI route (output_mode
  `fbdi_download`). Hide DB connection form in file mode; Continue no longer gated on DB creds.
- **Scope step fan-out preview** — a file detected as Supplier previews all 6 FBDI objects,
  selected by default with per-step toggles; deselected steps dropped on create.
- **Review step** file-mode aware: Source=File upload, Connection=uploaded filename,
  Implementation scope lists the FBDI conversions to be created.
- **Reusable reference standards:** backend `apply_learned_to_conversion` also re-applies
  learned constant defaults; `GET /api/learned-mappings/reference-standards` summary;
  persistent "reference standards on file" banner + per-object "gold on file (Nm Md Ks)"
  badge on Project Overview; matching chip on Mapping Review and Conversion Detail; per-object
  "Upload / Replace gold" on Conversion Detail.
- **Generate & download** now runs the whole pipeline in one click (gold + learnings +
  templates + deterministic + rule-based, AI only for residual) — separate "Apply gold now"
  button removed.
- **Convert-a-file (`/convert`) page** brought to parity with the wizard: fan-out
  "Conversions from your files (N)" section + "Implementation scope · Fusion Cloud modules"
  catalog with the object list; both reveal together **after Upload & analyze**; wizard-style
  **stepper** (Upload → Detect → Review & scope → Create & map).
- **Dataset dedupe on upload** (`9ede290`): SHA-256 of file bytes on `Dataset.content_hash`;
  identical re-uploads reuse the existing dataset. (Existing duplicates remain — use "Delete all".)
- **Large-file generation** (`c62a9c7`):
  1. **Chunked transform** — source processed in 25k-row windows and concatenated
     (row-local → byte-identical); bounds peak memory on tall/wide extracts.
  2. **Non-blocking** — each chunk runs via `asyncio.to_thread`.
  3. **Parallel objects** — "Generate all" runs objects through a concurrency pool (3) with
     a **live per-object progress panel** (queued → mapping → generating → done/failed,
     overall bar + %).

---

## 5. Verification status vs gold (Phoenix supplier dataset)

Method: gold `_Populated.xlsx` are the client's populated FBDI outputs; compare the tool's
output per column, aligning rows by "supplier name," classifying diffs as over-populated /
tool-blank / differ.

**Current column-level match = 546/592 (92%) on the six main sheets**, auxiliary sheets
perfect (Third-Party Pay 11/11, Bank Accounts 39/39, Payment Instructions 7/7):

| Object | Full match |
|---|---|
| Supplier Import | 145/156 |
| Supplier Address | 102/110 |
| Supplier Site | 189/199 |
| Site Assignment | 10/16 |
| Supplier Contacts | 87/90 |
| Supplier Banks | 13/21 |

Confirmed **byte-identical** after the chunk/thread/parallel refactor (small ~7.5k-row file
and a full 7,495-row "big file" both land at exactly these counts; big file exercised the
threaded single-pass branch — chunk-concat path triggers >25k rows).

---

## 6. Known remaining gaps (to push past 92%)

Two are **fixable in mapping** (recommended next work):
- **Bill-to BU** still maps to the supplier name instead of the Business Unit.
- **BU "\ Business Unit" suffix** — gold uses `Nextpower LLC Business Unit`; tool writes the
  bare `Nextpower LLC`. A deterministic suffix/crosswalk rule fixes Procurement/Client BU
  (Site Assignment), Procurement BU (Site), Business Unit Name (Banks) at once.

The rest are **source-data / formatting** (inherent to the extract): contact first/last name
(source doesn't split), address purpose per row (GENERAL/BILLING vs PRIMARY), address
casing/accents/postal hyphen, and supplier/payee number base offset.

Also: near-constant learned defaults slightly **over-populate** rows gold left blank (~10% of
rows on EMAIL channels / administrative contact) — acceptable tradeoff; could be tightened.

---

## 7. Deliverables produced (in the workbench folder)

- **`Source_to_OracleFBDI_Mapping.xlsx`** — standard master-data metadata mapped to Oracle
  FBDI. 10 tabs: Overview; NS-Supplier / NS-Customer / NS-Item; SyteLine-Supplier /
  -Customer / -Item; SF-Customer / SF-Item; Sources. Each row: source field (technical) →
  FBDI interface sheet + column, FBDI required flag, transform/crosswalk note, confidence.
  - Grounding: NetSuite Vendor verified from the NetSuite Records Browser; Oracle Customer
    FBDI tables from Oracle docs; Supplier from the tool's own templates; NetSuite
    Customer/Item, Salesforce Account/Product2, SyteLine use each system's standard schema.
  - "Sightline" was read as **Infor SyteLine** (the ERP already in the source list).
- **`FBDI_Templates/`** — the 8 corresponding official Oracle FBDI templates (verified by
  interface-table sheet names): Supplier Import/Address/Site/Site-Assignment/Contacts/Bank
  (`POZ_*`, `IBY_*`; the Bank one is a *modified AS400* variant), Customer (`HZ_IMP_*` +
  `RA_CUSTOMER_*`), Item (`EGP_SYSTEM_ITEMS_INTERFACE` + child tables).
  - NOTE: these were gathered from files already in the workspace; fresh copies could not be
    downloaded programmatically (see §8). Latest from Oracle:
    - Supplier `https://www.oracle.com/webfolder/technetwork/docs/fbdi-26b/fbdi/xlsm/SupplierImportTemplate.xlsm`
    - Customer `https://www.oracle.com/webfolder/technetwork/docs/fbdi-25d/fbdi/xlsm/CustomerImportTemplate.xlsm`
    - Item `https://www.oracle.com/webfolder/technetwork/docs/fbdi-26b/fbdi/xlsm/ItemImportTemplate.xlsm`

---

## 8. Constraints / policies observed

- **File downloads:** the approved web tools fetch **page text, not binary files**, and
  curl/wget/python URL fetching is prohibited (compliance). Binary `.xlsm` downloads must be
  done by the user, or reused from the workspace.
- **Credentials:** never enter passwords/API keys/tokens into any field; direct the user to
  do it. No financial trades/transfers. Sending messages / irreversible actions need explicit
  per-action confirmation.
- **Sandbox truncation** false-positives on compile checks — verify via Read tool.

---

## 9. Open next steps / backlog

1. **Bill-to BU remap** + **BU "\ Business Unit" suffix normalization** (deterministic) →
   expected to push past 92%.
2. Optionally **load Customer & Item FBDI templates into the tool** as target templates so
   those object conversions can generate (Supplier already loaded).
3. Optionally **wire the mapping-workbook crosswalks into the Mapping Knowledge Base**
   (seed `LearnedMapping` column_mapping rules per source system) so they auto-apply.
4. Consider lowering the 25k chunk threshold if chunk-concat needs exercising on smaller files.
5. Tighten near-constant default over-population on rows gold leaves blank.
6. Add **Salesforce Supplier** mapping tab if vendor migration from Salesforce is in scope.

---

## 10. Key files (backend)

- `app/services/output_service.py` — generation: `build_converted_dataframe` (chunked +
  to_thread), `_transform_frame`, `_apply_control_defaults`, `generate_output_artifact`
  (force-applies gold), multi-sheet CSV/zip output.
- `app/services/learning_service.py` — `apply_learned_to_conversion(force=)`,
  `capture_learnings_from_conversion`, `_business_object_for`.
- `app/services/example_learning_service.py` — learn gold example (`_clean` casing,
  near-constant capture, suppression).
- `app/services/mapping_service.py` — `run_mapping_suggestions` (deterministic+learning-first,
  AI for weak, then apply_learned).
- `app/services/object_fanout_service.py` — object-type catalog + fan-out.
- `app/services/dataset_service.py` — upload + profiling + **content_hash dedupe**.
- `app/routers/conversions.py` — generate-set, object-types, apply-reference-standard(s).
- `app/routers/learned.py` — reference-standards summary.

## Key files (frontend)

- `src/pages/ProjectOverviewPage.tsx` — Conversion Objects list, gold upload, reference-standard
  banner/badges, **Generate all & download** + live progress panel.
- `src/pages/ConvertFilePage.tsx` — `/convert` file flow: stepper, fan-out section, module catalog.
- `src/pages/ConversionDetailPage.tsx` — pipeline toolbar, per-object gold upload + banner.
- `src/pages/MappingReviewPage.tsx` — mapping canvas, learn-from-example, reference-standard chip.
- `src/components/setup/SetupWizard.tsx` — engagement wizard (Details/Source/Connection/Scope/Review).
- `src/api/index.ts` — API client (ConversionsApi, DatasetsApi, LearningApi, OutputApi, …).
