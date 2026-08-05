"""Generation returns when the FILE is done, not when the bookkeeping is.

MEASURED LIVE, 05-AUG
---------------------
A Supplier Site generate — 5,476 source rows, 88 columns, into a 210-column
template — wrote its artifact at 150 seconds. The API went on reporting
"generating" past 410 seconds. The file was complete, correct and downloadable
for that entire four minutes; it was downloaded while the poller still said the
run was in progress.

All of it was ``capture_learnings_from_conversion``, awaited AFTER the artifact
had been written and recorded. It is one Mongo upsert per mapped field, in
sequence, and — as the comment right above it already said about the object it
skips — it "contributes nothing to the file that was just written".

The skip conceded the point for objects over 300 fields. Supplier Site has 211,
so it paid in full, and the analyst waited nearly three times longer than the
work took.

THE RULE THIS ENCODES
---------------------
The deliverable is the artifact. Learning capture is a side effect, and a side
effect does not get to hold the deliverable hostage. It now runs after the
return; a failure is logged, never raised. If the worker dies mid-capture the
learnings are lost and the file is not — which is the right way round, because
nothing downstream reads them synchronously.
"""
import ast
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_BACKEND = Path(__file__).resolve().parent.parent
_SRC = _BACKEND / "app" / "services" / "output_service.py"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _generate_fn_source() -> str:
    """The raw source text of generate_output_artifact. Raw, not ast.unparse:
    unparse renormalises quotes and drops comments, and two of the checks below
    are about the order of statements as written."""
    src = _SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "generate_output_artifact")
    return "\n".join(src.split("\n")[node.lineno - 1:node.end_lineno])


def test_learning_capture_is_not_awaited_on_the_way_out():
    body = _generate_fn_source()
    check("capture is still called", "capture_learnings_from_conversion" in body)
    # The await inside the _capture task is correct and expected — that is where
    # the work belongs. What must not exist is an await in the OUTER flow, which
    # is what held the artifact. So this is a question of WHERE, not whether.
    i_inner = body.index("async def _capture")
    i_await = body.index("await capture_learnings_from_conversion(conversion)")
    check("the only await is inside the scheduled task", i_await > i_inner,
          "the outer flow still waits on the learning pass")
    check("it is scheduled rather than awaited", "create_task(_capture())" in body)
    check("and the scheduling happens after the artifact is recorded",
          body.index("await artefact.insert()") < i_inner)


def test_it_still_runs_when_there_is_no_event_loop():
    """A script or a sync test has no running loop. Dropping the capture there
    would make the behaviour depend on how the function was called, which is how
    a feature quietly stops happening in one environment."""
    body = _generate_fn_source()
    check("the no-loop case is handled", "except RuntimeError:" in body)
    check("and it falls back to running it", "await _capture()" in body)


def test_the_artifact_is_recorded_before_any_of_it():
    """Ordering is the whole point: the record has to exist before the side effect
    starts, or a poller that asks 'is there a file yet' still gets no for minutes."""
    body = _generate_fn_source()
    i_insert = body.index("await artefact.insert()")
    i_capture = body.index("capture_learnings_from_conversion")
    check("insert comes first", i_insert < i_capture, f"{i_insert} vs {i_capture}")
    i_return = body.rindex("return artefact")
    check("and the function returns after scheduling, not after capturing",
          i_return > i_capture)


def test_a_failing_capture_never_costs_the_caller_its_artifact():
    """The artifact is the deliverable. A learning-store hiccup must not turn a
    generate that produced a correct file into a failed one."""
    body = _generate_fn_source()
    i_capture = body.index("async def _capture")
    tail = body[i_capture:]
    check("the capture body catches", "except Exception:" in tail)
    check("and logs rather than raising", "log.exception" in tail)
    check("nothing re-raises", "raise" not in tail.split("return artefact")[0])


def test_the_scheduled_capture_actually_runs_and_is_isolated():
    """Behaviour, not source-reading: the same shape the function uses — schedule
    on the running loop, let a failure surface only in the log."""
    ran = {"ok": 0, "boom": 0}

    async def _ok():
        ran["ok"] += 1

    async def _boom():
        ran["boom"] += 1
        raise RuntimeError("learning store is down")

    async def _driver(fn):
        async def _wrapped():
            try:
                await fn()
            except Exception:  # noqa: BLE001
                pass
        asyncio.get_running_loop().create_task(_wrapped())
        return "artifact"          # the caller returns immediately

    async def _main():
        got = await _driver(_ok)
        check("caller returned without waiting", got == "artifact")
        got = await _driver(_boom)
        check("caller returned even though capture will fail", got == "artifact")
        await asyncio.sleep(0)     # let the tasks run
        await asyncio.sleep(0)
        return ran

    out = asyncio.run(_main())
    check("the good capture ran", out["ok"] == 1, f"got {out}")
    check("the failing one ran and was swallowed", out["boom"] == 1, f"got {out}")


def test_the_heavy_skip_is_still_there():
    """Objects over 300 fields skip capture entirely. That decision predates this
    one and is still right — scheduling hundreds of upserts in the background is
    better than awaiting them, but not doing them at all is better still."""
    body = _generate_fn_source()
    check("the heavy guard survives", "if not _heavy:" in body)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall generate-return-timing checks passed")
