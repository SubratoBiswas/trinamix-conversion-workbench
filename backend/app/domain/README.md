# `app/domain` — the domain layer

Pure business logic and reference data. **No framework imports** (no FastAPI, no
SQLAlchemy, no I/O). Everything here is unit-testable in isolation and is the single
source of truth for how a transformation behaves.

The transformation engine (`app/transformations/engine.py`) is now a thin dispatcher:
`apply_rule` → `_apply_one_rule` → `_RULE_REGISTRY.apply(...)`. It owns **no** rule logic.
All 39 rule types live here as strategy classes.

## Layout

```
app/domain/
├── text.py                     # to_str / is_blank / to_float / TRUEISH / FALSEISH
├── dates/
│   └── fbdi_date.py            # FbdiDate value object; parse_with_formats; parse_any_date;
│                               # oracle_date_to_py; OUT_DATE_FORMAT; the strptime format lists
├── geo/
│   └── country.py              # COUNTRY_TO_ISO + _ISO_SET (name → ISO 3166-1 alpha-2)
├── phone/
│   └── parse.py                # phone_split (libphonenumber) + phone_region_for + region table
├── precedence/
│   └── policy.py               # conversion_rule_wins / person_is_newer / wide_directive_wins
└── rules/
    ├── strategy.py             # RuleStrategy protocol (rule_type + apply(value, config, row, ctx))
    ├── engine.py               # RuleEngine: register / __contains__ / apply (dispatch by type)
    ├── registry.py             # standard_rule_engine() — one register() line per rule type
    ├── context.py              # row access + {Column} interpolation + branch conditions:
    │                           # _resolve_column, _row_value_ci, _interpolate, _branch_holds,
    │                           # _COMPARISON_OPS, _PLACEHOLDER
    └── library/                # the strategy classes, grouped by kind:
        ├── string_ops.py       # TRIM UPPERCASE LOWERCASE TITLE_CASE REMOVE_HYPHEN
        │                       # REMOVE_SPECIAL_CHARS REPLACE PAD SUBSTRING SPLIT
        ├── regex_ops.py        # REGEX_REPLACE REGEX_EXTRACT
        ├── value_ops.py        # DEFAULT_VALUE CONSTANT VALUE_MAP MAP_BOOLEAN
        ├── numeric_ops.py      # NUMBER_FORMAT ARITHMETIC
        ├── stateful_ops.py     # CONCAT COALESCE CONDITIONAL CASE_WHEN BLANK_IF_EQUALS
        │                       # PREFIX SUFFIX SUFFIX_WHEN   (read other columns off the row)
        ├── date_ops.py         # FORMAT_DATE DATE_FORMAT CONDITIONAL_DATE COMPUTED
        ├── geo_ops.py          # COUNTRY_ISO2 CITY_COUNTRY_KEY
        ├── phone_ops.py        # PHONE_PART PHONE_STRIP_AREA
        └── lookup_ops.py       # SELF_LOOKUP CROSS_CONVERSION_LOOKUP GROUP_FIRST_FLAG
                                # SEQUENCE CROSSWALK_LOOKUP   (read a per-run ctx index)
```

## Where does X live now? (things that moved out of `engine.py`)

| You want…                                   | It's here                                   |
|---------------------------------------------|---------------------------------------------|
| Date parsing / formatting / Oracle tokens   | `domain/dates/fbdi_date.py`                 |
| Country name → ISO code                      | `domain/geo/country.py`                     |
| Split a phone into country/area/number/ext  | `domain/phone/parse.py`                     |
| `to_str` / `is_blank` / `to_float`          | `domain/text.py`                            |
| `{Column}` interpolation, branch conditions | `domain/rules/context.py`                   |
| "which rule/decision is newer" precedence   | `domain/precedence/policy.py`               |
| A specific rule type's behaviour            | `domain/rules/library/*_ops.py`             |

`app/services/deterministic.py` re-exports `COUNTRY_TO_ISO`/`_ISO_SET` from
`domain/geo/country.py`, so existing service-layer callers are unaffected.

## Adding a new rule type

1. Write a strategy class in the right `library/*_ops.py` (or a new file):

   ```python
   class MyRule:
       rule_type = "MY_RULE"
       def apply(self, value, config, row=None, ctx=None):
           ...
           return result
   ```

2. Register it in `registry.py` (one import + one entry in `standard_rule_engine()`).
3. Add the string to `RULE_TYPES` in `app/models/transformation.py` and a typed form on
   the frontend `TransformationStudioPage`.
4. Add a unit test in `tests/unit/test_rules.py`.

No edit to `engine.py` is ever needed — it dispatches by `rule_type` automatically.

## Guarantees to preserve

- **Purity.** Nothing under `domain/` may import from `app.services`, `app.routers`,
  `app.database`, or any framework. Dependencies point *inward* only.
- **Behaviour lock.** Every change is gated by `tests/characterization/verify_goldens.py`
  (per-sheet SHA-256 manifest) — a generated FBDI workbook must stay byte-identical unless
  the change is a deliberate, reviewed output change. Run the domain unit suite
  (`pytest tests/unit`) and a Customer regen `--check` before shipping.
