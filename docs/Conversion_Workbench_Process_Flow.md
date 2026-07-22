# Trinamix Conversion Workbench — Process Flow

*The end-to-end conversion pipeline, stage by stage.*
*(A visual version of this flow is in `Trinamix_Conversion_Workbench_Diagrams.pptx`.)*

---

## The pipeline at a glance

```
Ingest & Profile  →  Auto-Map  →  Review & Refine  →  Generate  →  Output & Load
       │                                                                   │
       └───────────────── Learning loop (captured mappings) ◄──────────────┘
```

One source extract enters on the left and a load-ready Oracle Fusion file leaves on the right. Every correction made along the way is captured as a **learning** and fed back into Auto-Map, so each subsequent run needs less manual work.

---

## Stage 1 — Ingest & Profile

**Input:** a source extract (xlsx/csv) or a database/EBS connection.

1. **Upload / connect** — the file is stored durably (GridFS); DB sources are queried live.
2. **Parse** — a robust parser handles messy real-world files (magic-byte detection, HTML-as-xls, UTF-16, corrupt stylesheets).
3. **Profile** — every column gets a data-type, distinct-count, %-null, and sample values.
4. **Select target** — choose the Oracle FBDI object; one dataset can fan out to a set of related objects (e.g. Supplier → Supplier + Address + Site + Site Assignment + Contacts + Banks).

**Output:** a profiled Dataset bound to a target template.

---

## Stage 2 — Auto-Map

**Input:** the dataset's source columns + the target object's interface fields.

The engine decides every target field using a strict **precedence** — each field takes its value from the highest layer that has an answer:

1. **Golden record** — values learned from an uploaded gold output.
2. **Learnings** — reusable column mappings/defaults/suppressions captured from prior work.
3. **Mapping workbook** — analyst-authored source→target mappings seeded into the knowledge base.
4. **User rule** — a transformation you authored.
5. **Deterministic** — the rule-based matcher (name similarity + semantic synonyms + value/LOV affinity).
6. **Default** — a standard constant for the field.
7. **AI** — the LLM, **last**, and only for the fields still unresolved.

Heavy fan-out objects (Item ≈ 1,365 fields, Customer ≈ 1,250) resolve deterministically + from learnings/gold and **skip the AI residual**, so mapping stays fast and never times out. Self-heal: if the conversion is sitting on a flat single-sheet template, Auto-Map re-points it onto the real multi-sheet template first.

**Output:** a full set of mapping suggestions (mapped, defaulted, or flagged as gaps).

---

## Stage 3 — Review & Refine

**Input:** the mapping suggestions.

The reviewer works in the **Canvas** or **Table** view and can:
- Approve / reject each suggestion.
- Override the source column, or **pin a fixed value**.
- Add a **transformation rule** (VALUE_MAP, CASE_WHEN, PHONE_PART, CONDITIONAL_DATE, CONCAT, COALESCE, DATE_FORMAT…).
- Apply **AI recommendations** — value crosswalks for coded fields, defaults for required gaps, dedup hints — with **Apply & Learn** to save them.
- **Learn from example** — upload a gold file or type instructions to steer the whole mapping.

Every accepted correction is written to the **Learning Center** and immediately reusable.

**Output:** an approved, transformation-complete mapping.

---

## Stage 4 — Generate

**Input:** the approved mapping + the source data.

Row by row, generation:
1. Applies each field's mapping/transformation.
2. Blanks legacy null-sentinels (`NULL`/`N/A`/`NONE`) → empty cells.
3. Reformats dates to `YYYYMMDD`.
4. Applies control-field constants and authoritative defaults.
5. Fans out to **one CSV per interface sheet**, in exact template column order with Oracle's exact headers — zipped. Optional child sheets emit data only when a real source column maps into them; otherwise headers-only.

It runs as a **background job** (poll for status), so large loads never hit the gateway timeout.

**Output:** the FBDI `.zip` (or HDL `.dat`).

---

## Stage 5 — Output & Load

**Input:** the generated file.

1. **Validate / Simulate** in the tool to catch data-quality issues.
2. **Download** the zip.
3. **Load into Oracle Fusion** via *Load Interface File for Import*.
4. Promote the conversion through **DEV → QA → UAT → PROD**; track it in Migration Monitor / Load Runs.

**Output:** data loaded into Oracle Fusion.

---

## The learning loop

Approved mappings, gold-verified values, fixed values, and custom rules are captured as **learnings** (client-scoped, or global for source-system knowledge). They re-enter the pipeline at the **Learnings** precedence layer on the next run — so the tool needs progressively less AI and less manual review as an engagement matures.

---

## Verification discipline (two rules that prevent most confusion)

1. **Verify against the downloaded file, not the preview** — the preview skips the final control-default/constant pass.
2. **Regenerate after every deploy** — a redeploy clears previously generated artifacts.
