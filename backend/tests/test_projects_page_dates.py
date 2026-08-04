"""The Projects list must show when an engagement was created, and its delete
confirmation must say what survives.

Two separate things, one screen:

1. With 40+ engagements on screen, most of them named for a test run, the NAME
   cannot tell an old one from a current one. The card carried a go-live date
   that is almost always blank and never carried the created date, which is the
   one that would have answered it. ``created_at`` was already on ProjectOut and
   already on the frontend Project type — the card simply never read it. That is
   the inert-data pattern in its mildest form.

2. Deleting an engagement cascades into conversions, mappings, transformation
   rules, crosswalks, output records and load history. It does NOT touch
   datasets, templates or source connections. The confirmation said only "this
   cannot be undone", which is true and answers nothing — the question anybody
   actually has at that dialog is whether their uploaded data goes with it.

There is no JS runtime here, so this reads the source, like test_hook_order and
test_download_timeouts. Comments are stripped first.
"""
import io
import os
import re
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_BACKEND = Path(__file__).resolve().parent.parent
_PAGE = _BACKEND.parent / "frontend" / "src" / "pages" / "ProjectsPage.tsx"
_ROUTER = _BACKEND / "app" / "routers" / "projects.py"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _strip_ts_comments(src: str) -> str:
    """Drop // and /* */ without touching strings or template literals."""
    out, i, n, quote = [], 0, len(src), None
    while i < n:
        c, nxt = src[i], src[i + 1] if i + 1 < n else ""
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(nxt); i += 2; continue
            if c == quote:
                quote = None
            i += 1; continue
        if c in "\"'`":
            quote = c; out.append(c); i += 1; continue
        if c == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2; continue
        out.append(c); i += 1
    return "".join(out)


def _page() -> str:
    return _strip_ts_comments(_PAGE.read_text(encoding="utf-8"))


def _router_code() -> str:
    """delete_project's source with Python comments blanked by token position."""
    lines = _ROUTER.read_text(encoding="utf-8").splitlines()
    src = "\n".join(lines) + "\n"
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        (srow, scol), (_, ecol) = tok.start, tok.end
        ln = lines[srow - 1]
        lines[srow - 1] = ln[:scol] + " " * (ecol - scol) + ln[ecol:]
    joined = "\n".join(lines)
    i = joined.index("async def delete_project")
    return joined[i:joined.index("\n@router", i)]


def test_the_card_shows_when_the_engagement_was_created():
    src = _page()
    check("the card reads created_at", "project.created_at" in src)
    check("it is labelled", "Created:" in src)


def test_the_created_date_is_a_day_not_a_timestamp():
    """formatDate carries hours and minutes. On a card being scanned rather than
    read, a clock is noise — and 41 of them is a wall of digits."""
    src = _page()
    check("a day-only formatter exists", "const formatDay" in src)
    check("it asks for no clock", "toLocaleDateString" in src)
    i = src.index("Created:")
    window = src[max(0, i - 220):i + 60]
    check("the card uses it", "formatDay(project.created_at)" in window,
          "still formatting the created date with the timestamp helper")


def test_the_list_can_be_put_in_oldest_first_order():
    """The API returns newest first. When the job is clearing out old
    engagements, the ones you want were at the bottom of forty-one."""
    src = _page()
    check("a sort toggle exists", "oldestFirst" in src)
    check("it sorts on the created date", "_created(a)" in src and "_created(b)" in src)
    check("and the sorted list is what renders", "(sorted ?? items).map" in src,
          "the grid is still mapping the unsorted list")


def test_sorting_does_not_mutate_the_loaded_list():
    """Array.sort sorts in place. Sorting `items` directly would reorder state
    behind React's back and make the toggle stick after one press."""
    src = _page()
    check("it sorts a copy", "[...items].sort(" in src)


def _dialog() -> str:
    src = _page()
    return src[src.index("window.confirm("):][:1600].lower()


def test_the_delete_dialog_says_the_datasets_go_now():
    """MOVED, not weakened. This used to assert the opposite — that datasets were
    kept — because they were. The analyst asked for a delete to take its data
    with it, so the endpoint changed and the assertion moved with it. Recorded
    here rather than quietly rewritten: the old promise was on screen, and
    anybody who read it then would be wrong now.
    """
    d = _dialog()
    deletes = d.split("this keeps")[0]
    check("datasets are named as deleted", "dataset" in deletes)
    check("and the files behind them", "uploaded files" in deletes)
    check("it still says it is final", "undone" in d)


