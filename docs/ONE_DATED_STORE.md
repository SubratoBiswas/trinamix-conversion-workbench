# One dated store

Agreed with the analyst, 02-Aug-2026. This is the next change, before anything else.

> "Mappings, learnings and user inputs should be stored in the same place with date
> (with respect to client and source), whichever is latest as per date the mapping
> will happen in that way, and the same will be used for existing projects and
> future projects."

And on per-conversion overrides:

> "That's fine, the analyst mapping wins as that's the latest mapping as per date."

## The rule, in full

- **One store.** Every statement about how a field should be mapped is a dated entry.
- **Key:** `(client_id, source_erp, target_field)`.
- **Sources are all equal:** mapping workbook, gold standard, learning, steer box,
  grid edit, custom rule. Each writes an entry.
- **Newest wins.** Authorship is provenance, not precedence.
- **No scopes.** No object scope, no project scope, no per-conversion override. An
  edit is the client's newest statement about that field and applies everywhere.
- **Per-conversion mapping rows become a VIEW**, regenerated from the store. Never
  the source of truth.
- Applies to every project, existing and future.

## Why (do not re-litigate this)

Today there are TWO stores: `LearnedMapping` (dated, client+source scoped) and
`MappingSuggestion` (per-conversion rows, which generation actually reads). The
library is COPIED into the rows.

Every "the screen says one thing and the file says another" bug in the last week was
those two disagreeing, and no amount of fan-out fixing closes it, because the copying
IS the problem. A partial list, all one shape:

- a stale flag written by every path and read by none
- `mapping_sync` stripped by `response_model`
- a client-scope guard that could never fire
- `output_status` recorded and never returned
- seventeen templates claiming one object with no notion of which was current
- object-scoped fan-out reaching one conversion of six, fixed three times in three
  different call sites because there was no single place to fix it

Adding a scope back on day one would be repeating that on purpose. If a genuine
one-off ever appears, it is a nullable `conversion_id` on an entry plus a filter —
still latest-wins, still one rule. Not a redesign. Do not build it pre-emptively.

## Shape

`MappingDecision` (new collection, or `LearnedMapping` widened — decide first):

    client_id        required
    source_erp       required
    target_field     required, normalised
    decision         source_column | default_value | suppress | rule
    value            the column name, the constant, or the rule config
    effective_date   WHEN THE INSTRUCTION WAS GIVEN — the ordering key
    captured_from    workbook | gold | steer | grid | rule | seed   (provenance only)
    captured_by      actor
    is_deleted       tombstone, unchanged semantics

`effective_date` must never move on a read. A startup seed that finds nothing to do
must not re-stamp anything — `captured_at` did exactly that to the learnings layer and
inverted precedence on every redeploy.

## Order of work

1. **Resolver first, alone.** `resolve(client, source, field) -> winning entry`.
   Pure, unit-tested against a table of competing entries. No callers yet.
2. **Backfill.** Every existing `LearnedMapping` and every human-decided
   `MappingSuggestion` becomes an entry, carrying its existing date. Idempotent, and
   it must be possible to run it twice.
3. **Writes.** Route all six paths through one function. Assert in a test that no
   path writes a mapping any other way — the six-call-sites problem is what this
   whole change exists to end.
4. **Reads.** Generation resolves through the store. `MappingSuggestion` is
   populated from it for display, marked as derived.
5. **Delete the copy paths** — `apply_learned_to_conversion`,
   `propagate_learning_to_open_conversions` and the fan-out plumbing become
   unnecessary. That deletion is the proof the change landed.

Do not merge 3 before 2, or existing projects silently lose their history.

## Verification, before calling it done

Regenerate Supplier, Customer and Employee and diff against the 02-Aug bundles. The
files should be identical except where a genuinely newer decision changes a value. A
diff anywhere else means the backfill lost something.
