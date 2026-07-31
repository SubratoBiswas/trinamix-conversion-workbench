# Trinamix Conversion Workbench — Codebase Guide

**Who this is for:** you, changing this code by hand.

It is not a reference of every module. It is the shape of the system, the one flow
that matters, and then **change recipes** — "to do X, edit these files, in this
order, and here is the test that will catch you if you get it wrong."

The last section, [Traps](#7-traps-this-codebase-keeps-falling-into), is the most
valuable part. Every entry there is a bug that actually shipped. Read it before your
first change, not after.

---

## Contents

1. [What the system is](#1-what-the-system-is)
2. [The one flow that matters](#2-the-one-flow-that-matters)
3. [The precedence model](#3-the-precedence-model-the-heart-of-the-domain)
4. [Where things live](#4-where-things-live)
5. [Change recipes](#5-change-recipes)
6. [Testing](#6-testing)
7. [Traps this codebase keeps falling into](#7-traps-this-codebase-keeps-falling-into)
8. [Running and deploying](#8-running-and-deploying)

---

## 1. What the system is

An analyst uploads a legacy extract (NetSuite, eBOS, SyteLine, Workday…). The tool
maps its columns onto an Oracle Fusion Cloud interface — **FBDI** for most objects,
**HDL** for HCM — and generates the load files.

Two halves:

| | Stack | Entry point |
|---|---|---|
| **Backend** | Python 3.11, FastAPI, Beanie (async ODM) over MongoDB Atlas, pandas | `backend/app/main.py` |
| **Frontend** | React 18, TypeScript, Vite, Tailwind | `frontend/src/App.tsx` |

Roughly 154 Python files and 75 TS/TSX files. Most of the domain weight sits in four
backend services; almost everything else is CRUD around them.

**The four that matter:**

| Service | What it owns |
|---|---|
| `services/mapping_service.py` | Auto-mapping: source columns → target FBDI fields |
| `services/learning_service.py` | The learning library — capture, propagate, apply |
| `services/output_service.py` | Generation: mapped frame → FBDI CSV/xlsx/zip |
| `transformations/engine.py` | The rule engine — one pure function per rule type |

If you are lost, you are probably looking for one of those four.

### Two vocabularies, and they are not the same

This trips everyone up, so learn it now.

- A **MappingSuggestion** is *per conversion*. It says "on THIS conversion, target
  field X reads source column Y." It is what Mapping Review shows you.
- A **LearnedMapping** is *reusable*. It says "for this client, this object and this
  source system, field X reads column Y" — and it applies to conversions that already
  exist and to conversions not yet created.

Almost every "I changed it and nothing happened" bug is these two disagreeing.

---

## 2. The one flow that matters

```
  Upload                  Map                      Transform                Generate
 ┌────────┐   ┌────────────────────────┐   ┌──────────────────┐   ┌─────────────────┐
 │Dataset │──▶│  MappingSuggestion     │──▶│  DataFrame       │──▶│ CSV / xlsx /zip │
 │+column │   │  (one per target field)│   │  one col per     │   │ ConvertedOutput │
 │profiles│   │                        │   │  target field    │   │                 │
 └────────┘   └────────────────────────┘   └──────────────────┘   └─────────────────┘
   datasets.py    mapping_service.py         output_service.py       output_service.py
                        ▲                    _transform_frame()      generate_output_
                        │                                            artifact()
                  learning_service.py
                  apply_learned_to_conversion()
```

Follow it once in the code — it is the fastest way to understand the whole app.

### 1. Upload — `routers/datasets.py`, `parsers/tabular_parser.py`

A file becomes a `Dataset` plus one `DatasetColumnProfile` per column (type, null %,
distinct count, samples). **A multi-sheet workbook becomes one Dataset per sheet**,
all bound to one conversion through `Conversion.source_dataset_ids`.

### 2. Map — `services/mapping_service.py::run_mapping_suggestions`

Creates one `MappingSuggestion` per target field. Sources, in order of authority:

1. **The learning library** — `learning_service.apply_learned_to_conversion`
2. **Deterministic scoring** — `ai/rule_based.py::rank_candidates` (name similarity,
   type compatibility, value overlap)
3. **AI** — only when the analyst presses *AI fill blanks*, and only onto fields that
   are still empty. It never writes silently.

`status` on a mapping is the whole state machine:

| status | means |
|---|---|
| `suggested` | the tool guessed; nobody has confirmed |
| `approved` | confirmed — **check `approved_by`**: `learning-engine` means the engine did it, an email means a person did |
| `overridden` | a person changed the tool's pick |
| `not_applicable` | "keep blank" — ship this column empty |
| `rejected` | the suggested column was wrong |

> `approved_by == "learning-engine"` vs an email address is load-bearing in at least
> five places. An engine approval must never outrank a person.

### 3. Transform — `services/output_service.py::_transform_frame`

Pure, row-local, runs in a worker thread on 20,000-row chunks. For each mapping, in
target-field sequence order, it produces one output column:

```python
for m in sorted_mappings:              # ordered by target field sequence
    rules = pipelines.get(tgt.id)      # analyst TransformationRules
    if rules:      col_values = [apply_pipeline(rules, src_val, row=..., ctx=...)]
    elif has_src:  col_values = source column, blanks filled with default_value
    else:          col_values = [default_value] * n
    # then the strategy overlay can override — see §3
    out_cols[tgt.field_name] = col_values
```

Two things to know:

- **`out_cols` is keyed by field NAME**, so all interface sheets share one column per
  name. Per-sheet differences are re-applied later in `_finalize` via
  `_apply_sheet_decisions`.
- A rule can read **other target fields computed so far** (via `_RowWithTargets`),
  because fields are built in sequence order. That is how Party Number reads Party
  Type.

### 4. Generate — `services/output_service.py::generate_output_artifact`

Per sheet: `_finalize()` → control defaults → per-sheet analyst decisions → strategy
frame rules → column layout → write. Formats: `csv` (zip of headerless CSVs with an
`END` terminator), `xlsx`, `template` (fills the real Oracle .xlsm), `dat` (HDL).

---

## 3. The precedence model (the heart of the domain)

Stated by the analyst, 31-Jul:

> The mapping file provides the initial mapping, the gold standard the initial
> reference for output. After conversion the user can change any mapping, remove
> etc, and the tool should apply it and update the learning center. **The last
> mapping with respect to date is final**, and existing and new conversions map and
> generate output according to that.

Implemented as:

```
  1. Analyst decision, or the mapping file  ─┐
  2. Learnings and golden records            ├─ ALL COMPARED BY DATE. Latest wins.
  3. AI                                      ─┘
```

**Every decision carries a date.**

| Carrier | Field | Set when |
|---|---|---|
| `MappingSuggestion` | `approved_at` | any deliberate edit (`routers/mapping.py::update_mapping`) |
| `LearnedMapping` | `effective_date` | when the *instruction* was given — falls back to `captured_at` |

> `effective_date` is not `captured_at`. Every startup seed re-stamps `captured_at`,
> so ordering on it inverts the whole precedence after a redeploy. Use
> `learning_service._effective_of(lm)` — never read the fields directly.

An **undated** decision counts as older. It cannot be shown to have come later, and
reading it as newer is what made corrections vanish.

### The strategy overlay

`services/strategy_overlay.py` is a **write-time** enforcement layer that runs inside
`_transform_frame`, after mapping, where nothing downstream can undo it. It exists
because seeded learnings demonstrably did not reach the output.

It reads `data/supplier_strategy_defaults.json` and friends. A directive can be:

- `{"blank": true}` — ship empty
- `{"constant": "X"}` — write X
- `{"rule": {...}}` — run a transformation rule

`directive_for()` resolves sheet-specific vs bundle-wide (`applies_to_all_sheets`) by
**date** — latest wins, not most-specific-wins.

---

## 4. Where things live

```
backend/app/
  main.py               startup: DB init, router registration, background seeds
  database.py           init_beanie — EVERY model must be listed here
  config.py             settings (env vars)

  models/               Beanie Documents — one file per area
    conversion.py         Conversion
    dataset.py            Dataset, DatasetColumnProfile
    fbdi.py               FBDITemplate, FBDISheet, FBDIField, GoldStandard
    mapping.py            MappingSuggestion
    learned.py            LearnedMapping  ← read the find() override, see §7
    transformation.py     TransformationRule, Crosswalk, RULE_TYPES
    output.py             ConvertedOutput

  routers/              one file per API area, all prefixed /api
  services/             the logic (see §1 for the four that matter)
  transformations/engine.py   apply_rule / apply_pipeline — the rule engine
  validation/engine.py        row validation
  parsers/                    tabular + FBDI template parsing
  ai/rule_based.py            deterministic candidate scoring
  data/*.json                 SEEDED KNOWLEDGE — mappings, defaults, column orders
  data/fbdi_templates/        the real Oracle .xlsm templates

frontend/src/
  App.tsx               routes
  api/index.ts          one exported *Api object per backend area
  api/client.ts         axios instance + auth token
  pages/                one per route
  components/           shared + feature folders (transforms/, learn/, …)
  types/index.ts        TS mirrors of the backend schemas
```

### `data/*.json` is code

These files are the client's actual knowledge — mapping decisions, column orders,
strategy corrections. They are seeded into MongoDB on **every** startup by
`services/catalog_seed_service.py`, and every seeder respects tombstones.

**Changing a JSON here is usually the right way to change behaviour.** Adding a
hardcoded constant in Python usually is not.

---

## 5. Change recipes

### 5.1 Add a transformation rule type

Say you want `STRIP_LEADING_ZEROS`.

1. **Engine** — `transformations/engine.py`, in `apply_rule`:
   ```python
   if rt == "STRIP_LEADING_ZEROS":
       return _to_str(value).lstrip("0")
   ```
2. **Allow it** — add `"STRIP_LEADING_ZEROS"` to `RULE_TYPES` in
   `models/transformation.py`. Not in that tuple, the API rejects it.
3. **Declare any columns it reads** — if it reads columns other than its own,
   add it to `output_service._rule_referenced_columns`. **Skipping this is the single
   most common way to ship an inert rule** (see §7.1).
4. **UI** — `frontend/src/components/transforms/RuleAuthorModal.tsx`, add an entry to
   `RULE_SPECS` and put it in a `RULE_GROUPS` bucket. If you don't want to write a
   form, use `Form: RawConfigOnly`.
5. **Test** — `backend/tests/test_column_rules.py` or a new file.

> `RULE_SPECS` (24 types) and `RULE_TYPES` (32) drifting apart is what crashed the
> rule dialog. There is no error boundary in the frontend, so an unknown type used to
> unmount the whole page silently.

### 5.2 Change what a field defaults to

Four different layers can put a value in a column. Pick the right one:

| You want | Edit |
|---|---|
| A standard constant for one object | `data/supplier_default_values.json` (or the customer/item equivalent) |
| A signed strategy rule (blank / constant / derived) | `data/supplier_strategy_defaults.json` → `strategy_overlay` |
| A hardcoded control constant | `output_service._CONTROL_DEFAULTS` — **prefer not to**; see §7.4 |
| A per-conversion value | the analyst does it in the UI; it lands on `MappingSuggestion.default_value` |

After editing a `data/*.json`, restart (startup seeds re-run) or call the matching
`POST /api/learned-mappings/reseed-*` endpoint.

### 5.3 Add or change a seeded learning

1. Edit the JSON in `backend/app/data/`.
2. Give the file an `_effective_date` — that becomes the learning's `effective_date`
   and therefore its rank against the analyst's own edits.
3. If it is a new file, add a seeder to `services/catalog_seed_service.py`. Copy an
   existing one — the shape is:
   ```python
   existing = await LearnedMapping.find_one(..., include_deleted=True)   # tombstones!
   if existing and getattr(existing, "is_deleted", False):
       retired += 1; return          # the analyst retired it — respect that
   ```
4. Wire it into `main.py::_run_seeds_background` **and** add a
   `POST /learned-mappings/reseed-<name>` in `routers/learned.py`, so you can check it
   without a redeploy.
5. Scope it. `sheets=[...]` / `exclude_sheets=[...]` if it belongs to specific
   interface sheets — Oracle repeats field names across sheets (Customer has 19).

### 5.4 Add a field to the UI

1. **Backend schema** — `schemas/<area>.py`, add it to the `*Out` model.
2. **Serializer** — the router usually builds the dict by hand; add the key there too.
3. **TS type** — `frontend/src/types/index.ts`.
4. **API** — usually nothing; the `*Api` object passes through.
5. **Page** — render it.

Sanity check: `curl` the endpoint and confirm the key is in the JSON before touching
React. Half of "the UI doesn't show it" is the serializer, not the component.

### 5.5 Add an API endpoint

1. Pick the router in `routers/` (or add one, and register it in `main.py`).
2. ```python
   @router.post("/conversions/{cid}/my-thing")
   async def my_thing(cid: str, user: User = Depends(get_current_user)):
       ...
   ```
   `get_current_user` is the auth dependency. Every real endpoint has it.
3. Return plain dicts or a `response_model`. **ObjectIds must be stringified** —
   `schemas/oid.py` exists because forgetting this produced opaque 500s that looked
   like CORS errors in the browser.
4. Frontend: add a method to the matching `*Api` object in `api/index.ts`.

### 5.6 Add a field to the database

Beanie/Mongo is schemaless, so this is easy — and that is the danger.

1. Add the field to the `Document` in `models/`, **with a default**:
   ```python
   my_field: Optional[str] = None
   ```
   Every existing row lacks it. No default means every read of an old row explodes.
2. A brand-new model must be added to `document_models=[...]` in `database.py`, or
   every query against it fails at runtime, not at import.
3. There are **no migrations**. Backfill with a script or a one-shot endpoint if you
   need old rows populated.
4. If the field affects generation, it also needs to reach `output_service` — the
   model having it is not the same as the generator reading it.

### 5.7 Change an FBDI column order or file name

`data/supplier_fbdi_column_order.json` / `customer_fbdi_column_order.json`. Each
sheet carries `fbdi_order` (the worksheet order a human sees) and `csv_order` (what
the loader reads). **They differ on three of the fifteen Customer interfaces.**

The CSVs are headerless, so position is the only thing carrying meaning. A file in
the wrong order has the same column count and looks perfectly well formed.

Applied by `services/supplier_fbdi_layout.py`. Tested in
`tests/test_customer_fbdi_sequence.py` against **measured indices**, not rules of
thumb — there is no tidy rule, which is the argument for encoding it as data.

### 5.8 Add a new target object (e.g. a new FBDI interface)

1. Put the Oracle template in `data/fbdi_templates/`.
2. `services/template_seed_service.py` parses it into
   FBDITemplate → FBDISheet → FBDIField.
3. If it is a multi-file load, add it to `OBJECT_TEMPLATE_CATALOG` in
   `services/object_fanout_service.py` so one upload generates the whole sequence.
4. Column order + CSV names: a `data/*_fbdi_column_order.json`.
5. Seeded mappings: a `data/*_field_mappings.json` and a seeder.

HCM is the exception — it is **HDL**, not FBDI. `services/hdl_schema.py` is the
single source of truth for the Employee load (components, attributes, order), and
`hdl_output_service.py` writes the `.dat` files.

---

## 6. Testing

```bash
cd backend
python -m pytest tests -q                    # ~670 tests, ~2 min
python -m pytest tests/test_column_rules.py -q -p no:cacheprovider
```

There are **no fixtures and no test database**. Tests are pure: they call the real
functions with hand-built objects, or they read the shipped source and assert on it.
That is deliberate — a DB-backed suite would not have caught most of the bugs in §7.

Three kinds of test here, and the distinction matters:

1. **Unit** — `apply_rule` with a value, assert the output.
2. **Data** — assert the shipped `data/*.json` says what the analyst said. If they
   change their mind, this fails first.
3. **Seam** — assert that layer A actually *calls* layer B. Usually by reading the
   source and checking a string is present. Ugly, and it is the only kind that
   catches §7.1, which is the failure this codebase repeats most.

Two pandas versions are in play (2.2.3 pinned, 3.0.2 system). Run both if you touch
anything using pandas.

---

## 7. Traps this codebase keeps falling into

Every one of these shipped. Read them before your first change.

### 7.1 Shipped and inert

**The dominant failure mode.** A capability lands, passes its tests against hand-made
inputs, and never meets real data. Real examples:

- `SELF_LOOKUP` read an index **nothing in the codebase ever built**. It returned its
  default on every row of every real run, and its unit test passed against a
  hand-made index.
- `applies_to_all_sheets` was written by the analyst into the corrections file and
  **read by nothing**. Two "all sheets" rules applied to one sheet.
- `LearnedMapping.sheets` / `exclude_sheets` worked correctly and **not one seeded
  learning used them** — the count was zero.
- The strategy overlay never declared the source columns its rules read, so they were
  pruned out of the frame. Supplier Site shipped empty on 8,561 rows.
- `TransformationRule.source_column` was stored, shown in the UI, and never read by
  the generator.

**Defence:** when you add a mechanism, write a test that asserts the *data* uses it,
or that the caller actually calls it. "The feature works" and "the feature runs" are
different claims.

### 7.2 The tombstone

`models/learned.py` overrides `find` to inject `{"is_deleted": {"$ne": True}}`. A
deleted learning is **invisible** to a normal `find`/`find_one`.

So any code that looks for an existing row without `include_deleted=True` sees
nothing, inserts, and **resurrects the learning the analyst deleted**. This has been
found four times, in six different functions.

```python
existing = await LearnedMapping.find_one(..., include_deleted=True)   # always
if existing and getattr(existing, "is_deleted", False):
    return None          # automatic path: respect it
    # OR, for an explicit user action: revive IN PLACE, don't insert a second row
```

Beanie's `Document.get()` delegates to `find_one`, so **`LearnedMapping.get()` is
tombstone-blind too** and takes no `include_deleted`. Never use it on this model —
`tests/test_tombstone_guards.py` enforces that.

### 7.3 Two layers disagreeing

The screen reads one thing, the file reads another, and they drift:

- The Mapping Review page carried its **own hardcoded copy** of the control-defaults
  table, checked before the server's answer. No rule, learning or button could touch
  a constant compiled into the browser bundle.
- `compute_effective_defaults` (what the screen shows) had no suppression check, so
  the UI reported "Defaulted → 900001" for a field the generated file already left
  blank.
- Learnings written under the template's `business_object` and read under the
  conversion's `target_object`.

**A correct fix that looks broken is worse than a wrong one** — it invites re-fixing
something that is not wrong. When you change what a value will be, check every layer
that *displays* it too.

### 7.4 The control-default table

`output_service._CONTROL_DEFAULTS` fills any column that arrives at finalize
**entirely blank**. It has no "the analyst mapped this" guard on the general branch.

So whenever a mapped column fails to materialise — a renamed source, a sheet-routing
miss, a mapping row that lost the dedup — the constant lands on every row and the
screen and file disagree. `Address Name = "PRIMARY"` was exactly this.

Use it only for columns nobody has an opinion about. If there is a signed opinion,
the strategy overlay is the right layer.

### 7.5 The preview lies

`row_index` was passed by the mapping **preview** endpoint and never by generation.
`SEQUENCE` therefore returned `start + 0` for every row: Party Number, a required
unique key, would have shipped `NXT000001` on all 5,489 rows **while the preview
showed a perfect running sequence.**

The screen being right is what makes this class dangerous. When you add anything that
depends on `ctx`, check both callers.

### 7.6 Silent swallowing

```python
except Exception:
    pass          # ← this
```

Propagation failures returned `200` with a normal payload and no count, so "reached
12 conversions" and "threw immediately" looked identical to the analyst. If you
swallow, **return the error in the response**.

### 7.7 Field names repeat across sheets

Oracle repeats a field name across interface sheets — Customer has 19. A name-keyed
learning reaches all of them. That is right for `id → Party Original System
Reference` and wrong for the same field on `HZ_IMP_CLASSIFICS_T`.

`sheets` / `exclude_sheets` + `learning_service.sheet_allowed` is the mechanism. Use
it, and check whichever layer you are adding actually calls it.

---

## 8. Running and deploying

### Local

```bash
docker compose up          # backend :8000, frontend :80
```

Or, separately:

```bash
cd backend  && uvicorn app.main:app --reload      # needs MONGODB_URI
cd frontend && npm install && npm run dev         # :5173
```

Key env vars: `MONGODB_URI`, `MONGODB_DB`, `SECRET_KEY`, `ADMIN_EMAIL`,
`ADMIN_PASSWORD`, `AI_PROVIDER`, `ANTHROPIC_API_KEY` (optional).

### Deploy

`render.yaml` — backend (Docker) and frontend (static) on Render, MongoDB Atlas.

Push with **`launch_git.bat`** in the repo root. The commit message lives in
`COMMIT_MSG.txt` and is passed with `git commit -F`, **not `-m`**: `cmd.exe` caps a
command line at 8,191 characters, and when the message grew past that, `git commit`
failed to launch, the script went straight on to push, and reported
"Everything up-to-date" — a silent no-commit that still printed DONE. The batch now
verifies the commit before pushing.

Render's free tier cold-starts. The first request after idle takes ~45 seconds; that
is not a bug in your change.

### Startup seeds

`main.py::_run_seeds_background` runs ~14 seeders on **every** boot. They are
idempotent and tombstone-respecting. If you add seeded data, add it there, and give
it an on-demand `reseed-*` endpoint so "did it land?" doesn't cost a redeploy.

---

## Quick reference — "where do I look?"

| Symptom | Start here |
|---|---|
| A column is blank in the output | `_transform_frame` — is the mapping discarded? is the source column pruned? |
| A column has the wrong constant | `_CONTROL_DEFAULTS`, then `strategy_overlay.directive_for` |
| A rule saves but does nothing | `_rule_referenced_columns` (§7.1), then the discard guard in `_transform_frame` |
| A learning doesn't appear in the Learning Centre | the object key — `object_keys_for_object`, §7.3 |
| A change doesn't reach other conversions | `propagate_learning_to_open_conversions` — check the date comparison |
| A deleted learning came back | §7.2 |
| The UI shows something different from the file | §7.3 — find the second reader |
| Columns are shifted in a CSV | `*_fbdi_column_order.json` — `fbdi_order` vs `csv_order` |
