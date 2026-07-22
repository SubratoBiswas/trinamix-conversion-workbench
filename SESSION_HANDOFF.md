# Trinamix Conversion Workbench — Session Handoff (NextPower)

Continuation brief. Everything needed to pick up in a new chat: what the tool is, how to deploy, the gotchas, what changed this session, current deploy state, open items, and the file map.

---

## 1. What the tool is

Converts legacy ERP/PLM master data (NetSuite, Infor SyteLine, Arena/eBOS — EBOS/Ratana Lee/Anaplan, Workday, Salesforce) into Oracle Fusion loads — **FBDI** (`.csv` in `.zip`) for ERP, **HDL** (`.dat`) for HCM. Every analyst correction is captured as a reusable **learning** so later conversions auto-apply it.

**Two flows** (both documented in `docs/`):
- **File flow** — upload a file → map → generate → download the FBDI zip → load into Oracle by hand.
- **Connected flow** — a source connection (Oracle EBS over JDBC, or an API system) → discover/extract → map → **Load to Fusion** pushes it straight into Oracle Fusion via the ERP Integration service (`load_to_fusion` uploads to UCM + submits the `importBulkData` ESS job, returns a request id), then Load Runs / Migration Monitor poll the ESS phase and capture per-row errors with AI-explained fixes. `Simulate Load` = dry run.

**Stack:** FastAPI + Beanie/MongoDB + GridFS backend; React + TypeScript + Vite frontend; hosted on **Render** (backend `trinamix-conversion-backend.onrender.com`, frontend `tx-conversion-workbench.onrender.com`). Idempotent seeders run on startup (`app/main.py` `_run_seeds_background`).

**Mapping precedence (highest→lowest):** golden record → learnings → mapping workbook → user rule → deterministic → default → AI.

---

## 2. Deploy + environment gotchas (read before editing)

- **Deploy = user double-clicks `launch_git.bat`** in the repo root (`git add -A` + commit + push → Render auto-redeploys/restarts). I update the commit message in that file before each deploy. **Do NOT run git from the sandbox** (a stale `.git/index.lock` from a sandbox `git add` blocks commits). Keep the commit `-m` reasonably short.
- **Flaky sandbox mount:** bash/Python frequently read a **stale/truncated** copy of a just-edited file → false `SyntaxError` (unclosed brace/string, short line count). The **Read/Grep/Edit tools are authoritative** (Windows side, what deploys). To tell a real error from mount noise: retry a few times / verify the file tail with Read / parse a fresh copy in `/tmp`. This happened many times this session — don't be fooled.
- **Preview ≠ downloaded file:** the on-screen preview skips `_finalize` (control defaults, masks, authoritative constants). **Always verify against the DOWNLOADED FBDI file.**
- **Redeploy wipes generated artifacts:** **regenerate after every deploy** before downloading.
- **Render ~100s gateway timeout** surfaces as a misleading CORS/ERR_FAILED. Heavy work must be background/threaded.
- **Startup-only seeders:** template repairs and mapping seeds run at startup. A conversion **created between restarts** may miss them — which is why the Item repair was also wired into map-time (see below).
- **Seeder is upsert-with-upgrade** and only refreshes `rule_config` when `rule_type` **changes** — to force a config update on reseed, change the rule_type (or delete the row).

---

## 3. What changed this session

### Item flow — made it produce the real 18-sheet FBDI
- `ensure_item_multisheet()` (`template_seed_service.py`) force-seeds the real 17-sheet Item template and re-points flat-template conversions onto it, clearing stale mappings. `_is_item` broadened to catch source-prefixed flats (`SyteLine ERP Item Import`, `NetSuite Item Master`) while excluding child objects (cost/categories/revisions/…).
- **Map-time self-heal:** `run_mapping_suggestions` (`mapping_service.py`) calls `ensure_item_multisheet()` at the start of AI Auto Map and refreshes the conversion — fixes conversions created between restarts.
- **Heavy-template AI gate:** `_heavy = len(targets) > 300`; AI residual is skipped for Item(1365)/Customer(~1250) so mapping doesn't blow the gateway (was saving 0 mappings — "not mapping at all").
- **Backbone-only child sheets** extended to Item (only `EGP_SYSTEM_ITEMS_INTERFACE` always emits; child sheets headers-only unless a real source column maps in).
- **Null-sentinel cleanse** (`_blank_null_sentinels` in `output_service.py`): literal `NULL`/`N/A`/`NONE` → empty cells.
- **Hyphen fix** (`ai/rule_based.py`): the over-broad REMOVE_HYPHEN no longer strips hyphens from item/part/class/category/name/description/status/type fields (gold keeps `50-1020`, `Prefab-Build`); unchanged for Supplier/Customer/PO/Serial numbers.
- **Seeded 53 Item standard-field mappings** (`item_field_mappings.json`) from the NXT Item mapping doc, all 5 sources.

