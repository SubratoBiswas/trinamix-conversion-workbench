# Scope — Extensible Flexfield (EFF) support for Item Import

**Context:** NextPower's item field mapping document (`000 NXT _ Item Field Mapping Document_SB.xlsx`) shows that the *majority* of item attributes across all five source systems are migrated as **Extensible Flexfields (EFF)**, not standard FBDI columns — NetSuite 179, Arena Ratana Lee 193, Arena Anaplan 146, Arena EBOS 21. Only SyteLine is mostly standard-field. The tool today maps to standard columns only, so for this client it currently produces a fraction of the real item payload. This document scopes what EFF support requires.

## 1. What an EFF load actually is

Extensible Flexfields let Oracle store custom item attributes without adding native columns. In the Item Import FBDI they load through two dedicated interface sheets (both already present in our seeded template):

- **`EGO_ITEM_INTF_EFF_B`** — the base table (131 columns)
- **`EGO_ITEM_INTF_EFF_TL`** — the translatable table (53 columns), for attributes that carry per-language values

Each row is keyed by **Item Number + Organization Code + Source System / Reference + `Attribute Group Code`**, and the attribute *values* go into **positional slots**, not named columns:

| Slot family | Count | Companion |
|---|---|---|
| `ATTRIBUTE_CHAR1..40` | 40 | — |
| `ATTRIBUTE_NUMBER1..20` | 20 | each has `ATTRIBUTE_NUMBERn_UOM_NAME` |
| `ATTRIBUTE_DATE1..10` | 10 | — |
| `ATTRIBUTE_TIMESTAMP1..10` | 10 | — |

So a value's destination is decided by three things: **which attribute group** it belongs to (`Attribute Group Code`), **which segment position** within that group (1..N), and its **datatype** (CHAR/NUMBER/DATE/TIMESTAMP). Example: if the group `NXT_MECHANICAL` has segments `[Material(char,1), Weight(number,1), Finish(char,2)]`, then for each item one `EGO_ITEM_INTF_EFF_B` row is written with `Attribute Group Code = NXT_MECHANICAL`, `ATTRIBUTE_CHAR1 = <material>`, `ATTRIBUTE_NUMBER1 = <weight>`, `ATTRIBUTE_CHAR2 = <finish>`.

## 2. Why this is different from standard mapping

Standard mapping is 1:1 and name-based: source column → named target column, one row per item. EFF is a **wide → tall → wide pivot**:

- **wide** source (one row per item, N attribute columns)
- **tall** intermediate (one row per *item × attribute group*)
- **wide** output (each group-row spreads its segment values across the positional `ATTRIBUTE_*` slots)

Two source attributes in the *same* group land on the *same* output row in *different* slots; two attributes in *different* groups produce *two* rows for the same item. None of the current mapping/generation machinery expresses "group + segment position + datatype," so this is genuinely new capability, not a config tweak.

## 3. The hard dependency: EFF configuration metadata

The single blocking prerequisite is the **EFF setup definition** — the list of attribute groups configured on the Item, and for each group its segments with (name, sequence, datatype, value set). The mapping document labels everything `"EFF: Custom Item Extensible Flex Fields"` but does **not** specify group codes or segment positions, so we cannot place values into the correct `ATTRIBUTE_*` slots from the mapping doc alone.

This metadata has to come from the client's Oracle instance, via one of:

- **Manage Extensible Flexfields → Item** export (the authoritative source: context/group codes, segment codes, sequences, datatypes, value sets), or
- a **populated EFF gold file** (`EGO_ITEM_INTF_EFF_B` filled for a few items) that we reverse-engineer the group/segment layout from, or
- an analyst-maintained **segment map spreadsheet** (group code, segment code, position, datatype, source attribute) if neither of the above is available.

Recommendation: request the Manage-EFF export. Everything below assumes we can obtain one of these three.

## 4. Proposed design

### 4.1 Data model
Add an **EFF definition** store (per target object + org), holding attribute groups and their segments:

```
EffAttributeGroup { object, group_code, group_display, multi_row(bool) }
EffSegment        { group_code, segment_code, segment_display, position,
                    datatype(CHAR|NUMBER|DATE|TIMESTAMP), value_set, uom_name? }
```

