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


def test_the_delete_dialog_says_the_datasets_are_kept():
    """The actual question at that dialog. Answering it there is worth more than
    answering it in a chat window a week earlier."""
    src = _page()
    i = src.index("window.confirm(")
    dialog = src[i:i + 1400]
    check("it says what is kept", "THIS KEEPS" in dialog)
    check("datasets by name", "datasets" in dialog.lower())
    check("and that they are untouched", "not touched" in dialog.lower())


def test_the_delete_dialog_says_the_mappings_go():
    """The half that is easy to leave out, and the expensive half. Mapping work
    on those conversions is deleted outright."""
    src = _page()
    i = src.index("window.confirm(")
    dialog = src[i:i + 1400]
    check("it says what is deleted", "THIS DELETES" in dialog)
    check("mappings are named", "mapping" in dialog.lower())
    check("and it still says it is final", "undone" in dialog.lower())


def test_the_dialog_matches_the_cascade_it_describes():
    """A dialog that drifts from the code is worse than no dialog: it is a
    promise. Every model the endpoint deletes must be named on screen, and
    nothing the endpoint spares may be listed as deleted.
    """
    code = _router_code()
    dialog = _page()
    dialog = dialog[dialog.index("window.confirm("):][:1400].lower()
    deletes, keeps = dialog.split("this keeps")[0], dialog.split("this keeps")[1]

    for model, word in (("MappingSuggestion", "mapping"),
                        ("TransformationRule", "transformation rule"),
                        ("Crosswalk", "crosswalk"),
                        ("ConvertedOutput", "output"),
                        ("LoadRun", "load run")):
        check(f"{model} is deleted by the endpoint", f"{model}.find" in code)
        check(f"and the dialog says so", word in deletes, f"missing: {word}")

    check("Dataset is not in the cascade", "Dataset" not in code)
    check("and the dialog promises that", "dataset" in keeps)
    check("SourceConnection is not in the cascade", "SourceConnection.find" not in code)
    check("and the dialog promises that", "source connection" in keeps)


def test_the_learning_library_survives_a_delete():
    """Worth stating on screen because it changes the decision: the mappings are
    gone from the conversion but the library keeps what was learned, so a rebuilt
    engagement is not starting from nothing."""
    code = _router_code()
    check("no learned mappings are deleted", "LearnedMapping" not in code)
    check("no dated store rows are deleted", "mapping_store" not in code)
    dialog = _page()
    dialog = dialog[dialog.index("window.confirm("):][:1400].lower()
    check("and the dialog says so", "learning library" in dialog)


def test_the_endpoint_still_reports_what_it_removed():
    code = _router_code()
    check("the count comes back", "conversions_deleted" in code)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall Projects page date/delete checks passed")
