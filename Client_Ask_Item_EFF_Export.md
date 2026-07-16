**Subject:** Request — Item Extensible Flexfield (EFF) configuration for the data migration

Hi [Name],

As we build the item master conversion into Oracle Fusion, we've reached the part of the item data that loads as **Extensible Flexfields (EFF)** rather than standard item columns. Based on the field-mapping document, this is the *majority* of the item attributes.

To place each attribute value into the correct interface column (`EGO_ITEM_INTF_EFF_B` / `_TL`), we need the **EFF configuration** from your instance — specifically, for every configured attribute group and segment: the group code, the segment, its position, its data type, and the exact interface column it maps to.

Could you or your Oracle admin provide **one** of the following (in order of preference):

**Option 1 (best) — Extensible Flexfield configuration export**
From *Setup and Maintenance → Manage Extensible Flexfields*, search the flexfield code **`EGO_ITEM_EFF`** (Item Extended Attributes). For each attribute group / context, we need its segments with:

- Attribute Group code and display name
- Segment code and display name
- Segment sequence / display order (position)
- Data type — Character / Number / Date / Timestamp
- **Interface (database) column** the segment maps to — e.g. `ATTRIBUTE_CHAR1`, `ATTRIBUTE_NUMBER3`, `ATTRIBUTE_DATE2`
- Value set name and allowed values, if the segment is a list of values
- Whether the segment is translatable
- Which item class(es) the group is associated with

A configuration export/CSV, or even a screenshot set of the segments per group, both work.

**Option 2 — Populated EFF interface sample**
A filled `EGO_ITEM_INTF_EFF_B` (and `EGO_ITEM_INTF_EFF_TL` if you use translatable attributes) for ~5–10 representative items — for example from a prior test/CV load — with the `Attribute Group Code` and `ATTRIBUTE_*` columns populated. We can reverse-engineer the group → segment → column layout from that.

**Option 3 — Segment map spreadsheet**
If neither is readily to hand, a simple sheet with one row per segment: *attribute group code | segment | position | data type | interface column | the source attribute it corresponds to.*

Without one of these we can generate the standard item columns, but we can't correctly place the extensible-flexfield attributes — which is the larger part of the item load. Happy to jump on a short call with your Oracle admin if that's the quickest path.

Thanks,
[Your name]
