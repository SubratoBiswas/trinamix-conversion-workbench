# Trinamix Conversion Workbench — Clean Architecture, Phase 3 (As-Built)

**Status: Phase 3 (store / policy separation + precedence consolidation) — COMPLETE and deployed.**
Every slice verified byte-identical (differential + golden manifest), each shipped and
re-verified live against the Customer–New–1308 golden.

Phase 1 turned the rule *engine* into a strategy registry over a pure domain. Phase 2
thinned the `output_service` god-object into an orchestrator that delegates to that domain.
Phase 3 took the **decision logic still trapped inside the services** — which directive
applies, how a file is laid out, which dated statement wins — and moved it into the domain
behind a single recurring pattern: a **store** (the I/O half — reads files or Mongo, caches)
and a pure **policy** (the decision half — takes the loaded data as arguments and decides).

---

## 1. What Phase 3 did

Five golden-gated slices, each byte-identical to the deployed baseline it replaced.

| Slice | Extracted → domain | Store left behind in `services/` |
|---|---|---|
| 1 | Directive **selection** → `domain/directives/policy.py` | `strategy_overlay` (reads 5 JSON files, caches) |
| 2 | FBDI **layout** — reorder, END, load scope, file naming → `domain/fbdi/layout.py` | `supplier_fbdi_layout` (reads 4 spec files, caches) |
| 3 | Mapping-vs-directive **precedence** (`_explicit`) → `precedence/policy.mapping_outranks_directive` | `output_service._transform_frame` keeps the loop |
| 4 | Dated-store **read/resolve core** — `Entry` / `applies` / `_order` / `resolve` → `domain/store/resolver.py` | `mapping_store` (Mongo adapter: query, write, compaction) |
| 5 | One **ordering** behind all write-time precedence contests → `precedence/policy.pick_latest` | — (call sites unchanged) |

Two service modules that were 100% "pure logic + I/O in one file" are now thin stores over a
pure policy, and one was cut in half:

- **`mapping_store.py`: 1,169 → 775 lines.** The `Entry`, the applicability rules and the
  "one key, one date, one winner" resolver live in `domain/store/resolver.py`; the module now
  holds only the Mongo-facing adapter and re-exports the core (`mapping_store.Entry` **is** the
  domain `Entry`, so every caller is unchanged).
- **`strategy_overlay.py`** and **`supplier_fbdi_layout.py`** became stores whose every public
  function loads its data then delegates to `domain/directives/policy` and `domain/fbdi/layout`
  respectively — signatures untouched.

### The store / policy pattern

```
                 reads files / Mongo, caches            takes loaded data, decides
   caller ──►  services/<x>  (the STORE, adapter)  ──►  domain/<x>  (the POLICY, pure)
                    ▲ keeps the public signature              ▲ no I/O, unit-testable with a dict
```

The policy half never touches disk or the database. That is the domain's standing invariant
(no `app/domain/*` module does file or DB I/O), and it is what makes each policy testable
against a hand-built dict with no Beanie/Mongo stack in sight.

---

## 2. The two resolvers, and why they stay separate

Phase 3 finished with **two** "whichever is latest" resolvers, on purpose:

- **`domain/store/resolver.py`** ranks **library entries against each other** — the dated
  `LearnedMapping` statements for a `(client, source, field, sheet)` key. Undated statements
  sort **last** (an entry that cannot be placed in time cannot be shown to be newer).
- **`domain/precedence/policy.py`** runs the two **write-time contests** — a conversion's own
  mapping/rule/approval against a strategy **directive**, and a sheet-specific directive against
  a bundle-wide one. Here an undated authored *rule* is "left alone" and treated as **newest**.

These undated conventions are **opposite**, deliberately, because the contests mean different
things — so the two orderings are *not* merged. Slice 5 unified the four write-time functions
(`conversion_rule_wins`, `person_is_newer`, `wide_directive_wins`, `mapping_outranks_directive`)
behind one `pick_latest` over a common `Statement`, turning three tie-breaks that used to be
scattered `>=` / `>` operators into explicit, named, individually-tested terms.

### The three tie-break terms are deliberate — do not "clean them up"

Slice 5 made these explicit precisely so a future refactor does not silently flip them for the
sake of uniformity. Each protects a real behaviour:

| Term | What it does | Why it exists (do not flip) |
|---|---|---|
| `undated_is_newest` (rules) | an undated authored rule beats a dated directive | protects an analyst's hand-typed rule from being silently overridden by a strategy default |
| `CONVERSION > directive` on a tie | a conversion's approval/rule wins a same-instant tie | the analyst's specific statement outranks the generic directive (the old `>=`) |
| `EXACT > WIDE` on a tie | a sheet-specific directive wins a tie over a bundle-wide one | most-specific-wins for precision (the old `>`, exact wins unless wide is *strictly* newer) |

Making all three obey one mechanical rule would break these intentions. They are correct as
written; the value delivered was making them visible and pinned, not making them identical.

