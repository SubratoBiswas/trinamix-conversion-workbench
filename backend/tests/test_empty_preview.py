"""A blank Converted Data panel that will not say why.

Reported live: "Syteline source -> Item Import", Converted Data badge 0, the
column headers rendering perfectly (TRANSACTION TYPE, BATCH ID, ORGANIZATION
CODE, SOURCE SYSTEM CODE …), Lineage 260, Cleansing 18. Nothing under the header.
The analyst's question was "why is there nothing for this?" and the screen had no
answer anywhere on it.

THREE DEFECTS, all in that one panel.

1. Zero rows WITH columns fell through to the table. `renderData` only handled
   `columns.length === 0`, so a frame with 260 columns and no rows drew a header
   row over an empty body. A blank panel is indistinguishable from a failed load,
   from a page still working, and from a conversion that genuinely produced
   nothing — and those need different responses.

2. The badge was not counting what it said. `total_rows` returned the SOURCE
   dataset's row count wherever it could resolve one, under a label reading
   "Converted Data". The two agree on a healthy 1:1 conversion and diverge exactly
   when something has gone wrong, which is the only time anybody reads them.

3. It read `conversion.dataset_id` — the singular, legacy field — so a
   multi-source conversion never resolved it at all and fell back to the length of
   a 50-row preview. The badge would then report 50 for a 12,000-row extract.

The cause is NAMED now, and the naming is derived rather than guessed: each branch
is read from the conversion, and the last one admits it does not know instead of
inventing something plausible.

Pure: hand-built frames and a stub conversion. No database.
"""
import asyncio
from pathlib import Path

import pandas as pd

from app.services.output_service import _why_no_rows

_BACKEND = Path(__file__).resolve().parent.parent


class _Conv:
    def __init__(self, cid="c1"):
        self.id = cid
        self.source_dataset_ids = []
        self.dataset_id = None


EMPTY = pd.DataFrame()
WITH_COLS = pd.DataFrame({"Transaction Type": [], "Batch ID": [],
                          "Organization Code": []})


def why(df, source_rows, decisions=None, monkeypatch=None):
    if decisions is not None:
        import app.services.decision_service as ds

        async def _load(_cid, scope="duplicate"):
            return decisions
        monkeypatch.setattr(ds, "load_decisions", _load)
    return asyncio.run(_why_no_rows(_Conv(), df, source_rows))


# ── Each cause is named, and they are distinguishable ────────────────────────

def test_no_columns_at_all_says_nothing_is_mapped():
    r = why(EMPTY, 260)
    assert r["cause"] == "nothing_mapped"
    assert "Mapping Review" in r["detail"]


def test_columns_but_an_empty_source_says_so_and_explains_the_headers():
    """The headers are the giveaway that misleads: they come from the Oracle
    template, not the extract, so a wholly empty source still draws a full-width
    table."""
    r = why(WITH_COLS, 0)
    assert r["cause"] == "source_has_no_rows"
    assert "template" in r["detail"]


def test_a_source_with_rows_and_no_output_is_reported_as_exactly_that():
    """The reported case. It must not be dressed up as an empty file — the
    extract had rows and they were lost between reading and writing."""
    r = why(WITH_COLS, 260)
    assert r["cause"] == "none_survived"
    assert "260" in r["headline"]


def test_the_last_branch_admits_what_it_does_not_know():
    """The one branch that cannot be derived. It lists the causes worth checking
    and does not pick one — a confident wrong cause costs more than no cause,
    because it sends somebody to the wrong screen."""
    r = why(WITH_COLS, 260)
    for hint in ("Duplicate", "Cleansing", "header", "Lineage"):
        assert hint in r["detail"], hint


def test_every_cause_is_a_distinct_value():
    causes = {why(EMPTY, 0)["cause"], why(WITH_COLS, 0)["cause"],
              why(WITH_COLS, 260)["cause"]}
    assert len(causes) == 3


