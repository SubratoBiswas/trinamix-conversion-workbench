# NextPower — Validation & Cleansing Rules by Module

The rule set the Conversion Workbench applies at **Generate** (and on demand via the
Validation & Cleansing page). Rules are scoped by **FBDI object + client (NextPower)**.

**How rules are created:** *Extract* (auto‑derived from each FBDI template's field
metadata — required / max‑length / value‑set / numeric / date), *Upload* (an
xlsx/csv/json rules workbook), *Manual*, or *AI‑suggest*. Extracted counts below
reflect the metadata each Oracle template actually carries; where a template omits a
flag (e.g. HDL carries no length flags), that category is thin and is topped up by
Upload/Manual/AI.

**How rules run at Generate:** the merged, de‑duplicated frame is first **cleansed**
(universal trim + any custom cleansing rules) then **validated** (built‑in FBDI checks
+ custom validation rules); an issues report is attached and hard errors are flagged.

---

## Standard cleansing rules — all modules

| Rule | Applies to | Effect |
|---|---|---|
| **TRIM** | every text field (universal, always on) | strips leading/trailing whitespace |
| **UPPERCASE** | code / ID / flag fields (e.g. Import Action, currency, country, Y/N flags) | normalises to the codes Oracle accepts |
| **DATE reformat** | date fields | rewrites to Oracle's `YYYY/MM/DD` (FBDI `%Y%m%d`) |
| **DEFAULT_IF_BLANK** | control fields (Import Action → CREATE, Batch ID, org/BU defaults) | fills a safe constant when the source is blank |
| **REMOVE_SPECIAL** | key/number fields (tax id, account no.) | drops punctuation that breaks matching |
| **Null‑sentinel cleanse** | all fields | literal `NULL`/`N/A`/`NONE` → empty |

Cleansing is applied first so validation runs on cleaned values.

---

## Validation rules by module

Rule types: **REQUIRED** (must be non‑blank), **VALUE_IN_SET** (must be an Oracle LOV
code), **MAX_LENGTH**, **NUMERIC**, **DATE**, plus custom **REGEX / NOT_NEGATIVE**.

### Supplier  (156 fields — 6 interfaces: Import, Address, Site, Site Assignment, Contacts, Banks)
- **REQUIRED (3 on the Import interface):** Import Action, Supplier Name, Business Relationship. *(Each child interface adds its own required keys, e.g. Address Name, Supplier Site, Procurement BU.)*
- **NUMERIC:** 1 (Supplier Number sequence).
- **Value‑set / max‑length:** not flagged in this template's metadata — add via Upload/AI‑suggest (e.g. Delivery Channel ∈ {EMAIL, FAX}, Invoice Match Option ∈ {Receipt, …}).
- Analyst‑confirmed rules already live for Supplier: Delivery Channel / Communication Method derivation, Enable B2B (Y/N), Invoice Match Option = Receipt, Fax parsing, Inactive Date.

### Item  (1,365 fields)
- **VALUE_IN_SET (51 LOV‑coded fields):** e.g. Asset Class, Make or Buy, Serial Number Control, Lot Control, Lot Expiration Control, Stock Locator Control, Planning Method, Forecast Control, Structure Item Type, Create Configured Item, Repair Program, Pack Type, and 39 more.
- **MAX_LENGTH:** 95 fields (mostly 18‑char code columns — Batch ID, Sales Account, ATP Rule, Accounting Rule, …).
- **NUMERIC:** 330 fields.  **DATE:** 82 fields.
- **REQUIRED:** not flagged in the template metadata (Item Import marks few hard‑required); enforce the key ones via Manual/AI (Item Number, Organization Code, Item Class, Template).

### Customer  (1,254 fields — 19 interface sheets)
- **REQUIRED (49 across the sheets):** Batch Identifier (on every sheet) plus the discriminators — Party Type, Identifying Address, Party Site Use Type, Account Address Set, Purpose, Account Relationship Set, Role Type, Contact Point Type, Relationship Type, Relationship Code, Insert/Update Indicator, Customer Account Source System + Reference, etc.
- **NUMERIC:** 29 fields.
- **Value‑set / max‑length:** add via Upload/AI (country, currency, account status LOVs).

### BOM / Item Structure  (307 fields)
- **NUMERIC:** 12 (quantities, sequence numbers, operation seq).
- **REQUIRED / LOV:** not flagged in metadata — enforce the essentials via Manual/AI (Transaction Type = SYNC, Structure Name = Primary, Organization Code, Component Item, Quantity).

### Employee HDL  (82 attributes)
- HDL is a pipe‑delimited `.dat` loader; the template carries no FBDI length/LOV flags, so validation here is best defined via **Upload/Manual/AI** (e.g. required: PersonNumber, EffectiveStartDate, LegalEmployerName; dates → HDL format; LOVs for worker type / assignment status).

---

## Summary counts

| Module | Fields | Required | Value‑set (LOV) | Max‑length | Numeric | Date |
|---|---|---|---|---|---|---|
| Supplier (Import) | 156 | 3 | 0* | 0* | 1 | 0 |
| Item | 1,365 | 0* | 51 | 95 | 330 | 82 |
| Customer | 1,254 | 49 | 0* | 0* | 29 | 0 |
| BOM / Item Structure | 307 | 0* | 0* | 0* | 12 | 0 |
| Employee HDL | 82 | 0* | 0* | 0* | 0 | 0 |

\* Not flagged in the current template metadata — these are the categories to top up
via **AI‑suggest / Upload / Manual** on the Validation & Cleansing page. Everything in
the "not 0" columns is auto‑extracted with one click per module.
