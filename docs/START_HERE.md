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
pytest tests -q`. Currently **1,239 passing, 14 skipped** — all 14 skips want a
reachable MongoDB or an unopenable workbook, and none of them is a frontend skip.

Stage `frontend/src` as well as `backend/`, not just `backend/`. Roughly 27 of
these tests read .tsx files, and several others skip themselves with
`if not _FE.exists()`. A backend-only tree therefore reports failures that are
not real AND hides tests that never ran — both directions of wrong, from the
same omission. A tarball dropped in `_parser_probe/` (gitignored) is the cheap
way to get the tree across; exclude `backend/venv` or it will not finish.

**4a. The frontend CAN be checked from the container — this changed 05-Aug.**
The old note here said it could not. `npm ci` in `frontend/` works (the registry
is reachable), and then:

* `npx vite build` — what Render actually runs, and the guarantee that matters.
  Passed 05-Aug.
* `npx tsc --noEmit` — **146 pre-existing errors, and that is the baseline.**
  Do not report it as a regression and do not fix it in passing. Measure before
  and after, and compare the COUNT and the FILE LIST; your change is clean when
  both are unchanged. Most of the 146 are one cause — `types/index.ts` declares
  `User.id: number` while the API returns a string — plus a few missing exports
  from `@/types`.
* `esbuild <file> --outfile=/dev/null` — a two-second syntax check on one file.

Vite strips types without checking them, so a type error never stops a deploy.
That is why the backlog exists and why it is not urgent — and also why `tsc` will
never tell you your own file is wrong unless you diff the count.

**5. Move a test to its new address; never weaken it.** When behaviour changes
deliberately, rewrite what the test asserts and say why in the docstring.
`test_delivery_method.py` on 05-Aug is the worked example: EMAIL became FAX, and
the docstring carries the evidence rather than the change happening silently.

**6. Comments break source-reading tests.** Three tests in one session failed
because the comment quoted the expression the test was counting. Strip comments
before counting. It bites in JSON too: on 05-Aug a `_reconciled` note that quoted
a bad column name broke the test looking for that name in the file. Search the
DATA (`doc["sheets"]`), never the whole document.

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
is expressed. `supplier_corrections_05aug.json` is the current worked example.

---

## The failure this codebase repeats, in all three of its forms

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
returned first. Now one rule in `services/mapping_dedupe.py`, with a sweep test
that fails if a second status-priority table appears anywhere in `app/`.

**Nothing ever called it.** Added 05-Aug, because it cost a total outage.
`generate_output_artifact` grew an advisory `log.info` that read `obj_name` sixty
lines before the name was assigned. A name assigned anywhere in a Python function
is local for the whole of it, so that read raised `UnboundLocalError` before a
single byte was written — every format, every object, every conversion, from
04-Aug 15:51 until it was found on 05-Aug. **1,182 tests were green over it**,
because not one of them called the function.

Two guards now. `test_bom_produced_file.py` builds a whole conversion in an
in-process Mongo (`mongomock-motor`, now in requirements.txt) and GENERATES a
real package — if generation cannot run at all, the suite says so.
`test_use_before_assignment.py` sweeps `app/` for the same shape, flagging only
the certain case: a name assigned exactly once, at the function's own body level,
and read at an earlier line.

**When you find one of these, the fix is one implementation, not a corrected
copy.** Six copies is how a rule that was fixed once stayed broken five times.

---

## Open, in priority order

1. **Two things only the Render dashboard can do, and one is a security fix.**
   * **`JWT_SECRET` on the backend service.** The manifest used to set
     `SECRET_KEY`, which nothing reads — `Settings` is `extra="ignore"`, so the
     generated value was discarded and `JWT_SECRET` fell back to
     `"trinamix-local-dev-secret-change-me"`, which is in this repository.
     Anyone who can read the repo can mint a token the live backend accepts.
     `generateValue` only fires on the deploy that CREATES a variable, so the
     rename in `render.yaml` does not repair an existing service. Setting it
     signs everyone out, which is the correct outcome. SESSION_HANDOFF §13.3a.
   * **Static site → Redirects/Rewrites → `/*` → `/index.html` → Rewrite.**
     Without it every refresh and shared deep link 404s. Measured 05-Aug:
     `tx-conversion-workbench.onrender.com/` serves the app and `/clients`
     returns 404. `render.yaml` now names the real services and says in its own
     header that it does not govern them.
2. **Item Import shows 0 converted rows** (§13.2) — uninvestigated, and cheaper
   to chase than it was: `test_bom_produced_file.py` shows how to build a whole
   conversion in-process and generate it with no infrastructure. Point the same
   harness at an Item template and a SyteLine-shaped extract.
3. **The frontend type backlog** — 146 `tsc` errors, mostly the one `User.id`
   cause. Not urgent, because Vite does not typecheck, but it makes
   `npm run lint` useless as a gate, so a real regression would hide in the noise.
4. **§13.4–§13.6 in `docs/SESSION_HANDOFF.md`** — the `derived` flag
   under-reports; ONE_DATED_STORE step 5 (delete the copy paths); and the plan's
   own verification, still never run.
5. **Four customer questions remain open** in `customer_mapping_03aug.json` under
   `_open_questions` — Party Number generation, the BILL_TO/SHIP_TO source-sheet
   naming, the 216 unanswered source columns, and the `externalid` DFF. Two others
   were closed on 05-Aug and moved to `_resolved_questions` with their evidence.

**Closed 05-Aug, do not reopen:** the user-management screen, the BOM
produced-file check, `render.yaml`'s service names, the Delivery Method question,
two of the six customer questions, and §13.1a / §13.1b. §13.1b's item 1 was
deliberately NOT done — see immediately below.

---

## Two things that look wrong and are not

**`customer_fbdi_column_order.json` is not replaced by the V2.3 extraction, on
purpose.** §13.1b asked for the swap, and read as open for two days because the
live file named `V2 2.xlsx` while the V2.3 extraction sat unused in
`docs/incoming/`. Nobody had compared them. Diffed 05-Aug: same 15 interfaces in
the same order, same 15 CSV names, 996 + 996 columns, same three reorderings.
One difference, and it runs the wrong way — the extraction's last `csv_order`
entry for RA_CUSTOMER_PROFILES_INT_ALL reads `"Review Before Consolidated
Billing,"` with a trailing comma, and Oracle's own bundled workbook has none.
The live file is re-dated to 2026-08-03 and carries a `_reconciled` block;
`test_customer_spec_reconciled.py` re-runs the diff and reads Oracle's template
rather than trusting the note.

**The 04-Aug Delivery Method file still says EMAIL in both branches.** It is the
record of what was decided that day, and precedence here is expressed by date and
never by rewriting history. `supplier_corrections_05aug.json` supersedes it, and
a test asserts the 04-Aug file was not edited to make the new answer true.

---

## The third vocabulary, and the one that is not data

`supplier_fbdi_layout.py` now holds three: supplier, customer and BOM. They
disagree on purpose — customer needs a worksheet/CSV switch because three of its
fifteen interfaces reorder; BOM must not have one because all four of its tabs
agree; supplier appends `END` and BOM must not inherit it. `_reorder_to` is the
one piece they share. As of 05-Aug the BOM half is checked against a PRODUCED
file and against Oracle's own template, not only against itself.

`services/access_control.py` is deliberately NOT that shape. Every spec here is
data read through an accessor that degrades to a no-op when the file is missing,
which is right for a column order and catastrophic for an access control, because
its degraded state is "everybody is an administrator". It is a Python module with
no file to lose, and a test asserts it never grows one. Routers mount through
`access_control.mount(section=...)`, keyword-only with no default, so an
unclassified router is a startup crash rather than a public endpoint.

**Users and roles are complete as of 05-Aug.** `/api/users` — list, invite, set
role — mounted under `ADMIN`, with a Users screen under a new admin-only
"Administration" nav group. Inviting creates the account and its role but no
password: the record carries `auth_service.NO_PASSWORD` and cannot sign in until
a human sets one, which `verify_password` refuses BEFORE it reaches passlib, so
the guarantee does not rest on the library raising. Two lockouts are refused by
the API — demoting the last administrator, and changing your own role — and both
count administrators with `access_control.is_admin`, so the set this screen
protects and the set the guard admits cannot disagree about a padded or shouted
role string. Role changes take effect on the next REQUEST, not the next sign-in.

---

## Verifying live

The app is on Render: backend `trinamix-conversion-backend.onrender.com`,
frontend `tx-conversion-workbench.onrender.com` (there is no second frontend —
`trinamix-conversion-workbench.onrender.com` 404s at the root). `/api/health`
reports the running commit — compare it to the pushed SHA rather than guessing.
**Do not trust a cached `/api/health`**, and do not trust `/openapi.json` for
"is this endpoint deployed" — it is large enough to be truncated before the
answer, which produced one false negative already. Read the deployed source on
disk instead.

If Claude-in-Chrome is connected, drive the real logged-in browser to verify.
Never click "Merge all high-confidence" or anything else irreversible on real
data — that is the analyst's decision.
