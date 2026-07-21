# Trinamix Conversion Workbench — Session Handoff (NextPower)

Context document for continuing work in a new chat. Covers what the tool is, the architecture, everything built/fixed, current deploy state, verified results, pending items, and the gotchas needed to continue safely.

---

## 1. What this is

The **Trinamix Conversion Workbench** converts legacy ERP data (NetSuite, Arena/eBOS, Infor SyteLine, Workday, Anaplan) into **Oracle Fusion** load files — **FBDI** (`.csv` in `.zip`) for ERP objects and **HDL** (`.dat`) for HCM. Goal of this work: harden it for the **NextPower** client demo, and capture every analyst mapping rule/correction as **durable, reusable learnings** so future NextPower conversions auto-apply them.

**Stack**
- Backend: FastAPI + Beanie/MongoDB. Frontend: React + TypeScript + Vite.
- Hosting: Render. Backend `https://trinamix-conversion-backend.onrender.com`, frontend `https://tx-conversion-workbench.onrender.com` (also `https://trinamix-conversion-workbench.onrender.com`).
- Deploy: user double-clicks **`launch_git.bat`** in the repo root (git add -A + commit + push). Render auto-builds/deploys, and idempotent seeders run on startup.

---

## 2. Architecture essentials

**Mapping precedence** (highest → lowest): golden record → learnings → mapping workbook → user rule → deterministic → default → AI.

**Learnings** live in the `LearnedMapping` collection. Kinds: `column_mapping`, `suppress_field`, `example_default`, `ignore_source`, `reference_standard`. Multi-tenancy via `client_id` / `is_global`; untagged rows resolve for the default client **NextPower**. `is_global=true` = source-system knowledge reusable across all clients.

**Transformation engine** (`backend/app/transformations/engine.py`, `apply_rule`). Rule types include: `VALUE_MAP`, `CASE_WHEN` (branches: if_column/op/value/then + default), `CONDITIONAL`, `PHONE_PART` (country|area|number|extension), `MAP_BOOLEAN`, `CONDITIONAL_DATE`, `CONCAT`, `COALESCE`, `PREFIX`/`SUFFIX`, `DATE_FORMAT`, `COMPUTED`, etc. **Unknown rule types silently pass the value through unchanged** — that was the root cause of two bugs this session (see §4).

**Generation pipeline** (`backend/app/services/output_service.py`):
`build_converted_dataframe` → `_transform_frame` (per-row rules) → `_finalize` (reindex → `_blank_null_sentinels` → `_format_date_columns` (dates output as `%Y%m%d`) → `_apply_control_defaults` → `_mask_supplier_emails` → header rename) → `_write_all` (CSV/ZIP).
- `_CONTROL_DEFAULTS` = constant values applied by field name. `_AUTHORITATIVE` = fields overwritten unconditionally at finalize.
- **CRITICAL GOTCHA:** `get_output_preview` uses `build_converted_dataframe` **only** (skips `_finalize`). So the **preview ≠ the downloaded file** for control-default / mask / authoritative fields. **Always verify against the downloaded FBDI file, never the preview.**

**Seeder behaviour** (`catalog_seed_service._seed_catalog_file`): UPSERT-with-upgrade. It matches an existing learning by (kind=column_mapping, target_object, target_field, original_value=source_field) and, if the JSON's `rule_type` differs, updates **both** rule_type and rule_config, then de-dupes extras. Note: if rule_type is unchanged but rule_config changed, it will **not** update config — change the rule_type (or delete the row) to force an upgrade.

**Redeploy wipes the ephemeral disk**, so previously generated output artifacts are lost — you must **regenerate** after every deploy before downloading.

---

## 3. Current deploy state

- **Deployed and live: Wave V** (implemented CONDITIONAL_DATE + MAP_BOOLEAN, switched Enable B2B to CASE_WHEN).
- **Staged, NOT yet deployed: Wave V.1** (in `launch_git.bat`) — makes `CONDITIONAL_DATE` robust to the alternate `{then_value/else_value}` config schema and column-reference date tokens, to fix the **Supplier Site Assignment** Inactive Date variant (`then_value: "Last Modified"`). Optional/secondary — no reported issue depends on it. Deploy by running `launch_git.bat` whenever convenient.

---

## 4. Everything fixed this session (all captured as NextPower learnings)