### Supplier flow — seeded analyst mappings + value-maps
- **138 clean 1:1 mappings** (`supplier_field_mappings.json`, `seed_supplier_field_mappings()`), all 6 supplier objects, NetSuite SS Vendors + Arena eBOS.
- Tax Org Type / Supplier Type / Business Relationship removed from `_AUTHORITATIVE` (no longer forced constants); Business Relationship carries a VALUE_MAP (`Approved→SPEND_AUTHORIZED` else `PROSPECTIVE`); the `CORPORATION/SUPPLIER/SPEND_AUTHORIZED` constants stay in `_CONTROL_DEFAULTS` as fallback. Catalog seeder now honors per-row `rule_type`/`rule_config`.

### QA email issues (Tejaswini, "Open Issues") — all verified fixed in code
1. **Exchange Rate blank** — mapped (`Supplier Banks / Exchange Rate ← Current Currency Exchange Rate`); blank only where source is blank; regenerate fills real rates.
2. **Fax Area Code** — `PHONE_PART {part:area}`; engine-proven `+1 (208) 555-1234 → 208`.
3. **User Account Action "None"** — `VALUE_MAP No→"" / Yes→CREATE_USER_ACCOUNT` (engine-proven); removed from `_AUTHORITATIVE`/`_CONTROL_DEFAULTS` so nothing rewrites it to "NONE".
4. **Invoice Match Option = Receipt** — in `supplier_default_values.json`, seeded via `seed_supplier_default_values()` (startup-wired), applied as `example_default` even on `not_applicable`.
5. **Rejected exports as "suggested"** — the row-derivation used by the table AND the CSV export uses the highest-priority-per-target `mapByTarget` (rejected beats suggested).
6. **Item Number appears 4×** — NOT duplicate records; it's the linking key on all 16 item sheets. Fixed by returning `sheet_name` per field (`FBDIFieldOut` + `/templates/{id}/fields`) and badging a field with its sheet name in the canvas + table when the name repeats.

**Status of the QA list:** the code is complete for all six. They still show in the QA "Fresh Test" projects only because those were built before the fixes deployed. For supplier objects the learnings + defaults **force-apply at generate** (`if not _heavy: apply_learned(..., force=True)`), so a plain **Regenerate** clears 1–4; issue 5 needs a fresh **CSV export**; issue 6 is visual (deployed). Next chat: get a regenerated Supplier zip + exported mapping CSV and confirm in the downloaded file.

### Upload truncation ("1000 rows") — investigated, not a tool bug
No 1000-row cap anywhere: profiling samples 3,000 rows but `count_data_rows` records the true full count, the whole raw file is stored in GridFS, and generation reads the entire file (`max_rows=None`). The reported truncation was the file **share into the chat**, not the tool. Hardened one real weakness: the durable-copy step in `create_dataset_from_upload` no longer swallows failures silently (now logs), so a failed large-file store is diagnosable instead of looking like truncation.

### Performance / scale (confirmed constants)
- Transform runs in **25,000-row chunks** (`_TRANSFORM_CHUNK_ROWS`), each on a worker thread, concatenated (byte-identical to a single pass; peak memory ≈ one chunk).
- Heavy work (transform, coded-value enforcement, write) runs off the event loop via `asyncio.to_thread`.
- Generation is a **background job** (`operations._run_generation` via `asyncio.create_task`), UI polls status.
- **Streaming write** one sheet at a time (`del sdf`) into a deflated zip.
- Profiling `PROFILE_SAMPLE_ROWS = 3000`; xlsx read `read_only`; CSV via C parser; bulk-insert profiles.
- `_heavy = field_count > 300` gates the AI residual.

