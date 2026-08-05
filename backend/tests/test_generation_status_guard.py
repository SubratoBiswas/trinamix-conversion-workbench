"""The staleness guard must not fail work that finished.

WHAT HAPPENED, MEASURED LIVE ON 05-AUG
--------------------------------------
One Supplier Site interface — 5,476 source rows, 88 columns, into a 210-column
template — took about 570 seconds on the free instance and WROTE ITS ARTIFACT:
`SupplierSite_Template.xlsx`, 5,405 rows, 646KB, on disk and downloadable.

A few seconds later the poller's staleness guard hit its hard-coded 600 and
declared the run failed. The file was correct the whole time; the screen said it
had failed, so nobody went looking for it.

This is the codebase's signature bug wearing the opposite mask. Usually the screen
looks right and the file is wrong. Here the file was right and the screen said
failure — which is worse in one specific way: it sends you to regenerate, which
takes another ten minutes and fails the same way.

TWO CAUSES, BOTH FIXED HERE
---------------------------
1. The guard trusted a CLOCK. It now asks the disk first: an artifact generated
   at or after this run's start, whose file exists, is proof the work completed,
   and it is reported ready. Only when there is no such file is it a failure —
   which is the case the guard was actually written for (a worker restart or an
   OOM kill, where nothing was ever written).

2. The clock measured QUEUEING, not work. `generate-merged-all` stamps
   `output_started_at` on all six carriers at kick-off while `MERGE_CONCURRENCY`
   of them run at a time, so with six interfaces two at a time the last pair had
   been "generating" for twenty minutes before their turn came. Every one of the
   six reported failed at 600s with nothing wrong. `_run_merged_all` now
   re-stamps each carrier when its turn actually begins.

The ceiling is also an env var now (`GENERATION_TIMEOUT_SECONDS`, default 2400),
because 600 was shorter than one real interface on the instance this runs on.
"""
import ast
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_BACKEND = Path(__file__).resolve().parent.parent
_OPS = _BACKEND / "app" / "routers" / "operations.py"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _src() -> str:
    return _OPS.read_text(encoding="utf-8")


def _fn_src(name: str) -> str:
    tree = ast.parse(_src())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


# ---------------------------------------------------------------------------
# The timeout itself
# ---------------------------------------------------------------------------
def test_the_ceiling_is_no_longer_shorter_than_one_real_interface():
    """600s was under the measured 570s for a single Supplier Site, leaving no
    margin at all on the instance this actually runs on."""
    from app.routers.operations import _generation_timeout

    os.environ.pop("GENERATION_TIMEOUT_SECONDS", None)
    check("the default is generous", _generation_timeout() >= 1800,
          f"got {_generation_timeout()}")


def test_the_ceiling_can_be_raised_without_a_deploy():
    from app.routers.operations import _generation_timeout

    os.environ["GENERATION_TIMEOUT_SECONDS"] = "3600"
    try:
        check("env var is honoured", _generation_timeout() == 3600)
        os.environ["GENERATION_TIMEOUT_SECONDS"] = "not a number"
        check("garbage falls back rather than crashing the poller",
              _generation_timeout() >= 1800)
        os.environ["GENERATION_TIMEOUT_SECONDS"] = "1"
        check("and it cannot be set absurdly low", _generation_timeout() >= 60)
    finally:
        os.environ.pop("GENERATION_TIMEOUT_SECONDS", None)


# ---------------------------------------------------------------------------
# Ask the disk before believing the clock
# ---------------------------------------------------------------------------
def test_the_guard_checks_for_a_finished_artifact_before_failing():
    body = _raw_fn("generation_status")
    check("it looks for an artifact", "ConvertedOutput.find" in body)
    check("from THIS run, not any older one", "output_started_at" in body)
    check("and confirms the file is really on disk", ".exists()" in body)
    check("a finished run is reported ready",
          "'ready'" in body or '"ready"' in body)
    i_ready = body.find("ready")
    i_failed = body.find("failed", body.find("_generation_timeout"))
    check("the ready branch is considered first", 0 < i_ready < i_failed,
          f"ready@{i_ready} failed@{i_failed}")


def test_the_failure_message_no_longer_claims_a_timeout_it_cannot_know_about():
    """"Generation timed out or the worker restarted" was told to somebody whose
    file had been written. The message now describes what was actually observed:
    no file, after a measured number of seconds."""
    body = _raw_fn("generation_status")
    check("the old wording is gone", "Generation timed out or the worker restarted"
          not in body)
    check("it says no file was written", "no file was written" in body)
    check("and it reports the measured age", "{int(age)}" in body)


# ---------------------------------------------------------------------------
# The clock measures work, not queueing
# ---------------------------------------------------------------------------
def _raw_fn(name: str) -> str:
    """The function's own SOURCE TEXT. ast.unparse renormalises string quotes, so
    a test that matches on `"output_status": "generating"` silently never matches."""
    src = _src()
    tree = ast.parse(src)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    lines = src.split("\n")
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def test_the_carrier_clock_restarts_when_its_turn_actually_comes():
    """With six interfaces at MERGE_CONCURRENCY=2, the last pair had been
    'generating' for twenty minutes before any work began on them."""
    body = _raw_fn("_run_merged_all")
    check("the semaphore is still what bounds concurrency", "async with sem" in body)
    i_sem = body.index("async with sem")
    i_stamp = body.find("output_started_at", i_sem)
    check("output_started_at is re-stamped", i_stamp > i_sem,
          "the carrier clock still starts at queue time")
    # The CALL, not the import at the top of the function — that sits before the
    # semaphore and would make this pass wherever the stamp went.
    i_gen = body.index("await generate_merged_artifact(")
    check("and it happens before the work, not after", i_stamp < i_gen,
          f"stamp@{i_stamp} call@{i_gen}")


