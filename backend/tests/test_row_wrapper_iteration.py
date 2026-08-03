"""The row wrapper has to iterate like a dict, because rules iterate it.

Supplier Site and Supplier Site Assignment failed to generate for a week — two of six
files simply absent from the bundle, with no error on any screen — and this was why:

    KeyError: 0 | at engine.py:729 apply_pipeline <- engine.py:574 apply_rule
                 <- engine.py:564 _first <- output_service.py:341 __getitem__

``_first`` asks which of several column names a row actually has, by writing
``{norm(k): k for k in row}``. ``_RowWithTargets`` defined ``get``, ``__getitem__``
and ``__contains__`` but NOT ``__iter__`` — so Python fell back to the LEGACY sequence
protocol, calling ``row[0]``, ``row[1]``, … until IndexError, and ``__getitem__``
raised ``KeyError(0)`` on the very first step.

Two things made it expensive out of proportion to the fix. Generation runs in a
background worker, so it surfaced as a conversion that never produced output rather
than as an exception anyone could see. And the wrapper was verified through ``get()``,
which is how the preview path uses it — so every rule that ITERATES the row was broken
from the moment it shipped, and nothing exercised that.

Pure: stdlib + the class under test.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.output_service import _RowWithTargets  # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
        return
    raise AssertionError(f"{name} {detail}".strip())


def test_the_expression_that_crashed_now_works():
    """Verbatim from engine._first — the comprehension, not a paraphrase of it."""
    r = _RowWithTargets({"City": "Hyderabad", "Name": "ACME"},
                        {"Party Type": ["ORGANIZATION"]}, 0)
    by_norm = {str(k).lower(): k for k in r}
    check("iterating the row does not raise", True)
    check("source columns are yielded", {"city", "name"} <= set(by_norm), by_norm)
    check("so are target columns computed earlier in the row",
          "party type" in by_norm, by_norm)


def test_iteration_agrees_with_what_get_resolves():
    """A key iteration offers must be one get() can answer, or a rule finds a name
    and then reads nothing from it — mapped on screen, empty in the file."""
    r = _RowWithTargets({"City": "Hyderabad"}, {"Party Type": ["ORGANIZATION"]}, 0)
    for k in r:
        check(f"get({k!r}) answers", r.get(k) is not None)
    check("len matches the key count", len(r) == 2, len(r))


def test_a_source_column_shadows_a_target_of_the_same_name_exactly_once():
    """get() prefers the source, so iteration must not yield the name twice — a
    duplicate would let a rule pick the target copy and quietly disagree with the
    value the same row resolves."""
    r = _RowWithTargets({"Party Type": "SRC"}, {"Party Type": ["TGT"]}, 0)
    keys = list(r)
    check("yielded once", keys.count("Party Type") == 1, keys)
    check("and the source value wins", r.get("Party Type") == "SRC")


def test_a_missing_key_still_raises_KeyError():
    """The mapping contract is unchanged — this is additive."""
    r = _RowWithTargets({"City": "Hyderabad"}, {}, 0)
    try:
        r["nope"]
    except KeyError:
        check("missing key raises KeyError, not IndexError", True)
    else:
        raise AssertionError("a missing key did not raise")


def _all():
    return [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]


if __name__ == "__main__":
    for name, fn in _all():
        print(name)
        fn()
    print(f"\n{len(_all())} tests passed")
