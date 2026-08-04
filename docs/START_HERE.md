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

**2. `launch_git.bat` in the repo root IS the deploy — update it on EVERY
deploy, unprompted.** It is self-updating: it runs from a copy in `%TEMP%` (it
patches itself), verifies HEAD actually moved before claiming success, and
refuses to start if a manifested patch is missing. When files are written
directly, leave `EXPECT` empty — an empty patch set is a valid deploy.

Rewrite `DEPLOY_NOTE` every time to name what this deploy ships and the measured
suite result. That is not optional tidiness and it is not conditional on there
being patches: `EXPECT` going empty removed the one line that said what a run was
shipping, so every deploy started looking identical to every other deploy, and a
green run that shipped the wrong change is what this script exists to prevent.
The analyst has asked for this twice. Never describe deploy steps in chat instead.

**3. `COMMIT_MSG.txt` is the commit message.** Passed with `-F`, never `-m`
(cmd.exe caps a command line at 8,191 chars and the message has exceeded it).

**4. Run the full backend suite before delivering.** `cd backend && python -m
pytest tests -q`. Currently **1,111 passing, 14 skipped**. There is no JS runtime,
so frontend guarantees are asserted by reading source (`test_hook_order`,
`test_download_timeouts`) — and **the frontend cannot be typechecked from the
container**; say so when shipping .tsx.

Stage `frontend/src` as well as `backend/`, not just `backend/`. Roughly 27 of
these tests read .tsx files, and several others skip themselves with
`if not _FE.exists()`. A backend-only tree therefore reports failures that are
not real AND hides tests that never ran — both directions of wrong, from the
same omission. A tarball dropped in `_parser_probe/` (gitignored) is the cheap
way to get the tree across.

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

The worst one so far, 04-Aug: **mapping edits reached Mapping Review and not the
generated FBDI**, on every track at once. `collapse_mapping_dupes` — status, then
a row carrying a source column, then freshest — had exactly ONE caller, the
endpoint the screen reads. Generation carried its own copy in six places that
compared status alone with a strict `>`, so a tie went to whichever row Mongo
returned first. Saving an edit does not move a row out of `suggested`, so the
analyst's row and an empty auto-map twin tied, and the two sides broke the tie
differently. Now one rule in `services/mapping_dedupe.py`, with a sweep test that
fails if a second status-priority table appears anywhere in `app/`.

`updated_at` on `MappingSuggestion` was also never maintained — only the
auto-mapper set it — so "freshest" pointed at the machine's guess rather than the
person's decision. Every write goes through `stamp_edit` now.

**When you find one of these, the fix is one implementation, not a corrected
copy.** Six copies is how a rule that was fixed once stayed broken five times.

---

## Open, in priority order

1. **User-management screen — the last piece of users and roles.** List, invite,
   set role. Enforcement is done and tested (`services/access_control.py`,
   `test_role_access.py`, 24 tests); what is missing is any way to MAKE somebody
   a Normal user other than editing Mongo by hand. Every existing account is an
   administrator, so nothing has changed for anyone yet — safe, but unfinished.
   Creating accounts and setting passwords stays a human action.
2. **BOM CSVs have never been compared against a real conversion.** The layout
   function and its caller are tested; a produced file is not. `docs/
   BOM_AND_USERS_HANDOFF.md` §1 asks for a column-by-column check before anyone
   loads one.
3. **Render dashboard, not code**: Static site → Redirects/Rewrites → `/*` →
   `/index.html` → **Rewrite**. Without it every refresh and shared deep link
   returns a bare 404. Verified against Render's docs that no file-based
   alternative exists. Note `render.yaml` carries the rule but is bound to
   services named `trinamix-backend`/`trinamix-frontend`, while the live sites
   are `tx-conversion-workbench` and `trinamix-conversion-workbench` — so the
   manifest never governed them and reads as though this is handled.
4. **Delivery Method open question** — the 04-Aug rule assigns EMAIL in *both*
   branches because that is what was written, twice. The 28-Jul version said FAX.
   In `supplier_corrections_04aug.json` under `_open_question`.
5. **Customer 03-Aug open questions** — four of them, in
   `customer_mapping_03aug.json` under `_open_questions`. Phone Line Type
   contradicts itself; Identifying Address says "Yes" where the column takes Y/N.
6. **§13.x in `docs/SESSION_HANDOFF.md`** — older items, still open. Two of them
   read as open and look already done: §13.1a (drop `Third_Party_Pay_Relationships`)
   is wired via `_strategy_sheets_to_drop` with `test_dropped_sheets.py`, and
   §13.1b's live `customer_fbdi_column_order.json` already carries 15 sheets, 996
   columns and every CSV name — but names **V2_2** as its source while
   `docs/incoming/customer_fbdi_column_order_V2.json` (the V2.3 extraction) sits
   unused. Diff them and either close the entry or re-date the spec; do not redo it.

---

## The third vocabulary, and the one that is not data

`supplier_fbdi_layout.py` now holds three: supplier, customer and BOM. They
disagree on purpose — customer needs a worksheet/CSV switch because three of its
fifteen interfaces reorder; BOM must not have one because all four of its tabs
agree; supplier appends `END` and BOM must not inherit it. `_reorder_to` is the
one piece they share.

`services/access_control.py` is deliberately NOT that shape. Every spec here is
data read through an accessor that degrades to a no-op when the file is missing,
which is right for a column order and catastrophic for an access control, because
its degraded state is "everybody is an administrator". It is a Python module with
no file to lose, and a test asserts it never grows one. Routers mount through
`access_control.mount(section=...)`, keyword-only with no default, so an
unclassified router is a startup crash rather than a public endpoint.

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