def test_the_kickoff_still_marks_every_carrier_generating():
    """The re-stamp must not replace the initial stamp — a carrier that has been
    queued but not started still has to read as 'generating', or the poller sees
    'idle' and the client stops waiting."""
    body = _raw_fn("generate_merged_all")
    check("carriers are marked at kick-off", '"output_status": "generating"' in body)
    check("with a start time", "output_started_at" in body)


# ---------------------------------------------------------------------------
# The behaviour, run rather than read
#
# Against a real Beanie session (mongomock), because the guard's whole job is to
# ask the DATABASE and the DISK what happened. Substituting those is substituting
# the thing under test.
# ---------------------------------------------------------------------------
import pytest                                                    # noqa: E402

pytest.importorskip("mongomock_motor",
                    reason="mongomock_motor is not installed — the guard's "
                           "behaviour cannot be exercised")

from beanie import init_beanie                                   # noqa: E402
from mongomock_motor import AsyncMongoMockClient                 # noqa: E402

from app.models.conversion import Conversion                     # noqa: E402
from app.models.output import ConvertedOutput                    # noqa: E402
from app.models.project import Project                           # noqa: E402
import app.routers.operations as ops                             # noqa: E402


async def _scenario(started_ago: int, artifact_age: int | None, file_on_disk: bool):
    """Build one conversion whose run started `started_ago` seconds back, with an
    artifact `artifact_age` seconds old (None = no artifact at all). Returns the
    poller's answer and the conversion as it was left."""
    import tempfile
    await init_beanie(database=AsyncMongoMockClient()["guard"],
                      document_models=[Project, Conversion, ConvertedOutput])
    pr = Project(name="guard"); await pr.insert()
    started = datetime.utcnow() - timedelta(seconds=started_ago)
    cv = Conversion(project_id=pr.id, name="c", target_object="Supplier Site",
                    output_status="generating", output_started_at=started)
    await cv.insert()
    if artifact_age is not None:
        tmp = Path(tempfile.mkdtemp()) / "SupplierSite_Template.xlsx"
        tmp.write_bytes(b"x" * 1024)
        if not file_on_disk:
            tmp.unlink()
        await ConvertedOutput(
            conversion_id=cv.id, output_file_path=str(tmp),
            output_file_name=tmp.name, row_count=5405, column_count=210,
            generated_at=datetime.utcnow() - timedelta(seconds=artifact_age),
        ).insert()
    res = await ops.generation_status(str(cv.id), None)
    return res, await Conversion.get(cv.id)


def test_a_run_that_finished_late_is_reported_ready_not_failed():
    """The 05-Aug case exactly: started ~1h ago (past any ceiling), artifact
    written 30 seconds ago, file on disk. It finished. Say so."""
    import asyncio
    res, cv = asyncio.run(_scenario(started_ago=4000, artifact_age=30, file_on_disk=True))
    check("reported ready", res.get("status") == "ready", f"got {res.get('status')}")
    check("and written back as ready", cv.output_status == "ready",
          f"got {cv.output_status!r}")
    check("with no error text", not cv.output_error, f"got {cv.output_error!r}")
    check("and the artifact is handed to the client", res.get("output"))


def test_a_run_that_wrote_nothing_is_still_a_failure():
    """The case the guard was actually written for — a worker restart or an OOM
    kill, where the status would otherwise sit on 'generating' forever."""
    import asyncio
    res, cv = asyncio.run(_scenario(started_ago=4000, artifact_age=None, file_on_disk=False))
    check("reported failed", res.get("status") == "failed", f"got {res.get('status')}")
    check("and says no file was written",
          "no file was written" in (cv.output_error or ""), f"got {cv.output_error!r}")


def test_an_artifact_whose_FILE_is_gone_does_not_count_as_finished():
    """The record surviving in Mongo while the ephemeral disk was wiped is the
    one case where believing the database alone would be wrong."""
    import asyncio
    res, cv = asyncio.run(_scenario(started_ago=4000, artifact_age=30, file_on_disk=False))
    check("reported failed", res.get("status") == "failed", f"got {res.get('status')}")


def test_an_artifact_from_an_EARLIER_run_does_not_rescue_a_failed_one():
    """Otherwise a conversion that generated successfully last week reports ready
    forever and a genuine failure is invisible."""
    import asyncio
    res, _cv = asyncio.run(_scenario(started_ago=4000, artifact_age=90000, file_on_disk=True))
    check("an older artifact does not count", res.get("status") == "failed",
          f"got {res.get('status')}")


def test_a_run_still_inside_the_window_is_left_alone():
    """The guard must only ever act after the ceiling. A conversion that is
    genuinely mid-flight keeps reporting generating."""
    import asyncio
    res, cv = asyncio.run(_scenario(started_ago=30, artifact_age=None, file_on_disk=False))
    check("still generating", res.get("status") == "generating", f"got {res.get('status')}")
    check("and nothing was written back", cv.output_status == "generating")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall generation status guard checks passed")
