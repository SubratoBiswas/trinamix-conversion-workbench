"""Six interface objects took 353 seconds, and none of them was slow on its own.

WHAT WAS ACTUALLY WRONG
-----------------------
``_run_merged_all`` generated every object strictly one at a time. Its docstring
said why — "sequentially (bounded memory)" — and that reason was real: a wide
multi-source load OOM'd the worker once, the request died at the gateway, and the
browser reported it as a CORS error. Serialising made peak memory the size of the
largest single object.

It also made wall-clock the SUM of every object. A bound of 2 keeps the guarantee
that made the original choice right — peak is N objects, N small and known — while
roughly halving the wait. It is an env var, because the safe number is a property
of the instance, not of this code.

MEASURED, NOT GUESSED
---------------------
Filling one real Supplier template — load the .xlsm, write 5,000 x 168 cells, save
— is about 12.8s. Six of those is ~77s, so the template fill was never where the
353 seconds went. That is the kind of thing a log line answers and a code reading
does not, which is why every object and the two heaviest phases are now timed on
every run.

AND THE WAIT LOOKED WORSE THAN IT WAS
-------------------------------------
The poller walked the carriers with ``await`` inside the loop and only ticked
after the whole sweep, so a bundle showing 6/6 done in one panel still read
"0/6 (353s)" on the button beside it.

Source-reading, like test_hook_order — there is no JS runtime here.
"""
import io
import os
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_BACKEND = Path(__file__).resolve().parent.parent
_API = _BACKEND.parent / "frontend" / "src" / "api" / "index.ts"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _py(rel: str) -> str:
    lines = (_BACKEND / rel).read_text(encoding="utf-8").splitlines()
    src = "\n".join(lines) + "\n"
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.COMMENT:
            continue
        (srow, scol), (_, ecol) = tok.start, tok.end
        ln = lines[srow - 1]
        lines[srow - 1] = ln[:scol] + " " * (ecol - scol) + ln[ecol:]
    return "\n".join(lines)


def test_objects_no_longer_generate_one_at_a_time():
    src = _py("app/routers/operations.py")
    check("the jobs run together", "asyncio.gather(*(_one(obj, cid)" in src)
    check("and the old serial loop is gone",
          "for obj, carrier_id in jobs:" not in src)


def test_concurrency_is_bounded_and_not_merely_removed():
    """The memory guarantee is the point. Unbounded gather over six wide objects
    is the OOM the sequential loop existed to prevent, reintroduced."""
    src = _py("app/routers/operations.py")
    check("a semaphore bounds it", "asyncio.Semaphore(merge_concurrency())" in src)
    check("and every job takes it", "async with sem:" in src)


def test_the_bound_is_configurable_without_a_deploy():
    """The safe number is a property of the instance, not of this code — a free
    tier and a paid one do not want the same answer."""
    from app.routers.operations import merge_concurrency
    old = os.environ.get("MERGE_CONCURRENCY")
    try:
        os.environ.pop("MERGE_CONCURRENCY", None)
        check("defaults to 2", merge_concurrency() == 2, f"got {merge_concurrency()}")
        os.environ["MERGE_CONCURRENCY"] = "1"
        check("can be put back to serial", merge_concurrency() == 1)
        os.environ["MERGE_CONCURRENCY"] = "5"
        check("can be raised", merge_concurrency() == 5)
        os.environ["MERGE_CONCURRENCY"] = "999"
        check("but not absurdly", merge_concurrency() == 8)
        os.environ["MERGE_CONCURRENCY"] = "0"
        check("never zero — that would generate nothing", merge_concurrency() == 1)
        os.environ["MERGE_CONCURRENCY"] = "banana"
        check("rubbish falls back to the default", merge_concurrency() == 2)
    finally:
        os.environ.pop("MERGE_CONCURRENCY", None)
        if old is not None:
            os.environ["MERGE_CONCURRENCY"] = old


def test_every_object_is_timed():
    """"Why is this slow" has now been answered twice by reading code, which can
    only establish where the time is NOT. A number per object per run is what
    makes the next answer a measurement."""
    src = _py("app/routers/operations.py")
    check("each object logs its own time", "%s finished in %.1fs" in src)
    check("the total is logged too", "object(s) in %.1fs at concurrency" in src)
    check("and it is kept on the carrier", '"output_seconds": round(_el, 1)' in src)
    model = _py("app/models/conversion.py")
    check("the model can hold it", "output_seconds" in model)


def test_a_failure_still_reports_how_long_it_ran_for():
    """A job that fails after four minutes and one that fails instantly are
    different problems."""
    src = _py("app/routers/operations.py")
    check("failures are timed", "failed after %.1fs" in src)


def test_the_two_heavy_phases_are_timed():
    src = _py("app/services/output_service.py")
    check("cleanse and validate is timed", "cleanse + validate took %.1fs" in src)
    check("the file write is timed", "write %s took %.1fs" in src)


def test_the_poller_asks_every_carrier_at_once():
    src = _API.read_text(encoding="utf-8")
    check("the sweep is parallel", "await Promise.all(pending.map" in src)
    check("only pending carriers are asked", "carriers.filter(c => !done[" in src)
    check("and the old serial await is gone",
          "for (const c of carriers) {\n        if (done[c.conversion_id]) continue;" not in src)


def test_a_polling_blip_is_not_read_as_a_failure():
    """One dropped request on a cold-starting free tier must not mark an interface
    failed — it has not finished, which is a different thing."""
    src = _API.read_text(encoding="utf-8")
    i = src.index("await Promise.all(pending.map")
    body = src[i:i + 700]
    check("a throw is tolerated", "catch {" in body)
    check("and skipped rather than failed", "if (!s) continue;" in src[i:i + 1200])


def test_the_poll_does_not_wait_three_seconds_to_notice_a_fast_object():
    src = _API.read_text(encoding="utf-8")
    check("the interval ramps", "i < 15 ? 1000 : 3000" in src)


def test_the_timeout_still_covers_a_long_bundle():
    """Ramping the early ticks shortens the total window if the count does not
    move with it — a 353-second bundle must still fit."""
    src = _API.read_text(encoding="utf-8")
    check("more ticks than before", "i < 400" in src)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall generation throughput checks passed")