### Wave V (from a parallel session, present in this repo)
Transformation engine (`app/transformations/engine.py`, `apply_rule`, `RULE_TYPES`) with VALUE_MAP, CASE_WHEN, PHONE_PART, MAP_BOOLEAN, CONDITIONAL_DATE, CONCAT, COALESCE, DATE_FORMAT, etc. **Unknown rule types pass the value through silently** — if a field shows the raw source value, check the rule type is implemented and in `RULE_TYPES`. Supplier transform mappings in `supplier_transform_mappings.json`. Wave V.1 (CONDITIONAL_DATE both-schema + column-ref tokens) may be staged; verify vs origin.

### Deliverables produced this session (`docs/` + repo root)
- **PPTX:** `Trinamix_Conversion_Workbench_Diagrams.pptx` (architecture + process-flow slides).
- **Word docs** (`docs/`): `Conversion_Workbench_User_Guide.docx`, `Conversion_Workbench_Process_Flow.docx`, `Conversion_Workbench_Features_Overview.docx` (has a **Performance and scale** section), `Connected_Load_Flow_Process_Flow.docx`, `Connected_Load_Flow_User_Guide.docx`. Markdown originals alongside. Diagrams embedded; prose written in a plain human voice (not LLM-ish).
- **Scope/ask:** `EFF_Support_Scope.md`, `Client_Ask_Item_EFF_Export.md`.

---

## 4. Current deploy state (verify first thing)

- Confirmed the backend was **live** this session (Swagger + OpenAPI respond).
- Pending / last edits to push via `launch_git.bat`: the **upload durable-copy logging** hardening, and the **issue-6 sheet-name disambiguation** (backend `sheet_name` on `/templates/{id}/fields` + frontend badges) if not already pushed.
- Check: `git log --oneline -3` and compare local vs `origin/main`. The `launch_git.bat` commit message reflects the newest change.

---

## 5. Open items / next steps

1. **Clear the QA list operationally:** on each Supplier/Item Fresh Test project → Re-run AI + Regenerate + re-export the mapping CSV → verify against the DOWNLOADED file (not preview). Then reply to Tejaswini.
2. **EFF for Item** — the majority of NextPower item attributes are Extensible Flexfields (`EGO_ITEM_INTF_EFF_B/_TL`, positional `ATTRIBUTE_*` slots by `Attribute Group Code`). Blocked on the client's Manage-EFF export; design in `EFF_Support_Scope.md`, client ask in `Client_Ask_Item_EFF_Export.md`.
3. **Supplier value crosswalks** for Tax Org Type ← Entity Type and Supplier Type ← Category — need the real NetSuite source-value lists to build the crosswalks.
4. **Root cause worth fixing:** `fbdi_seed.auto_seed_if_empty` still creates flat single-sheet templates for new conversions. The Item map-time self-heal repairs it; consider fixing conversion-creation and adding the same self-heal for Supplier/Customer.
5. **New Item mapping file** (`000 NXT _ Item Field Mapping Documents 1.xlsx`) — same tabs as the one already seeded; refresh `item_field_mappings.json` from it if it's an updated version.
6. Optional: stitch the five docs into one client manual (cover + TOC); Wave V.1 deploy if still staged.

---

## 6. Key files

