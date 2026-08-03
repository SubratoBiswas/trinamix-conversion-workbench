# One dated store — what landed

Built against `docs/ONE_DATED_STORE.md`, with the key you specified:
**`(client, source system, target field)`**. No object scope.

Steps 1–4. Step 5 (deleting the copy paths) is deliberately not in this change.

**Tests: 843 passing, 13 skipped, 0 failing.** Baseline before the change was 747.

---

## The rule, as implemented

`backend/app/services/mapping_store.py` is the whole of it.

- Every statement about how a field should be mapped is a dated **entry**, keyed
  `(client_id, source_erp, target_field)`.
- Four decisions: `source_column`, `default_value`, `suppress`, `rule`.
- Workbook, gold, learning capture, steer box, grid edit, custom rule — all write
  entries of the same shape through **one function**, `record_decision`.
- **Newest wins.** `captured_from` / `captured_by` are recorded and read by nothing
  that decides a winner.
- No object scope, no project scope, no per-conversion override.
- A `MappingSuggestion` row is a **view**: marked `derived`, with `derived_from`
  naming the entry it came from. A row a person edits stops being derived and
  becomes an entry in its own right, carrying `approved_at` as its date.

### Precedence tiers that are gone

Each of these used to compete with the date, and they disagreed with each other:

| Removed | Was in |
|---|---|
| suppression-loses-to-mapping | `apply_learned_to_conversion` |
| strong-transform candidate ordering | `_candidate_order` |
| a separate date ordering for defaults | the constant-default pass |
| object-key spelling widening (`$in` over 5 spellings) | every query in the apply pass |
| a human approval being permanently immune | `_eligible` — it had **no date test at all** |
| `>` vs `>=` disagreeing on an exact tie | propagation vs blank/rule corrections |

There is now one ordering, in one function, with one tie-break that is only
reached when two entries bear the same instant.

---

## The four steps

**1. Resolver, alone.** `resolve(entries, client, source, field, sheet) -> winner`
and `resolve_all` for a whole sheet. Pure — no database — tested against a table of
competing entries (`tests/test_one_dated_store.py`, 48 tests). `resolve_all` is
asserted against `resolve` rather than assumed to match it.

**2. Backfill.** `mapping_store_backfill.py`. Every human-decided
`MappingSuggestion` becomes an entry carrying its **existing** `approved_at`; every
undated library row gets `effective_date` from its `captured_at`, once. Runs at
startup after the seeders, and on demand at `POST /api/learned-mappings/backfill-dated-store`.
Idempotent: a run with nothing to do writes nothing, and no date ever moves.

**3. One writer.** Eleven seeder sites, the workbook import, apply-proposal, gold,
steer, manual-map, the AI-default cache and auto-capture all now go through
`record_decision`. `tests/test_one_dated_store_writes.py` walks the AST of every
module and fails if anything else constructs a mapping-decision learning. Three
exceptions remain and each is asserted to be a kind that is *not* a mapping
decision (crosswalk is one row per source value, file-signature identifies a file,
and the Learning Centre's own "add" button).

**4. Reads.** Generation resolves through the store before building the file, and
writes only rows that actually change.

---

## Two things worth your attention

### The heavy-object skip is gone

Generation used to skip the whole apply pass for objects with >300 fields — the
19-sheet Customer and Item loads — on the grounds that re-applying was what made
generation hang. That meant the biggest objects were the ones most likely to ship
against a stale copy.

The pass now reads the store **once** and writes only rows whose content actually
changes, so the gate is no longer needed and has been removed. Worth watching the
first heavy generate for timing.

### Dropping the object from the key merges 41 shipped decisions

Measured against the JSON in `backend/app/data/`, not estimated: 675 seeded
entries, **67 target-field names are claimed by more than one object**, and for
**41 of them the objects disagree about the answer**. Those now resolve to one
winner. Examples:

```
addressline1  (netsuite)
  2026-07-31  Supplier          source_column  address_1          <- NXT Supplier Mapping 3
  undated     Customer          source_column  addr1              <- NXT customer field mapping doc
  => the store now answers: address_1
```

Affected names include `Address Line 1/2/3`, `City`, `Country`, `Postal Code`,
`State`, `Phone`, `Email`, `Fax`, `Account Name`, `Account Number`,
`Payment Terms`, `Payment Method`, `First/Middle/Last Name`, `Supplier Name`.

**What limits the damage:** the apply pass only writes a decision whose source
column actually exists in that conversion's extract. A Customer extract has
`addr1`, not `address_1`, so the Supplier entry is skipped there rather than
writing a column that isn't in the file — the field falls back to the matcher
instead of going wrong. But the Customer mapping doc no longer *applies* to those
fields, because it is the older statement.

If that is not what you want for these 41, the smallest fix consistent with the
rule is to re-date the Customer document — it is the client's more recent statement
about those fields on Customer extracts, so giving it a date later than 31-Jul makes
it win on its own merits. That is a data change, not a code change.

Sheet scope (`sheets` / `exclude_sheets`) is preserved as an applicability
predicate on an entry — it is what the analyst *said* ("id maps to Party Original
System Reference, but not on `HZ_IMP_CLASSIFICS_T`"), not a tier that competes with
the date. Among entries that apply to a sheet, newest still wins outright. Say the
word if you want it gone too.

---

## Verification still outstanding

The plan asks for: regenerate Supplier, Customer and Employee and diff against the
02-Aug bundles. That needs MongoDB and the bundles, neither of which this sandbox
has. Everything short of it is done — full suite green, static analysis clean, the
app imports, and the resolver is asserted deterministic under input reordering.

Run the regenerate on your side and the diff should be empty except where a
genuinely newer decision changes a value — expect the 41 fields above to be where
any difference shows up.
