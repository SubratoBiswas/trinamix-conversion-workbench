# Trinamix Conversion Workbench — User Guide

*How to convert legacy ERP/PLM master data into Oracle Fusion load files.*

---

## 1. What the tool does

The Conversion Workbench takes a source data extract (from NetSuite, Infor SyteLine, Arena/eBOS, Workday, Anaplan, Salesforce, or a database) and turns it into an **Oracle Fusion load file** — **FBDI** (a `.zip` of `.csv` files) for ERP objects, or **HDL** (`.dat`) for HCM. It maps every source column to the right Oracle interface field, applies the transformations and constant defaults each field needs, and remembers every correction you make so the next conversion is faster.

You do the work in five stages: **Ingest → Auto-Map → Review → Generate → Download & Load.**

---

## 2. Getting started

1. Open the app and sign in.
2. The top bar has a **model selector** (Haiku / Sonnet / Opus) that controls which AI model does the residual mapping — leave it on the default unless you're tuning cost/quality.
3. The left sidebar is your navigation: **Datasets**, **FBDI Library** (Templates, Gold Standards, Target Objects), **Conversion Workbench** (Projects, Conversion Objects, Dataflows, Mapping Review, Output Preview), **Load Management** (Migration Monitor, Load Runs), and **AI Engine** (Learning Center, Rule Library, Crosswalk Library).

**Key vocabulary**
- **Project / Engagement** — a client workstream that groups related conversions.
- **Dataset** — an uploaded source file (or DB connection) that's been parsed and profiled.
- **Conversion** — one dataset mapped to one target FBDI object.
- **Target FBDI object** — e.g. Supplier, Supplier Site, Item Import, Customer.
- **Learning** — a reusable rule the tool captured (a column mapping, a default, a suppression) that auto-applies next time.

---

## 3. Stage 1 — Ingest a source file

1. Click **Create** (top bar) or go to a project and add a conversion.
2. Choose the source type: **File upload** (xlsx/csv) or **Database/EBS connection**.
3. Upload the file. The tool parses it, profiles every column (data type, distinct count, % null, sample values) and stores it durably.
4. Pick the **target FBDI object** for this conversion (e.g. "Supplier Import", "Item Import"). One source file can fan out to several related objects.

**Tip:** always confirm the **Source Dataset** panel shows the file you intend — the row/column count (e.g. `2,662 × 58`) tells you it's the right extract. If it shows the wrong file, click **Replace**.

---

## 4. Stage 2 — Auto-Map

On the conversion, click **AI Auto Map** (or **Run AI Mapping**). The engine assigns a source column (or a constant/rule) to every target field using a strict precedence:

**Golden record → Learnings → Mapping workbook → User rule → Deterministic → Default → AI (last).**

Each field takes its value from the highest layer that has an answer; AI only fills what's left. On large multi-sheet objects (Item = 1,365 fields, Customer = ~1,250) the engine resolves deterministically and from learnings/gold, and skips the AI residual so it stays fast.

When it finishes you'll see counts: **Auto-Mapped**, **Approved**, **Required Gaps**, **Learned**, **From KB**.

---

## 5. Stage 3 — Review & Refine (Mapping Review)

This is where you check and correct the mapping. Two views:

- **Canvas** — source columns on the left, target fields on the right, drag to connect. A field that lives on multiple interface sheets (e.g. `Item Number`, the linking key on every item table) is badged with its **sheet name** so it reads as distinct, not duplicated.
- **Table** — a scannable list with confidence, status, and the decided value per field.

**The precedence bar** at the top ("How each field is decided") shows how many fields came from each layer.

For any field you can:
- **Approve / Reject** the suggestion.
- **Override source column** — pick a different source column.
- **Set a fixed value** — pin a constant into every row (e.g. a country code, `CORPORATION`, `SPEND_AUTHORIZED`). This overrides AI and clears the source column.
- **Add a custom rule** — a transformation (VALUE_MAP, CASE_WHEN, PHONE_PART, CONDITIONAL_DATE, CONCAT, COALESCE, DATE_FORMAT, etc.).
- **Reset gold defaults** — clear a constant a gold file baked in that's wrong here.