def test_every_reason_carries_a_headline_and_a_detail():
    for df, n in ((EMPTY, 0), (WITH_COLS, 0), (WITH_COLS, 260)):
        r = why(df, n)
        assert r["headline"].strip() and r["detail"].strip()
        assert r["headline"].endswith(".")


# ── A recorded decision is not a fault ───────────────────────────────────────

def test_excluding_every_record_is_reported_as_a_decision_not_a_failure(monkeypatch):
    """An analyst who excluded everything gets an empty file on purpose. Calling
    that a defect sends them hunting for a bug they created deliberately."""
    r = why(WITH_COLS, 4, decisions=[
        {"verdict": "exclude", "member_keys": ["a", "b"]},
        {"verdict": "exclude", "member_keys": ["c", "d"]},
    ], monkeypatch=monkeypatch)
    assert r["cause"] == "excluded_by_decision"
    assert "not a fault" in r["detail"]
    assert "Duplicate suspects" in r["detail"]


def test_a_partial_exclusion_is_not_blamed_for_an_empty_frame(monkeypatch):
    """Two records excluded out of 260 did not empty the file, and saying so would
    close the investigation on the wrong answer."""
    r = why(WITH_COLS, 260, decisions=[{"verdict": "exclude", "member_keys": ["a", "b"]}],
            monkeypatch=monkeypatch)
    assert r["cause"] == "none_survived"


def test_a_merge_verdict_is_not_read_as_an_exclusion(monkeypatch):
    """Merge collapses a group to one row; it never empties the file."""
    r = why(WITH_COLS, 4, decisions=[
        {"verdict": "merge", "member_keys": ["a", "b", "c", "d"]}],
        monkeypatch=monkeypatch)
    assert r["cause"] == "none_survived"


def test_a_decision_lookup_that_fails_does_not_break_the_diagnosis(monkeypatch):
    """The diagnosis runs on a page that is already showing nothing. Throwing here
    would replace a blank panel with a stack trace."""
    import app.services.decision_service as ds

    async def _boom(_cid, scope="duplicate"):
        raise RuntimeError("no database")
    monkeypatch.setattr(ds, "load_decisions", _boom)
    assert why(WITH_COLS, 260)["cause"] == "none_survived"


# ── The counts ───────────────────────────────────────────────────────────────