def test_the_dialog_warns_that_a_shared_dataset_is_kept():
    """The exception is the whole risk. Uploads are deduped by content hash, so
    the same file re-uploaded REUSES the row — a dataset can be shared, and
    deleting it blind pulls the source out from under an engagement nobody
    touched."""
    d = _dialog()
    check("the exception is stated", "except" in d)
    check("and it names the reason", "another engagement" in d)


def test_the_dialog_separates_the_rows_from_the_logic():
    """The distinction the whole design rests on. The mapping ROWS are a view of
    the dated store and go with their conversions. The LOGIC — column A of this
    source, for this module, maps to column B of the FBDI — is a statement in the
    library, keyed by client and source system, and is not stored on the
    conversion at all. A dialog that blurred the two would make people keep rows
    they do not need or lose logic they do."""
    d = _dialog()
    deletes, keeps = d.split("this keeps the mapping logic")
    check("the rows are named as deleted", "mapping rows" in deletes)
    check("with their rules and crosswalks",
          "transformation rules" in deletes and "crosswalk" in deletes)
    check("the logic is named as kept", "learning library" in keeps)
    check("and how it is keyed", "source system" in keeps)
    check("in the analyst's own terms", "column a" in keeps and "column b" in keeps)


def test_the_dialog_matches_the_cascade_it_describes():
    """A dialog that drifts from the code is worse than no dialog: it is a
    promise. Every model the endpoint deletes must be named on screen, and
    nothing the endpoint preserves may be listed as deleted.
    """
    code = _router_code()
    d = _dialog()
    deletes, keeps = d.split("this keeps")[0], d.split("this keeps")[1]

    for model, word in (("ConvertedOutput", "output"),
                        ("LoadRun", "load run"),
                        ("Dataset", "dataset")):
        check(f"{model} is deleted by the endpoint", f"{model}" in code)
        check(f"and the dialog says so", word in deletes, f"missing: {word}")

    # The row deletions moved into a labelled loop so one failure cannot make the
    # engagement undeletable. The assertion moves with the structure: each model
    # must appear in that loop, and the loop must actually delete.
    loop = code[code.index("for _label, _op in ("):]
    loop = loop[:loop.index("await Conversion.find")]
    check("the loop deletes", "await _op.delete()" in loop)
    for model, word in (("MappingSuggestion", "mapping rows"),
                        ("TransformationRule", "transformation rules"),
                        ("Crosswalk", "crosswalk")):
        check(f"{model} is deleted by the endpoint", f"{model}.find(" in loop)
        check("and the dialog says so", word in deletes, f"missing: {word}")

    check("SourceConnection is not in the cascade", "SourceConnection.find" not in code)
    check("and the dialog promises that", "source connection" in keeps)


def _delete_call_for(code: str, model: str) -> str:
    """The statement operating on ``model``, so a .delete() elsewhere in the
    cascade cannot be mistaken for this model's."""
    i = code.find(f"{model}.find")
    return code[i:code.find("\n", i)] if i >= 0 else ""


def test_the_logic_is_captured_before_the_rows_are_deleted():
    """Order is the whole guarantee. Every deliberate edit already records a
    learning as it is made, and generation captures again — but "already" is an
    assumption, and after the delete statement the rows do not exist to be asked.
    """
    code = _router_code()
    ci = code.index("capture_learnings_from_conversion(_c)")
    di = code.index("MappingSuggestion.find({\"conversion_id\"")
    check("capture runs", ci > 0)
    check("BEFORE the rows go", ci < di, "the rows are deleted before the capture")


def test_a_failed_capture_is_reported_and_never_blocks_the_delete():
    """Best-effort, but not silent. A capture that throws must not strand a half
    deleted project — and must not vanish either, because it means logic may not
    have reached the library before its rows went."""
    code = _router_code()
    check("failures are caught", "except Exception as _cap_exc" in code)
    check("and collected", "capture_errors.append" in code)
    check("and returned", '"capture_errors"' in code)
    check("with a count of what was kept", '"logic_captured"' in code)
    page = _page()
    check("the page warns about them", "capture_errors" in page)
    check("in words", "could not be captured" in page.lower())


