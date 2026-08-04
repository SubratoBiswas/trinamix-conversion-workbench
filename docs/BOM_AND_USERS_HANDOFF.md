# Two requests started, 04-Aug — where each one stands

## 1. BOM FBDI/CSV column sequence — DATA DONE, NOT WIRED

`backend/app/data/bom_fbdi_column_order.json` is written and both analyst claims
are **verified against the workbook, not assumed**:

| tab | FBDI cols | CSV cols | identical | END column |
|---|---|---|---|---|
| EGP_STRUCTURES_INTERFACE | 74 | 74 | yes | none |
| EGP_COMPONENTS_INTERFACE | 103 | 103 | yes | none |
| EGP_SUB_COMPS_INTERFACE | 65 | 65 | yes | none |
| EGP_REF_DESGS_INTERFACE | 65 | 65 | yes | none |

The file carries `"append_end_column": false` and the four CSV file names
(`EgpStructuresInterface.csv` etc.) from the Summary tab.

### ⚠ It is inert until something reads it

This is the exact shape CODEBASE_GUIDE §7.1 warns about, and the reason
`customer_sheet_scope`, `blank_sheets` and SELF_LOOKUP each needed rescuing: data
that says something and no code that asks. **Do not consider this done.**

`services/supplier_fbdi_layout.py` is the consumer. It already holds two
vocabularies — `supplier_col_order()` / `_ORDER_FILE` and `customer_layout()` /
`_CUSTOMER_FILE` — plus `apply_customer_layout(..., with_end=...)`. BOM needs the
third, and the wiring must be asserted by a test that reads `output_service`,
not only by one that reads the JSON.

**The END flag is the part to get right.** The supplier package has always
appended an END record terminator; the analyst says BOM has none. Inheriting
supplier behaviour here adds a field Oracle does not expect on these four
interfaces. `with_end` already exists as a parameter — BOM must pass `False`.

**Why the order matters at all:** these are headerless CSVs. Column POSITION is
the only thing carrying meaning, so a list that is right about the names and
wrong about the order loads silently into the wrong fields. Verify the generated
BOM CSVs column-by-column against this JSON before anyone loads them.

## 2. Users and roles — NOT STARTED, deliberately

Requested: an Admin / Normal user split, with Normal users seeing only Home,
Conversion Workbench and Load Management.

Nothing has been written. This is security-relevant and I ran out of context to
do it properly; a half-built permission system is worse than none, because it
looks enforced.

### The one thing that must not be got wrong

**Hiding a nav link is not access control.** Every screen in the sidebar is
backed by an API route, and those routes currently take
`Depends(get_current_user)` with no role test. A Normal user who cannot see
"Gold Standards" can still call `/api/gold/...` if they know the path — and the
FBDI templates, learning library and audit trail all sit behind sections this
request wants restricted.

### Order of work

1. `role: str = "admin"` on `models/user.py`, defaulting existing users to admin
   so nobody is locked out by the migration.
2. A `require_admin` dependency beside `get_current_user`, applied to **every
   router outside the three allowed sections** — that is the actual control.
3. Frontend nav filtering, which is presentation only, and a 403 page for a
   Normal user who reaches a restricted route by URL.
4. A user-management screen: list, invite, set role. Creating accounts and
   setting passwords stays a human action.
5. Tests that walk the router modules and fail if a route outside the allowed
   sections lacks the admin dependency — the same AST-sweep shape as
   `test_one_dated_store_writes`, so a new unguarded route cannot be added
   quietly.

Sections a Normal user keeps: **Home**; **Conversion Workbench** (Clients,
Projects, Conversion Objects, Dataflows, Mapping Review, Recommendations, Output
Preview); **Load Management** (Migration Monitor, Load Runs, Error Traceback,
Dependency Graph). Everything else — Datasets, FBDI Library, AI Engine,
Governance — is admin-only, and that includes their APIs.