Extend the mapping model so a source column can target an **EFF segment** instead of a native field: add `eff_group_code` + `eff_segment_code` to the mapping/learning records (nullable — a normal mapping leaves them blank). The existing `LearnedMapping` already carries `rule_config`, so this can ride there initially without a schema migration.

### 4.2 Ingestion of EFF metadata
A parser for the Manage-EFF export (or the gold-file reverse-engineer) that populates `EffAttributeGroup`/`EffSegment`, mirroring how we already parse FBDI templates and the LOV metadata. Idempotent startup seed, same pattern as `seed_item_field_mappings`.

### 4.3 Mapping UX
In Mapping Review, when a source column has no good standard-field target, offer an **"Map to Extensible Flexfield"** action: pick Attribute Group → pick Segment (positions/datatypes shown from the parsed definition). Store as an EFF mapping. The analyst document's ~540 EFF rows become the seed for this — once the group/segment layout is known we can auto-attach most of them (they already name the source attribute and sample value).

### 4.4 Generation
Extend `output_service` so that when the Item fan-out reaches `EGO_ITEM_INTF_EFF_B/_TL`, it runs the EFF pivot instead of the standard `reindex`:

1. Collect all EFF-mapped source columns and their (group, segment position, datatype).
2. Group by attribute group; for each item, emit one row per group that has any non-empty value.
3. Place each value in `ATTRIBUTE_<TYPE><position>` (and `_UOM_NAME` companion for numbers), fill the key columns (Item Number, Org Code, Source System, `Attribute Group Code`), plus the backbone control defaults.
4. Route translatable segments to `EGO_ITEM_INTF_EFF_TL`.

This slots cleanly into the backbone-suppression work already shipped: the EFF sheets light up precisely because real source columns are mapped into them.

### 4.5 Validation
Datatype coercion + reporting: values that don't fit the segment datatype (text in a NUMBER slot, unparseable dates) get flagged in the existing quality/error surface rather than silently written.

## 5. Phased plan

- **Phase 0 — Obtain metadata (blocking, client action):** get the Manage-EFF export or a populated EFF gold file. Nothing downstream can be verified without it.
- **Phase 1 — Metadata ingestion + model:** parse groups/segments, store them, extend mapping/learning to carry `eff_group_code`/`eff_segment_code`. ~2–3 days.
- **Phase 2 — Generation pivot:** EFF pivot into `EGO_ITEM_INTF_EFF_B` (single group first, then multi-group multi-row), key columns, control defaults, `_UOM_NAME`. ~3–4 days. Verifiable against the EFF gold file.
- **Phase 3 — Mapping UX + auto-seed:** the "Map to EFF" picker, and auto-attach the ~540 analyst EFF rows to their groups/segments. ~3–4 days.
- **Phase 4 — TL + validation:** translatable table, datatype coercion, error reporting. ~2 days.

MVP that would already produce a loadable EFF file for the demo = Phases 1 + 2 against one known attribute group.

## 6. Risks / open questions

- **Group/segment layout unknown** until the client provides the EFF setup — the whole feature is gated on it (Phase 0).
- **Multi-row correctness:** items with values across several groups must produce one clean row per group; empty groups must be suppressed (Oracle rejects empty EFF rows). Covered by the pivot logic but needs gold verification.
- **Value sets / LOVs on segments:** some EFF segments are LOV-constrained; those values must resolve through the existing value-mapping layer, same as coded standard fields.
- **Category vs EFF choice:** the analysts left a few attributes (e.g. Category) as "EFF *or* Catalog Category." Those need a business decision before mapping.
- **Cross-reference / trading-partner attributes** (manufacturer, MPN, SyteLine item) are *not* EFF — they belong on `EGP_TRADING_PARTNER_ITEMS_INTF` / cross-reference sheets and are a separate, smaller work item.

## 7. What's already in place (no further work)

- The 18-sheet Item template incl. `EGO_ITEM_INTF_EFF_B/_TL` is seeded.
- Backbone-only suppression means the EFF sheets stay headers-only until real source columns map into them, then populate — exactly the hook EFF generation needs.
- The 53 standard-field Item mappings from the analyst doc are seeded as auto-applying learnings, so the standard portion of the item load already works; EFF is the remaining, larger portion.