---

## 3. How it was verified (every slice)

The same three gates as Phase 1 and Phase 2:

1. **Differential** — the DEPLOYED baseline module (re-staged from the live tree) vs the new
   store+policy, byte-identical across a battery. Totals per slice:
   - Slice 1: 5,481 directive cases + 4,698 facade cases.
   - Slice 2: 1,362 frame cases across all 15 Customer + 5 Supplier + 4 BOM interfaces,
     including the 87-wide `HZ_IMP_LOCATIONS_T` duplicate-name reorder.
   - Slice 3: the full mapping-provenance space (status × approver × dates × value × rules ×
     directive).
   - Slice 4: 5,760 rows + 5,265 resolve/resolve_all cases + 1,944 mapping objects.
   - Slice 5: 27,105 comparisons, with a date universe carrying an exact duplicate instant to
     hammer the `>=` / `>` boundaries and undated values to hammer the left-alone rule.
2. **Unit tests** — `test_directives.py`, `test_fbdi_layout.py`, `test_precedence_mapping.py`,
   `test_store_resolver.py`, `test_statement_ordering.py` pin each contract (**77 new tests**).
3. **Golden manifest** — a real Customer–New–1308 regen `--check`ed against the committed
   per-sheet SHA-256 manifest after each deploy: **IDENTICAL, no drift**, all five times.

The container recycles and the git-bridge's whole-`backend/` staging (hardened in Phase 2)
carried through cleanly; no partial-commit incidents this phase.

---

## 4. Domain layer after Phase 3

```
app/domain/
├── text.py               to_str / is_blank / to_float / TRUEISH / FALSEISH
├── frames.py             frame formatting (dates, sentinels, email mask, sheet names, headers)
├── dates/fbdi_date.py    FbdiDate; parse_with_formats; oracle_date_to_py; OUT_DATE_FORMAT
├── geo/country.py        COUNTRY_TO_ISO + _ISO_SET
├── phone/parse.py        phone_split + phone_region_for
├── directives/policy.py  NEW select_directive / blank_fields_for / apply_frame_rules …   (slice 1)
├── fbdi/layout.py        NEW reorder_to / apply_*_layout / *_name_for / load-scope           (slice 2)
├── precedence/policy.py  Statement + pick_latest; the 4 contest functions          (slices 3, 5)
├── store/resolver.py     NEW Entry / applies / _order / resolve / resolve_all         (slice 4)
└── rules/                strategy registry + library (39 strategies)               (Phases 1–2)
    ├── strategy.py, engine.py, registry.py, context.py, row.py, indexes.py, columns.py
    └── library/          string, regex, value, numeric, stateful, date, geo, phone, lookup ops
```

The services that used to own these are now adapters: `strategy_overlay` and
`supplier_fbdi_layout` load-and-delegate; `mapping_store` is the Mongo half of the dated store;
`output_service._transform_frame` reads a boolean from `precedence/policy` instead of computing
precedence inline.

### The dependency rule still holds
```
Interface (FastAPI routers) → Application/Services (adapters + orchestrators) → Domain (pure)
                                                                                 ↑ never imports outward, never does I/O
```

---

## 5. What's next — Phase 4 (recommended, not yet started)

The **rule engine** (Phase 1) and the **precedence + directive + layout + store decisions**
(Phase 3) are now in the domain. The largest remaining pure-logic-in-services module is
**`customer_merge.py` — ~1,460 lines, zero `await`, no model imports** — the multi-source
customer grain / party-linkage / DFF-UDCP / contact fan-out logic, operating entirely on
DataFrames. It is a textbook domain candidate and migrates the same way: name the seams,
extract one concern per golden-gated slice, leave a thin adapter, differential + golden each
step. (Migrating it also resolves the one remaining inward-dependency smell, where a domain
date helper is currently reached into from this service.) Other, smaller candidates the sweep
surfaced: `entity_resolution`, `address_arbiter`, `bom_structure_service`, `cleansing_rules`.

**Deliberately *not* done:** flipping any of the three precedence tie-break terms to a single
"clean" convention. As documented in §2, each is a correct, intentional protection; unifying
them would be a behaviour change for the sake of tidiness. If future assurance is wanted, the
right tool is a read-only measurement of whether each term ever fires on live data — not an
edit.

---

## 6. How to keep it clean

- **Never** import `app.services` / `app.routers` / framework code from `app/domain`, and never
  do file or DB I/O there. Pure logic goes in a **policy**; its I/O goes in a **store** that
  delegates to it and keeps the public signature.
- Every output-affecting change must keep the golden byte-identical, or be a deliberate,
  reviewed output change with the golden re-saved.
- Before shipping: `pytest tests/` (unit + differential), then a Customer regen
  `verify_goldens.py --check`.
- The three precedence tie-break terms in `precedence/policy.py` are deliberate (§2). Change one
  only as an explicit, measured, signed-off behaviour change — never as a "simplification".
