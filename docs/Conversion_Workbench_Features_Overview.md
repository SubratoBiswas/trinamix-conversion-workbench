# Trinamix Conversion Workbench — Features Overview

*A complete catalogue of what the tool does.*

---

## 1. Purpose

An AI-assisted data-conversion platform that turns legacy ERP/PLM master data into **Oracle Fusion load files** — **FBDI** (`.csv` in `.zip`) for ERP objects and **HDL** (`.dat`) for HCM — with every analyst rule captured as durable, reusable knowledge.

---

## 2. Sources (inbound)

- **Files:** Excel (`.xlsx`, `.xlsm`) and CSV/TSV, including messy real-world exports — robust parser handles magic-byte mismatches, HTML-as-xls, UTF-16, and corrupt stylesheets.
- **Systems:** NetSuite, Infor SyteLine, Arena / eBOS (EBOS, Ratana Lee, Anaplan), Workday, Salesforce.
- **Databases / Oracle EBS:** live connection with synonym/view resolution.
- **Automatic column profiling:** data type, distinct count, % null, sample values per column; durable storage so sources survive restarts.

---

## 3. Targets (outbound)

- **Oracle Fusion FBDI** for ERP objects, produced as one CSV per interface sheet, zipped, in exact template column order with Oracle's exact headers and `*` required markers.
- **Oracle HCM HDL** (`.dat`) for HCM objects (e.g. Worker).
- **Multi-sheet fan-out:** Customer (19 sheets), Item / Product Hub (17 sheets), Supplier (6 objects — Supplier, Address, Site, Site Assignment, Contacts, Banks), and more.
- **One dataset → many objects:** a single source extract can generate the full related object set.

---

## 4. Mapping engine

- **Layered precedence:** Golden record → Learnings → Mapping workbook → User rule → Deterministic → Default → AI (last).
- **Deterministic matcher:** column-name token similarity, semantic synonym dictionary, description overlap, value/LOV affinity, sample-pattern affinity, type compatibility, and an exact-identity bonus — so most fields map with no AI.
- **AI residual mapping:** only the ambiguous fields go to the LLM; batched per sheet with samples and confidence. Model selectable (Haiku / Sonnet / Opus) for cost control.
- **Heavy-template gating:** on 300+ field objects the AI residual is skipped (deterministic + learnings only) to stay fast and within gateway limits.
- **Alternative candidates:** the UI offers ranked alternate source columns per field.

---

## 5. Transformation engine

Per-row rules applied at generation. Rule types include:

- **VALUE_MAP** — source value → Oracle code crosswalk (e.g. Approved → SPEND_AUTHORIZED).
- **CASE_WHEN / CONDITIONAL** — branch on a source column (e.g. Email Transactions = Yes → EMAIL).
- **PHONE_PART** — split a phone/fax string into country / area / number / extension.
- **MAP_BOOLEAN** — Yes/No → Y/N (or blank).
- **CONDITIONAL_DATE** — emit today's date, blank, a column's date, or a literal based on a flag (handles multiple config schemas).
- **CONCAT, COALESCE, PREFIX/SUFFIX, DATE_FORMAT, COMPUTED**, and more.
- Constant **fixed values** pinned into a field for every row.

---

## 6. Knowledge base & learnings

- **Learning types:** `column_mapping`, `suppress_field`, `example_default`, `ignore_source`, `reference_standard`.
- **Multi-tenancy:** learnings are client-scoped (default: NextPower) or `is_global` for source-system knowledge reusable across clients.
- **Auto-capture:** approvals, gold-verified values, fixed values, and rules become learnings automatically.
- **Auto-apply:** on the next matching conversion, learnings apply before AI — so the tool needs progressively less AI.
- **Seeded analyst mappings:** Item (all 5 source systems) and Supplier (NetSuite SS Vendors + Arena eBOS, all 6 objects) mapping documents are distilled into seeded learnings, including value-maps.
- **Rule Library & Crosswalk Library:** inspect and edit learnings and value crosswalks directly.
- **Do-not-map:** noisy source columns (e.g. 179 NetSuite `custitem_*`) seeded as `ignore_source` so AI stops over-mapping them.

---

## 7. Gold standards & steering

- **Gold reference standards:** upload a known-good output; the tool derives column mappings, constant defaults, and blank-field suppressions and applies them automatically at generate.
- **Project-independent Gold library:** upload gold files without a project; multi-file upload with orphan tracking.
- **Steer with instructions:** type plain-English guidance to fix a mapping.

---

## 8. Coded values (Oracle LOVs)

- Parses each field's allowed values / lookup type from the FBDI template.
- Measures how many source values resolve to the target list (exact / meaning / synonym / fuzzy).
- **Import lookup codes** and manage value crosswalks; flags coded columns that need a look before generate.

---

## 9. Data quality & validation

- **Null-sentinel cleanse:** literal `NULL` / `N/A` / `NONE` become empty cells.
- **Date normalisation** to Oracle `YYYYMMDD`.
- **Hyphen-safe identifiers:** preserves hyphens in item numbers / class codes / part numbers.
- **AI data-quality checks & cleansing suggestions**; **AI load-error explanations** with fix suggestions.
- **Run Validation / Simulate Load** before the real load.

---

## 10. Generation & output

- **Background generation** with live status polling — large multi-sheet loads never hit the gateway timeout.
- **Streaming, memory-bounded** write (one sheet at a time) for wide/large datasets.
- **Backbone-only population:** optional child sheets stay headers-only unless a real source column maps into them, matching how gold files leave optional tables blank.
- **CSV-zip fast path** plus XLSX option; **Download all** relevant files as a zip.
- **Self-healing templates:** conversions on a flat/wrong template are auto-repaired onto the real multi-sheet template at map time.

---

## 11. Review experience

- **Canvas** (drag-to-map) and **Table** views, with a live **precedence bar** showing how each field was decided.
- **Per-sheet disambiguation:** fields that repeat across interface sheets (linking keys) are badged with their sheet name.
- **Fixed value, override, custom rule, reset-gold-default** controls per field.
- **Recommendations panel** with Apply / Apply & Learn.
- **Highest-priority-per-target** dedup so a rejected mapping never exports as suggested.

---

## 12. Environments, monitoring & platform

- **Environment progression:** DEV → QA → UAT → PROD with promotion.
- **Migration Monitor, Load Runs, Error Traceback, Dependency Graph** for load orchestration and the object load sequence.
- **Datasets management** (delete, replace, cascade), **new-engagement** flow (DB vs File).
- **Stack:** FastAPI + MongoDB (Beanie) + GridFS backend; React + TypeScript + Vite frontend; hosted on Render; idempotent startup seeders.

---

## 13. On the roadmap

- **Extensible Flexfields (EFF)** for Item — the majority of NextPower item attributes load as EFF (`EGO_ITEM_INTF_EFF_B/_TL`); design is scoped, pending the client's flexfield configuration export.
- **Supplier value crosswalks** for Tax Org Type / Supplier Type once the real source-value lists are provided.
