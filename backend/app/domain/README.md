# `app/domain` — the domain layer

Pure business logic and reference data. **No framework imports** (no FastAPI, no
SQLAlchemy, no I/O), and **nothing here imports `app.services` / `app.routers`** —
dependencies point inward only. Everything is unit-testable in isolation.

Two refactors built this layer, each behind a golden-file gate (every slice
byte-identical, independently deployable):

- **Phase 1** moved the transformation **rule engine** here — 40 rule types became
  strategy classes; `transformations/engine.py` is now a 91-line dispatcher.
- **Phase 2** thinned the **services layer** — the pure logic inside the big
  `output_service` orchestrator (index building, column analysis, the row object,
  frame formatting) moved here; `output_service.py` went 3,657 → ~3,040 lines and the
  async orchestrators are left as thin orchestration that *delegates* to this layer.

## Layout

```
app/domain/
├── text.py                     # to_str / is_blank / to_float / TRUEISH / FALSEISH
├── dates/fbdi_date.py          # FbdiDate value object; parse_with_formats; parse_any_date;
│                               # oracle_date_to_py; OUT_DATE_FORMAT; strptime format lists
├── geo/country.py              # COUNTRY_TO_ISO + _ISO_SET (name → ISO 3166-1 alpha-2)
├── phone/parse.py              # phone_split (libphonenumber) + phone_region_for + region table
├── precedence/policy.py        # conversion_rule_wins / person_is_newer / wide_directive_wins
├── frames.py                   # [P2] frame formatting: to_fbdi_date, format_date_columns,
│                               #   blank_null_sentinels, resolve_today_tokens, mask_supplier_emails,
│                               #   dedup, safe_sheet_name, normalize_columns, is_attribute_column,
│                               #   header_label
└── rules/
    ├── strategy.py             # RuleStrategy protocol (rule_type + apply(value, config, row, ctx))
    ├── engine.py               # RuleEngine: register / __contains__ / apply (dispatch by type)
    ├── registry.py             # standard_rule_engine() — one register() line per rule type
    ├── context.py              # row access + {Column} interpolation + branch conditions
    ├── row.py                  # [P2] RowWithTargets — the per-row context object a rule reads
    ├── indexes.py              # [P2] build_{self,sequence,group_first,city_country,city_case}_index
    ├── columns.py              # [P2] flat_cols / branch_columns / interpolated_columns /
    │                           #      rule_referenced_columns  (which source columns a rule reads)
    └── library/                # the 40 rule strategy classes, grouped by kind:
        ├── string_ops.py       regex_ops.py   value_ops.py   numeric_ops.py
        ├── stateful_ops.py     # CONCAT COALESCE CONDITIONAL CASE_WHEN BLANK_IF_EQUALS PREFIX SUFFIX SUFFIX_WHEN
        ├── date_ops.py         # FORMAT_DATE DATE_FORMAT CONDITIONAL_DATE COMPUTED
        ├── geo_ops.py          # COUNTRY_ISO2 CITY_COUNTRY_KEY
        ├── phone_ops.py        # PHONE_PART PHONE_STRIP_AREA
        └── lookup_ops.py       # SELF_LOOKUP CROSS_CONVERSION_LOOKUP GROUP_FIRST_FLAG SEQUENCE CROSSWALK_LOOKUP
```

`[P2]` marks modules added by Phase 2.

## Where does X live now?

| You want…                                        | It's here                        |
|--------------------------------------------------|----------------------------------|
| A specific rule type's behaviour                 | `rules/library/*_ops.py`         |
| `{Column}` interpolation, branch conditions      | `rules/context.py`               |
| The per-row object a rule reads (`row.get`, …)   | `rules/row.py`                   |
| Build the ctx index a lookup rule reads          | `rules/indexes.py`               |
| Which source columns a rule references           | `rules/columns.py`               |
| Date parse/format, sentinels, sheet/header names | `frames.py`, `dates/fbdi_date.py`|
| Country name → ISO / phone split                 | `geo/country.py`, `phone/parse.py`|
| "which decision is newer" precedence             | `precedence/policy.py`           |

Service-layer callers that need these import them back under their historical names,
so `output_service` / routers / `strategy_overlay` / `learning_service` are unchanged
at their call sites while the logic lives here.

## Rules of the layer

- **Purity.** Nothing under `domain/` imports `app.services`, `app.routers`,
  `app.database`, or any framework. (`pandas` / `phonenumbers` are pure compute libs and
  are allowed.)
- **Behaviour lock.** Every change keeps the generated FBDI workbook byte-identical
  (`tests/characterization/verify_goldens.py`), or it is a deliberate, reviewed output
  change with the golden re-saved. Before shipping: `pytest tests/unit`, then a Customer
  regen `verify_goldens.py --check`.
- **Adding a rule type** never edits `engine.py` — one strategy class + one `register()`.
