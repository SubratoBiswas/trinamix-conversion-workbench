# Trinamix Conversion Workbench — Clean Architecture, Phase 1 (As-Built)

**Status: Phase 1 (transformation rule engine) — COMPLETE and deployed.**
Live commit `e542830`, health `ok`, Customer–New–1308 regen byte-identical to golden.

This document records what was actually built, how it was proven safe, and what comes
next. It supersedes the earlier forward-looking blueprint for the rule-engine portion.

---

## 1. What Phase 1 did

Turned the transformation engine from a ~950-line `if/elif` monolith into a **strategy
registry** with a pure domain layer. Every one of the **39 rule types** is now an
independently testable strategy class; `engine._apply_one_rule` is nothing but dispatch.

| Metric | Before | After |
|---|---|---|
| `engine.py` | ~950 lines, 40-branch `if/elif` | **91 lines**, pure dispatch |
| Rule logic location | inline in `engine.py` | `app/domain/rules/library/*` (9 files) |
| Reference data (dates, country, phone) | inline in `engine.py` | `app/domain/{dates,geo,phone}` |
| Shared helpers | inline in `engine.py` | `app/domain/text.py`, `domain/rules/context.py` |
| Adding a rule type | edit the `if/elif` chain | one class + one `register()` line |
| `if rt == …` branches in engine | ~40 | **0** |

### The dependency rule
Dependencies point inward only:

```
Interface (FastAPI routers)  →  Application/Services  →  Domain
                                                          ↑ never imports outward
```

`app/domain/*` imports no framework and no service. `app/services/deterministic.py`
re-exports the relocated country table so existing service callers are unchanged.

See `app/domain/README.md` for the full layout and "where does X live now" map.

---

## 2. How it was done — strangler fig behind golden gates

The engine was migrated **one slice at a time**, each slice byte-identical, independently
revertible, and independently deployable. No big-bang rewrite.

The pattern for every slice:

1. **Extract** a rule branch (or a helper/table) into a domain module, reproduced
   **verbatim** — only `cfg` → `config` renames and a defensive `ctx = ctx or {}`.
2. **Register** the strategy; **remove** the branch from `engine.py`.
3. **Differential test** — run the OLD tree and the NEW tree in separate subprocesses over
   a battery of `(value, config, row, ctx)` inputs; assert the JSON results are
   **byte-identical**.
4. **Golden check** — regenerate the real Customer–New–1308 FBDI workbook and compare to a
   committed per-sheet SHA-256 manifest (`tests/characterization/verify_goldens.py`).
5. **Unit tests** — pin each strategy's contract in `tests/unit/test_rules.py`.
6. **Ship** — one scoped commit, deploy, re-verify the golden on the live build.

### Three independent proofs of "no behaviour change", every slice
- **Differential batteries** (old vs new, byte-identical): 70 + 47 + 77 + 35 = **229 cases**
  across the four migration batches, plus regression samples.
- **Golden manifest**: 15 sheets, per-sheet SHA-256 + row/col counts — every regen `IDENTICAL`.
- **Unit suite**: `tests/unit` now **59 passing** (rules, dates, precedence, context).

---

## 3. Migration timeline (deployed slices)

| Commit | Slice | Result |
|---|---|---|
| `8898db6`→`9014886` | registry + first 18 rule types + helper relocation | golden-clean |
| `cf06f36` | 8 stateful types (CONCAT/COALESCE/CASE_WHEN/…) | golden-clean |
| `55205a1` | 4 date types + date helpers → `domain/dates` | golden-clean |
| `e8841a9` | geo/phone types + country table → `domain/geo`, phone → `domain/phone` | golden-clean |
| `e542830` | 5 index-backed lookup types → engine is pure dispatch | golden-clean |

A deploy incident along the way (a partial commit missing `date_ops.py`) was diagnosed
from the container logs and fixed by completing the slice — a good demonstration that the
scoped-commit discipline makes such issues obvious and recoverable.

---

## 4. Domain layer (as-built)

```
app/domain/
├── text.py            to_str / is_blank / to_float / TRUEISH / FALSEISH
├── dates/fbdi_date.py FbdiDate value object; parse_with_formats; parse_any_date;
│                      oracle_date_to_py; OUT_DATE_FORMAT; strptime format lists
├── geo/country.py     COUNTRY_TO_ISO + _ISO_SET
├── phone/parse.py     phone_split (libphonenumber) + phone_region_for + region table
├── precedence/policy.py   conversion_rule_wins / person_is_newer / wide_directive_wins
└── rules/
    ├── strategy.py    RuleStrategy protocol
    ├── engine.py      RuleEngine (register / __contains__ / apply)
    ├── registry.py    standard_rule_engine() — the wiring
    ├── context.py     _resolve_column / _row_value_ci / _interpolate / _branch_holds /
    │                  _COMPARISON_OPS / _PLACEHOLDER
    └── library/       string_ops, regex_ops, value_ops, numeric_ops, stateful_ops,
                       date_ops, geo_ops, phone_ops, lookup_ops   (39 strategies)
```

Test-only follow-up done as part of lock-in: two older tests reached past the public API
into engine internals that moved (`_oracle_date_to_py`, `_COMPARISON_OPS`); both were
updated to import from their new domain homes. No other test touched engine internals.

---

## 5. What's next — Phase 2 (recommended, not yet started)

The rule engine was one subsystem. The **services layer** is the larger remaining debt —
`customer_merge.py`, `output_service.py`, and `strategy_overlay.py` are big files that mix
several concerns (frame merging, owned-field stamping, DFF/UDCP, precedence/overlay
selection, FBDI sheet assembly, date coercion). The same strangler-fig method applies:

1. **Name the ports** — e.g. `MergePolicy`, `OwnedFieldStamper`, `OverlaySelector`,
   `FbdiAssembler`, `DateCoercer` — as small interfaces the services depend on.
2. **Extract adapters** behind those ports, one concern per slice, each golden-gated.
3. **Thin the god-objects** to orchestration only, the same way `engine.py` was thinned.

Also outstanding (functional, separate track): propagate the UI-mapping-persistence fix
beyond customer/NetSuite to the other modules (Supplier eBOS, BOM, Employee), and the
deferred B1 live-preview fidelity item.

---

## 6. How to keep it clean

- **Never** import `app.services` / `app.routers` / framework code from `app/domain`.
- Every output-affecting change must keep the golden byte-identical, or be a deliberate,
  reviewed output change with the golden re-saved.
- Before shipping: `pytest tests/unit`, then a Customer regen `verify_goldens.py --check`.
- Adding a rule type never edits `engine.py` — one strategy class + one `register()` line.