def test_no_archive_fields_were_left_behind():
    """A briefly-considered design stamped the surviving rows with orphaned_at.
    The rows are deleted now, so those fields would be dead weight on the model —
    exactly the shipped-and-inert shape, in the data layer."""
    model = (_BACKEND / "app" / "models" / "mapping.py").read_text(encoding="utf-8")
    check("no orphan stamp on the model", "orphaned_at" not in model)
    check("nor in the endpoint", "orphaned_at" not in _router_code())


def test_a_dataset_another_engagement_uses_is_skipped():
    """The orphan rule. The endpoint must look for OTHER conversions before
    deleting anything, and must exclude the ones it is about to delete — checking
    after the fact would find nothing and delete everything."""
    code = _router_code()
    check("it looks for other users", "still_used" in code)
    check("excluding the doomed conversions", '"$nin": conv_ids' in code)
    check("a shared dataset is skipped", "datasets_kept.append" in code)
    check("and the skip is reported", '"datasets_kept"' in code)


def test_the_skipped_datasets_reach_the_screen():
    """A delete that quietly did less than the dialog promised is the screen and
    the truth disagreeing again, just in the reassuring direction. The kept list
    has to be shown, not only returned."""
    src = _page()
    check("the response is read", "datasets_kept" in src)
    check("and surfaced", "still_used_by" in src)


def test_the_uploaded_file_is_removed_from_disk():
    """Deleting the row and leaving the file is how a disk fills up with data
    somebody believes they deleted."""
    code = _router_code()
    check("the file is removed", "os.remove(ds.file_path)" in code)
    check("and its column profiles", "DatasetColumnProfile" in code)


def test_the_learning_library_survives_a_delete():
    """Worth stating on screen because it changes the decision: a rebuilt
    engagement is not starting from nothing."""
    code = _router_code()
    check("no learned mappings are deleted", "LearnedMapping" not in code)
    check("no dated store rows are deleted", "mapping_store" not in code)
    check("and the dialog says so", "learning library" in _dialog())


def test_the_endpoint_still_reports_what_it_removed():
    code = _router_code()
    check("the count comes back", "conversions_deleted" in code)


def test_the_capture_cannot_hang_the_delete():
    """The bug this caused in production. Capture across ~1200 fields is hundreds
    of Mongo upserts — output_service skips it above 300 fields and says in as
    many words that it "is what made the request hang". An unbounded loop of it
    over every conversion did exactly that: small engagements deleted, the 6- and
    17-conversion ones timed out at the gateway, and the browser reported a
    generic failure for a backstop nobody had asked to wait for.
    """
    code = _router_code()
    check("there is a deadline", "_CAPTURE_BUDGET_S" in code)
    check("and a per-conversion cap", "_PER_CONVERSION_S" in code)
    check("the call is bounded", "wait_for(" in code)
    check("a timeout is caught", "TimeoutError" in code)
    check("and reported, not swallowed", "timed out" in code)
    check("what did not fit is named", "capture budget spent" in code)


def test_housekeeping_failures_do_not_make_an_engagement_undeletable():
    """A side task that throws must not leave the project on screen forever. The
    engagement goes; what failed comes back in warnings."""
    code = _router_code()
    check("failures are collected", "warnings.append" in code)
    check("and returned", '"warnings": warnings' in code)
    check("datasets are handled one at a time", "dataset {d}:" in code)
    page = _page()
    check("and the page shows them", "res?.warnings" in page)


def test_a_failed_delete_says_why():
    """"Failed to delete engagement." is the blank panel that could not explain
    itself — it cannot tell a timeout from a permission problem from a bad row, so
    the only next step it leaves anyone is to press the same button again.
    """
    page = _page()
    i = page.index("Could not delete")
    body = page[i:i + 900]
    check("the server detail is shown", "response?.data?.detail" in _page())
    check("with the status code", "HTTP ${status}" in body)
    check("403 is explained", "403" in body)
    check("a timeout is distinguished from a refusal", "ran out of time" in body)
    check("and says a retry is safe", "idempotent" in body)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall Projects page date/delete checks passed")