**Backend**
- `app/services/output_service.py` — generation: chunked transform, `_TRANSFORM_CHUNK_ROWS=25000`, `to_thread`, `_finalize`, `_CONTROL_DEFAULTS`, `_AUTHORITATIVE`, `_blank_null_sentinels`, backbone suppression (`_is_item`/`_CUST_BACKBONE`), streaming `_write_all`, `_heavy` gate, force-apply learnings at generate.
- `app/services/mapping_service.py` — `run_mapping_suggestions` (map-time item self-heal + heavy AI gate), EBS live query.
- `app/services/template_seed_service.py` — `ensure_customer_multisheet`, `ensure_item_multisheet`, `_is_item`, `_BUNDLED`.
- `app/services/catalog_seed_service.py` — `_seed_catalog_file` (upsert; honors rule_type/rule_config), `seed_item_field_mappings`, `seed_supplier_field_mappings`, `seed_supplier_default_values`, `seed_mapping_catalog`.
- `app/services/dataset_service.py` — upload/profile; `count_data_rows`, `PROFILE_SAMPLE_ROWS=3000`, GridFS durable copy (now logs failures).
- `app/services/learning_service.py` — `apply_learned_to_conversion` (force at generate; example_default on not_applicable).
- `app/transformations/engine.py` — `apply_rule(rule_type, config, value, row, ctx)`, `RULE_TYPES`.
- `app/services/fusion_service.py` + `app/routers/fusion.py` — connected flow: `load_to_fusion` (UCM + importBulkData), `/conversions/{id}/load-to-fusion`, ESS phase poll, LoadRun/LoadError.
- `app/routers/discovery.py` + `app/models/v10.py` (SourceConnection) — source DB connections, test, discovery runs (EBS via JDBC / `ojdbc11.jar`).
- `app/routers/fbdi.py` — `/templates/{id}/fields` now returns `sheet_name`; `fbdi_seed.py` flat fallback schemas (`itemmasterimport`) + `auto_seed_if_empty`.
- `app/data/*.json` — `item_field_mappings.json`, `supplier_field_mappings.json`, `supplier_transform_mappings.json`, `supplier_default_values.json`, `item_donotmap_columns.json`, `mapping_catalog.json`.
- `app/main.py` — startup seeders (fire-and-forget, idempotent).

**Frontend**
- `src/pages/MappingReviewPage.tsx` — canvas/table, `mapByTarget` (highest-priority dedup; used by table + CSV export), per-sheet `sheet_name` badge, precedence bar, fixed-value/override/rules.
- `src/types/index.ts` — `FBDIField.sheet_name?`.

**Repo root:** `launch_git.bat` (deploy), the `.docx`/`.md` deliverables, `EFF_Support_Scope.md`, `Client_Ask_Item_EFF_Export.md`, `Trinamix_Conversion_Workbench_Diagrams.pptx`.

---

## 7. Client source / gold / mapping files (in uploads)
NetSuite vendor: `All Vendors - SS All Vendors - Phoenix.xlsx`. SyteLine item: `SyteLine ERP.xlsx`. Arena item: `EBOS - arena_ebos_item_attributes_7-6-2026 SB.xlsx`. Item mapping docs: `000 NXT _ Item Field Mapping Document_SB.xlsx`, `000 NXT _ Item Field Mapping Documents 1.xlsx`. Supplier mapping: `Suppliers Field Mapping - Updated v3 1 (2) SB.xlsx`. Gold: `ItemImportTemplate (1) (1) 1.xlsm`, `NCR CustomerImportTemplate UAE 1 1.xlsm`, `Customers Template BATCH 21 revised.xlsm`. QA: `Fw_ Conversion Workbench_ Open Issues.msg`.

---

## Fix: rejected mappings leaked into the FBDI (2026-07-22)

**Symptom.** Reject a suggested mapping. The UI shows "rejected" and the downloaded
mapping CSV shows "rejected" — but the generated FBDI still contains values from the
rejected source column.

**Root cause.** `output_service._transform_frame`. The per-target dedup ranks
`rejected`(1) above `suggested`(0), so the rejected row correctly *wins* and becomes the
field's mapping. But the only skip in the loop was `if m.status == "not_applicable"`,
and `rejected` was handled only on the rules line (`m.status != "rejected"`), which
suppressed the *transformation* and nothing else. `has_src` stayed true, so the rejected
column's raw values were written.

**Fix.**

```python
_discarded = m.status in ("not_applicable", "rejected")
if _discarded and not (m.default_value and str(m.default_value).strip()):
    continue
...
has_src = bool(m.source_column) and m.source_column in col_cache and not _discarded
```

A discarded mapping never reads `source_column`. It is skipped outright unless an
explicit `default_value` was attached, in which case that default is emitted as a
CONSTANT (this preserves cases like Invoice Match Option = "Receipt", where the analyst
threw away the source column but still wants a fixed value).

**Verified** by running the real `_transform_frame` standalone (beanie is unimportable in
the sandbox): approved keeps its source, rejected emits no column, rejected+default emits
the constant, not_applicable still skipped, rejected column's values absent everywhere.