**NetSuite Supplier**
- **Delivery Channel** (Supplier Import / Address / Site): now derived via CASE_WHEN (Email Transactions=Yes→EMAIL, Fax=Yes→FAX, else blank). Root cause of the earlier constant-EMAIL bug: `"delivery channel"` was in the generator's `_AUTHORITATIVE` set (and `_CONTROL_DEFAULTS`), so `_apply_control_defaults` overwrote the per-row result with a constant EMAIL at finalize (looked right in preview, clobbered in the file). Removed from both.
- **Communication Method** (Site): same Email/Fax derivation as Delivery Channel.
- **Invoice Match Option** (Site): analyst default **"Receipt"** seeded as an `example_default` learning (`supplier_default_values.json` + `seed_supplier_default_values()`). Also fixed generation so an explicit default populates even when the mapping is `not_applicable` (previously dropped as "leave blank"), and `example_default` learnings now apply to `not_applicable` fields, overriding a gold suppression.
- **Supplier Banks Exchange Rate**: now populated (earlier blank was a stale pre-deploy file).
- **Supplier Contacts Fax Area Code** (+ Country Code + Number): parsed from the Fax column via PHONE_PART (was unmapped).
- **Supplier Contacts User Account Action**: mapped from Login Access via VALUE_MAP (No→blank, Yes→CREATE_USER_ACCOUNT) so "No" no longer renders as "None".
- **Supplier Address Inactive Date**: corrected — was wrongly matched to Date Created; now derives from the **Inactive** flag via CONDITIONAL_DATE (Inactive=Yes → today's date, else blank).
- **Supplier Site Enable B2B Messaging**: wired from **Enable For EDI** (Yes→Y, No→N, blank→blank) via CASE_WHEN.
- **Supplier Site Payment Hold Date** ← "DVC Last Screening Date & Time" (direct); other undocumented-but-correct profile/address/site mappings kept as learnings per client decision.

**Engine additions (Wave V)** — root cause: `CONDITIONAL_DATE` and `MAP_BOOLEAN` were seeded as rules but never implemented, so they passed values through (Inactive Date came out as the raw "No"/"Yes" flag; Enable B2B came out blank). Implemented both; registered in `RULE_TYPES`. Switched Enable B2B seed from MAP_BOOLEAN to the proven CASE_WHEN-reads-source-column pattern.

**SyteLine Item**
- **User Item Type** → `matl_type` (analyst wrote "malt_type"; the real column is `matl_type`). Added to `item_field_mappings.json`.
- **Item Cross Reference**: flagged — the bundled Item Import template has a field "Cross Reference", not a distinct "Item Cross Reference" object; that's a separate Oracle interface. **Needs an analyst/template decision.**

**Mapping CSV export bug**
- A REJECTED mapping was exporting as "suggested". `MappingReviewPage.tsx` `mapByTarget` was last-write-wins across duplicate rows per target; now keeps the highest-priority row per target (overridden > approved > not_applicable > rejected > suggested).

**Item do-not-map**
- 179 NetSuite `custitem_*` source columns seeded as `ignore_source` learnings so AI stops over-mapping them.

---

## 5. Verified in the actual downloaded FBDI files (NetSuite Supplier, ~7,495 rows)

| Field | Output distribution | Status |
|---|---|---|
| Delivery Channel (Import / Address / Site) | blank ×7493, EMAIL ×2 | ✅ |
| Communication Method (Site) | blank ×7493, EMAIL ×2 | ✅ |
| Invoice Match Option (Site) | Receipt ×7495 | ✅ |
| Fax Area Code (Contacts) | real parsed codes (1, 2, 34, 61, 86, 208…) | ✅ |
| User Account Action (Contacts) | NONE ×7495 | ✅ |
| Banks Exchange Rate | real rates (1, 0.194077, 1.14581…) | ✅ |
| Address / Site Inactive Date | 20260721 ×372 (inactive), blank ×7123 | ✅ |
| Site Enable B2B Messaging | Y ×35, N ×7364, blank ×96 (matches source) | ✅ |

Source distributions that confirm the above: `Enable For EDI` = No ×7364, Yes ×35, blank ×96; `Inactive` = No ×7123, Yes ×372.

---

## 6. Pending / awaiting decision

1. **Deploy Wave V.1** (optional): fixes Supplier Site Assignment Inactive Date (`then_value: "Last Modified"` column-ref schema). Currently that field emits today's date instead of the Last Modified date. Run `launch_git.bat`.
2. **Item Cross Reference (SyteLine)**: analyst to confirm the target — the bundled template has "Cross Reference" (Item Import), no distinct "Item Cross Reference" object. Decide whether it's a separate interface/template.
3. **User Account Action "Yes" code**: confirm the Oracle LOV value (seeded as `CREATE_USER_ACCOUNT`) is correct for NextPower.
4. Task #34 (older, still pending): "Add force-reseed endpoint + verify + retest" — a nice-to-have admin endpoint; seeders currently run on startup and are idempotent.

---

## 7. Key files touched

- `backend/app/transformations/engine.py` — implemented `MAP_BOOLEAN` + `CONDITIONAL_DATE` (V.1 makes CONDITIONAL_DATE handle both `value/else` and `then_value/else_value` schemas + column-ref tokens).
- `backend/app/models/transformation.py` — `RULE_TYPES` includes `MAP_BOOLEAN`, `CONDITIONAL_DATE`.
- `backend/app/data/supplier_transform_mappings.json` — Delivery Channel×3, Communication Method, Fax split (PHONE_PART), User Account Action (VALUE_MAP), Inactive Date (CONDITIONAL_DATE), Enable B2B (CASE_WHEN), Payment Hold Date.
- `backend/app/data/supplier_default_values.json` + `seed_supplier_default_values()` — Invoice Match Option = Receipt.
- `backend/app/data/item_field_mappings.json` — SyteLine User Item Type ← matl_type.
- `backend/app/data/item_donotmap_columns.json` + `seed_item_donotmap_columns()` — 179 NetSuite custitem_* columns.
- `backend/app/services/output_service.py` — preview email mask; suppressed_keys excludes default-carrying fields; `_transform_frame` emits default on not_applicable; removed "delivery channel" from `_CONTROL_DEFAULTS` + `_AUTHORITATIVE`.
- `backend/app/services/learning_service.py` — `example_default` pass now applies to not_applicable fields.
- `backend/app/services/catalog_seed_service.py` — seeders; `_seed_catalog_file` upsert-with-upgrade.
- `backend/app/main.py` — seeders wired into startup (fire-and-forget, idempotent).
- `frontend/src/pages/MappingReviewPage.tsx` — mapByTarget highest-priority-per-target (fixes rejected→suggested export).
- `launch_git.bat` — deploy script; commit message currently = Wave V.1.

---

## 8. Gotchas for whoever continues (important)

- **Verify against the downloaded FBDI file, not the preview** (preview skips `_finalize`).
- **Regenerate after every deploy** — redeploy wipes generated artifacts ("No output artifact found" = stale/absent, just regenerate).
- **Parse FBDI CSV with a quote-aware parser** — naive comma-split misaligns wide rows (garbage currencies/IDs). ZIP inflation in-browser via `DecompressionStream('deflate-raw')`.
- **Do NOT run git from the sandbox** — a stale `.git/index.lock` from a sandbox `git add` blocked commits before. Deploy only via the user running `launch_git.bat` on Windows.
- **Keep the commit message in `launch_git.bat` short and cmd-safe** — an overly long/special-char `-m` once failed with "the system cannot find the file".
- **Unknown transform rule types pass through silently** — if a field shows the raw source value, check the rule type is actually implemented in `engine.py` and listed in `RULE_TYPES`.
- **Browser JS calls: avoid long inline `sleep`** — CDP times out at ~45s. Use shorter waits or poll.
- To upgrade an existing learning's config on reseed, **change its rule_type** (the seeder only refreshes config when rule_type differs).

---

## 9. Handy live-verification snippet (browser console on the app tab)

`window.__fv.created` holds the 6 supplier conversion IDs. Auth token: `localStorage.getItem('trinamix.token')`. Backend base: `https://trinamix-conversion-backend.onrender.com/api`. Learnings list endpoint: `GET /api/learned-mappings?limit=3000`. Regenerate: `POST /api/conversions/{id}/generate-output`. Download: `GET /api/conversions/{id}/download-output` (ZIP; inflate deflate-raw; quote-aware CSV parse; then tally the target column).
