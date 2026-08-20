# Trinamix Conversion Workbench — Clean Architecture, Phase 2 (As-Built)

**Status: Phase 2 (services-layer decomposition) — COMPLETE and deployed.**
Every slice verified byte-identical (differential + golden manifest), each shipped and
re-verified live against the Customer–New–1308 golden.

Phase 1 turned the rule *engine* into a strategy registry over a pure domain. Phase 2
turned the largest *service* — the `output_service` FBDI generator — from a god-object
that held business logic inline into a thin orchestrator that **delegates** to the domain.

---

## 1. What Phase 2 did

Extracted the pure, testable logic that was trapped inside `output_service` into the
domain, one golden-gated slice at a time.

| Slice | Extracted → domain | Result |
|---|---|---|
| 1 | ctx-index builders → `rules/indexes.py` | `output_service` 3657→3422 |
| 2 | rule → source-column analysis → `rules/columns.py` | 3422→3299 |
| 3 | frame formatting (dates/sentinels/email/sheet) → `frames.py` | 3299→3151 |
| 4 | the per-row context object → `rules/row.py` | 3151→3071 |
| 5 | column/header helpers → `frames.py` | 3071→3042 |

**`output_service.py`: 3,657 → ~3,042 lines (−17%).** Six new/expanded domain modules;
**five service→service couplings removed** (`learning_service`, `strategy_overlay`,
`routers/fusion`, `routers/operations` no longer reach into `output_service`).

### The dependency rule now holds for the services layer
```
Interface (FastAPI routers) → Application/Services (orchestrators) → Domain (pure)
                                                                      ↑ never imports outward
```
`output_service`'s async functions (`generate_output_artifact`, `build_converted_dataframe`,
`_transform_frame`, …) remain in the service layer **by design** — they coordinate DB
reads, chunked frame building and file assembly. What changed is that they no longer
*contain* pure business logic: index building, column analysis, the row object, and all
frame formatting are domain calls now.

---

## 2. Why the orchestrators were not "extracted"

`_transform_frame` and `generate_output_artifact` are async, DB-bound, and built on
closures over per-frame mutable state. That is application orchestration, not domain
logic — the clean-architecture goal is that they hold **no independently-testable pure
logic**, which is now true: they delegate every pure step (`apply_pipeline`,
`RowWithTargets`, `build_*_index`, `to_fbdi_date`, `rule_referenced_columns`, …) to the
domain. Forcing them into "ports and adapters" for its own sake would have added
indirection without removing risk. Further decomposition (e.g. a `FileAssembler` port,
a `FrameBuilder` port) is available as optional Phase 3 work, not a correctness gap.

---

## 3. How it was verified (every slice)

Same three gates as Phase 1:
1. **Differential** — the old function(s), AST-extracted verbatim from `output_service`,
   vs the new domain module, byte-identical across a battery of inputs (16 + 18 + N +
   6 + N cases across the five slices).
2. **Unit tests** — `tests/unit/test_indexes.py`, `test_columns.py`, `test_frames.py`,
   `test_row.py` pin each extracted contract.
3. **Golden manifest** — a real Customer–New–1308 regen `--check`ed against the committed
   per-sheet SHA-256 manifest after each deploy: **IDENTICAL, no drift**, all five times.

One deploy incident (a partial commit that stranded a new module) was diagnosed from the
container logs and fixed; the push scripts were then hardened to stage the whole
`backend/` tree so a re-run can never commit a half-slice.

---

## 4. Domain layer after Phase 2

See `backend/app/domain/README.md` for the full map. New in Phase 2:
`rules/indexes.py`, `rules/columns.py`, `rules/row.py`, and the expanded `frames.py`.

## 5. Optional next (Phase 3, not required)

- Port the file-assembly + naming out of `generate_output_artifact` behind a
  `FileAssembler` interface (the `supplier_fbdi_layout` module is already a
  dependency-light adapter — this would formalise the seam).
- Split `strategy_overlay` into a directive **store** (file load + cache adapter) and a
  pure **selection policy** (→ domain), introducing the first real port.
- Thin `_transform_frame`'s per-column loop into a documented pipeline of named steps.

None of these are behaviour or correctness gaps — they are further separation-of-concerns
polish on top of a services layer that is already thin and a domain that is already pure.