**Applies to existing rejections, not just new ones.** This is a generate-time fix that
reads the stored `status`, so previously-rejected mappings are honoured on the next
Generate with no re-mapping. Two things could have resurrected them and neither does:
`apply_learned_to_conversion._eligible()` accepts only `suggested` (or `approved` under
force), never `rejected`; and the `_PRIO` dedup keeps `rejected` above a stale
`suggested` row for the same target.

---

## eBOS/SyteLine supplier mappings + learning-engine field-match fix (2026-07-22)

Source: `eBOS Vendors - 04-27-2026 (1).xlsx`. Six tabs; the useful ones are
`Oracle-NetSuite_SyteLine` (3 header rows — Oracle field / NetSuite column / **eBOS
column** — then 158 rows of expected Oracle output), `Data eBOS New` (the real 26-column
vendor extract) and `Sheet1` (Oracle ← NetSuite ← eBOS reconciliation).

### The latent bug this uncovered

`apply_learned_to_conversion` matched the learned `target_field` to the template's
`field_name` by **exact string**. Oracle decorates headers (`Supplier Name*`,
`Address Name *`, `*Supplier Number`, `**Bank Name`) while the analyst docs write the
plain name. So a whole class of seeded mappings sat in the library and never applied —
in the column pass, the suppression pass and the constant-default pass alike. All three
now do exact-match first, then a `_normalize()` fallback; a normalized key that would
collide across two different template fields is dropped rather than guessed.

### Verified rules (replayed through the real engine over the 158 gold rows)

| Oracle field | eBOS column | rule | match |
|---|---|---|---|
| Supplier Name* / Supplier Number / Alternate Name | name, vend_num, name | direct | 100% |
| Payment Method / Payment Terms | pay_type, terms_code | direct | 100% |
| **Bank Name / *Account Number / Account Name | bank_name, wire_id_acct_num, bank_acct_name | direct | 100% |
| Taxpayer ID, Tax Registration Number | tax_id | REPLACE " "→"" | 98.6 / 97.1% |
| Postal code / State / City / Address Name | zip, state, city, city | direct | 97–99% |
| Supplier Site* | city | PREFIX "BU " | 97.4% |
| Phone Extension | phone | PHONE_PART extension | 7/7 |
| Phone | phone | REGEX_REPLACE (strip trailing ext + leading "1-") | 55.7% exact, +50 rows differ only by separator |

30 of 39 verifiable rules match gold at ≥95%.

### Constants (NextPower-scoped `example_default`)

Supplier Type=`Standard`, Business Relationship=`SPEND_AUTHORIZED`, Invoice/Payment
Currency=`USD`, Client BU / Bill-to BU=`NX US BU`. Each is the single distinct value
across all 158 gold rows. `Supplier Type=Standard` supersedes the generic `SUPPLIER`
control default because `_CONTROL_DEFAULTS` only fills columns left *entirely* blank.

### Rows the gold disproved (removed)

* `**Branch Name ← Bank ID` — Bank ID is the routing number; gold leaves Branch Name empty.
* `IBAN ← WIRE ID` — WIRE ID is the account number; gold leaves IBAN empty.
* `Remit Advice Email ← internal_email_addr` — empty in all 158 gold rows.
* Oracle `E-Mail` switched from `internal_email_addr` (internal AP mailbox, 10.5%) to
  `contact` (42.8%).

### Known gaps, deliberately NOT seeded

* **Address compaction.** Gold splits `4141 Inland Empire Blvd, Suite 305` into Line 1 +
  Line 2 and drops `c/o` / `Dept.` prefix lines. `addr##1 → Address Line 1` direct is
  84.8%; any rule I could derive scored worse. Needs an analyst cleansing rule.
* **Contact first/last name.** Gold names are hand-curated (`dale@…` → "Kurt"), not
  derivable from the extract.
* **Corporate Web Site.** `internet_url` is empty in this extract; the gold URLs were
  supplied manually. Mapping kept — correct whenever the column is populated.
* **Tax Org Type / Ship-to & Bill-to Location.** Owned by Finance in the sheet, no
  reliable source column.

### Reaching existing conversions

Seeders run at startup and upsert-with-upgrade, and generation re-runs
`apply_learned_to_conversion(force=True)` for every supplier template (all under the
300-field heavy gate). Existing NextPower conversions therefore pick this up on the next
**Regenerate** — no re-mapping, no re-upload. `overridden` / `rejected` choices are still
respected.