**Recommendations panel** (right) surfaces AI suggestions — value crosswalks for coded fields, defaults for required fields with no source, and dedup hints. Click **Apply** or **Apply & Learn** (the latter saves it as a reusable learning).

**Learn from example & steer** — upload a *gold* output file and/or type plain-English instructions; the tool derives mappings/defaults/suppressions from it and applies them.

**Coded values (Oracle LOVs)** — for fields with a fixed list of values, the tool shows how many source values resolve to the target list and lets you import lookup codes / review the crosswalk.

---

## 6. Stage 4 — Generate the output

Click **Generate Output** (or **Generate & download**). Generation runs as a background job (so it never times out) and:

1. Applies every mapping and transformation row by row.
2. Blanks legacy null-sentinels (`NULL`, `N/A`, `NONE`) so they load as empty cells.
3. Reformats dates to Oracle's `YYYYMMDD`.
4. Applies control-field constants and authoritative defaults.
5. Writes **one CSV per interface sheet**, zipped — exactly matching the FBDI template's columns, order, and headers (with the `*` required markers).

Multi-sheet objects only populate the sheets that carry real data (the backbone always emits; optional child sheets emit only when a real source column maps into them, otherwise headers-only).

> **Important:** the on-screen **Preview** is a quick view that skips the final control-default/constant pass, so it can differ from the file. **Always verify against the downloaded FBDI file, not the preview.** And **regenerate after every deploy** — a redeploy clears previously generated artifacts.

---

## 7. Stage 5 — Download & load into Oracle

1. Click **Download Output** — you get the `.zip` (FBDI) or `.dat` (HDL).
2. Load it into Oracle Fusion via the **Load Interface File for Import** scheduled process (or paste the per-sheet data into the shipped FBDI template and use its **Generate CSV File** button).
3. Use **Simulate Load** / **Run Validation** in the tool first to catch data-quality issues before the real load.

---

## 8. Environments & promotion

Each conversion moves through **DEV → QA → UAT → PROD**. Use **Promote to environment** to advance a validated conversion. **Migration Monitor** and **Load Runs** track load history.

---

## 9. Learnings — why the tool gets smarter

Every approved mapping, gold-verified value, fixed value, and custom rule is captured in the **Learning Center** as a reusable *learning* (tagged to the client, or marked global for cross-client source knowledge). On the next conversion from the same source system, those learnings auto-apply — so you review less and less over time. The **Rule Library** and **Crosswalk Library** let you inspect and edit them directly.

---

## 10. Troubleshooting / gotchas

- **Output looks flat (one CSV, not the multi-sheet set):** the conversion is on a flat template. Click **AI Auto Map** — the tool self-heals it onto the real multi-sheet template, then **Generate** again.
- **A field is blank / shows a stale value:** regenerate (redeploy wipes old output), then verify against the *downloaded* file, not the preview.
- **A field shows the raw source value (rule not applied):** the transformation may not have been re-mapped since the rule was added — Re-run AI Mapping, then regenerate.
- **The same field appears several times in the canvas:** those are the per-sheet linking keys (e.g. Item Number on each interface table) — they're now badged with their sheet name to distinguish them.
- **Coded field rejected by Oracle:** open the **Crosswalk Library** and confirm the source value → Oracle LOV code mapping.

---

## 11. Quick reference — the five-stage flow

| Stage | Action | Screen |
|---|---|---|
| 1. Ingest | Upload/connect source, pick target object | Create / Datasets |
| 2. Auto-Map | Run AI Auto Map | Conversion / Mapping Review |
| 3. Review | Approve, override, fixed values, rules, crosswalks | Mapping Review |
| 4. Generate | Generate Output (background) | Conversion / Output Preview |
| 5. Download & Load | Download zip → Load Interface for Import | Conversion / Load Management |
