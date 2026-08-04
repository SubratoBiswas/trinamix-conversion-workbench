# START HERE — read this first in a new session

The analyst is **Subrato**. Repo: `C:\Users\SubratoBiswas\trinamix-conversion-workbench`
(connect that folder at session start — grants are per-session by design).

Say "read docs/START_HERE.md" and this is all the context you need to be useful.

---

## The working agreement — established over a long session, do not relitigate

**1. Write files STRAIGHT INTO the repo folder. Never hand over downloads.**
`SendUserFile` → then `mcp__remote-devices__device_commit_files` to the real path.
The analyst cannot download `.bat` files at all, and asked four times before this
stuck. A patch is only correct when the bridge is down.

**2. `launch_git.bat` in the repo root IS the deploy.** It is self-updating: it
runs from a copy in `%TEMP%` (it patches itself), verifies HEAD actually moved
before claiming success, and refuses to start if a manifested patch is missing.
When files are written directly, leave `EXPECT` empty — an empty patch set is a
valid deploy. Always update this file rather than describing deploy steps in chat.

**3. `COMMIT_MSG.txt` is the commit message.** Passed with `-F`, never `-m`
(cmd.exe caps a command line at 8,191 chars and the message has exceeded it).

**4. Run the full backend suite before delivering.** `cd backend && python -m
pytest tests -q`. Currently **1,066 passing, 14 skipped**. There is no JS runtime,
so frontend guarantees are asserted by reading source (`test_hook_order`,
`test_download_timeouts`) — and **the frontend cannot be typechecked from the
container**; say so when shipping .tsx.

**5. Move a test to its new address; never weaken it.** When behaviour changes
deliberately, rewrite what the test asserts and say why in the docstring.

**6. Comments break source-reading tests.** Three tests in one session failed
because the comment quoted the expression the test was counting. Strip comments
before counting.

---

## The architecture in four sentences

**One dated store** (`services/mapping_store.py`). Every statement about how a
field maps is a dated entry keyed **(client, source system, target field)**.
Newest wins; authorship is provenance only. Per-conversion mapping rows are a
*view*, never the truth. `docs/ONE_DATED_STORE.md` is the full plan.

**The write-time overlay** (`services/strategy_overlay.py`) is the *guarantee* —
learnings demonstrably did not reach the output, so constants, suppressions and
rules are enforced inside `_transform_frame` where nothing downstream can undo
them. It reads a list of dated JSON files (`_EXTRA_FILES`). To change a rule,
**add a newly-dated file** rather than editing an old one; that is how precedence
is expressed.

---

## The failure this codebase repeats, in both its forms

**Shipped and inert** (CODEBASE_GUIDE §7.1) — data that says something and code
that never asks. It has cost `customer_sheet_scope`, `blank_sheets`, SELF_LOOKUP
and every multi-column rule in the store. When you add data, add the test that
asserts the CALLER calls it.

**The screen and the file disagree, and the screen looks right.** The most
expensive shape of bug here. Recent instances: an approved constant overwritten by
a strategy default; "0 undecided" beside 342 groups; a blank panel that could not
say why. Whenever you fix one, ask what else reads that guard.

---

## Open, in priority order

1. **BOM column order is written and NOT WIRED** — `bom_fbdi_column_order.json`.
   See `docs/BOM_AND_USERS_HANDOFF.md` §1. Must pass `with_end=False`.
2. **Users and roles — not started.** Same doc §2. The trap is named there:
   hiding a nav link is not access control.
3. **Render dashboard, not code**: Static site → Redirects/Rewrites → `/*` →
   `/index.html` → **Rewrite**. Without it every refresh and shared deep link
   returns a bare 404. Verified against Render's docs that no file-based
   alternative exists.
4. **Delivery Method open question** — the 04-Aug rule assigns EMAIL in *both*
   branches because that is what was written, twice. The 28-Jul version said FAX.
   In `supplier_corrections_04aug.json` under `_open_question`.
5. **Customer 03-Aug open questions** — four of them, in
   `customer_mapping_03aug.json` under `_open_questions`. Phone Line Type
   contradicts itself; Identifying Address says "Yes" where the column takes Y/N.
6. **§13.x in `docs/SESSION_HANDOFF.md`** — older items, still open.

---

## Verifying live

The app is on Render: backend `trinamix-conversion-backend.onrender.com`,
frontend `tx-conversion-workbench.onrender.com`. `/api/health` reports the running
commit — compare it to the pushed SHA rather than guessing. **Do not trust a
cached `/api/health`**, and do not trust `/openapi.json` for "is this endpoint
deployed" — it is large enough to be truncated before the answer, which produced
one false negative already. Read the deployed source on disk instead.

If Claude-in-Chrome is connected, drive the real logged-in browser to verify.
Never click "Merge all high-confidence" or anything else irreversible on real
data — that is the analyst's decision.