_SRC = (_BACKEND / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
_BODY = _SRC.split("async def get_output_preview(")[1].split("\nasync def ")[0]


def test_the_source_total_reads_every_bound_dataset():
    """It read `conversion.dataset_id` alone — the singular legacy field — so a
    multi-source conversion fell through to the length of a 50-row preview and the
    badge reported 50 for a 12,000-row extract."""
    assert "conversion.source_dataset_ids" in _BODY
    assert "if conversion.dataset_id:" not in _BODY


def test_the_estimate_cannot_claim_rows_the_conversion_did_not_produce():
    """The whole defect in one line: the badge said "Converted Data" and reported
    the SOURCE count, so a conversion producing nothing could still show 260."""
    assert "total = source_rows if (converted and source_rows) else converted" in _BODY


def test_both_counts_are_reported_separately():
    """They agree on a healthy conversion and diverge exactly when something is
    wrong, so one number cannot carry both."""
    for key in ('"source_rows"', '"converted_rows"', '"preview_limit"'):
        assert key in _BODY, key


def test_the_reason_is_attached_only_when_the_frame_is_empty():
    """A reason on a healthy preview is noise, and would train people to ignore
    it on the one that matters."""
    assert "if converted == 0:" in _BODY
    assert 'out["empty_reason"] = await _why_no_rows(' in _BODY


def test_the_schema_carries_the_new_fields():
    src = (_BACKEND / "app" / "schemas" / "runtime.py").read_text(encoding="utf-8")
    body = src.split("class OutputPreviewOut(")[1].split("\nclass ")[0]
    for f in ("source_rows", "converted_rows", "preview_limit", "empty_reason"):
        assert f in body, f


# ── The panel renders it ─────────────────────────────────────────────────────

_PAGE = _BACKEND.parent / "frontend" / "src" / "pages" / "OutputPreviewPage.tsx"


def test_zero_rows_no_longer_falls_through_to_an_empty_table():
    """The defect: `columns.length === 0` was the ONLY empty case, so 260 columns
    and no rows drew a header over an empty body."""
    if not _PAGE.exists():
        return
    src = _PAGE.read_text(encoding="utf-8")
    assert "d.rows.length === 0 ?" in src
    body = src.split("const renderData = (d: OutputPreview) =>")[1][:2400]
    assert "empty_reason?.headline" in body
    assert "empty_reason?.detail" in body


def test_the_panel_shows_both_counts_so_the_gap_is_visible():
    """"Source 260 · Converted 0" is the whole diagnosis at a glance, and it is
    the comparison the badge alone could never show."""
    if not _PAGE.exists():
        return
    body = _PAGE.read_text(encoding="utf-8").split(
        "const renderData = (d: OutputPreview) =>")[1][:2400]
    assert "source_rows" in body and "converted_rows" in body


def test_the_panel_says_the_preview_is_bounded():
    """Otherwise "Converted 50" on a 12,000-row extract reads as data loss."""
    if not _PAGE.exists():
        return
    body = _PAGE.read_text(encoding="utf-8").split(
        "const renderData = (d: OutputPreview) =>")[1][:2400]
    assert "preview_limit" in body


def test_there_is_a_fallback_when_the_backend_names_no_cause():
    """An older backend, or a cause added later, must not render as a blank panel
    again — which is the exact failure being fixed."""
    if not _PAGE.exists():
        return
    body = _PAGE.read_text(encoding="utf-8").split(
        "const renderData = (d: OutputPreview) =>")[1][:2400]
    assert "?? \"The conversion produced no rows.\"" in body


# ── The source file, back out of the tool ────────────────────────────────────
#
# "How to download source files?" — you could not. Everything downloadable was
# output-side (the FBDI bundle, the run report, the mapping export, the duplicate
# list), so the one artefact that settles a disagreement between the tool and the
# data was the one artefact nobody could open. On a card reading "0 rows · 165
# cols" against a conversion producing nothing, opening the workbook IS the
# investigation.

_DS_ROUTER = (_BACKEND / "app" / "routers" / "datasets.py").read_text(encoding="utf-8")


def test_a_dataset_can_be_downloaded():
    assert '@router.get("/{dataset_id}/download")' in _DS_ROUTER
    body = _DS_ROUTER.split("async def download_dataset(")[1].split("\n@")[0]
    assert "FileResponse(" in body


def test_it_serves_the_bytes_generation_reads_not_a_second_copy():
    """Rehydrated from GridFS through the SAME helper generation uses. A separate
    read could hand back a file the conversion is not looking at, which in an
    investigation is worse than no file at all — the disk is ephemeral and a
    dataset older than one deploy has no local copy."""
    body = _DS_ROUTER.split("async def download_dataset(")[1].split("\n@")[0]
    assert "materialize_dataset_file(ds)" in body


def test_missing_bytes_say_what_to_do_rather_than_404_silently():
    body = _DS_ROUTER.split("async def download_dataset(")[1].split("\n@")[0]
    assert "re-upload" in body


def test_the_original_filename_is_preserved():
    """"dataset.bin" tells you nothing about which of three similarly-named
    uploads you just opened — and this whole case is three cards called some
    variant of "Syteline source"."""
    body = _DS_ROUTER.split("async def download_dataset(")[1].split("\n@")[0]
    assert "filename=ds.file_name" in body


def test_the_button_is_wired():
    page = _BACKEND.parent / "frontend" / "src" / "pages" / "ConversionDetailPage.tsx"
    api = _BACKEND.parent / "frontend" / "src" / "api" / "index.ts"
    if not page.exists() or not api.exists():
        return
    assert "DatasetsApi.download(" in page.read_text(encoding="utf-8")
    assert "/datasets/${id}/download" in api.read_text(encoding="utf-8")
