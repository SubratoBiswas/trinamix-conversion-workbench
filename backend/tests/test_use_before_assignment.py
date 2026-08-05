"""A local name read before it is ever assigned — the whole-feature outage shape.

WHAT HAPPENED, 04-Aug 15:51
---------------------------
``generate_output_artifact`` grew an advisory timing log:

    log.info("generate phase — %s: cleanse + validate took %.1fs",
             obj_name, _time.monotonic() - _dq_t0)

``obj_name`` was assigned about sixty lines FURTHER DOWN. In Python a name
assigned anywhere in a function is local for the whole of it, so that read raised
``UnboundLocalError`` before a single byte was written — every format, every
object, every conversion. Generation was dead from that commit until it was found
on 05-Aug, and the line that killed it does nothing but write a log message.

Two reasons it survived review and a green suite:

  * It reads perfectly. Nothing about the line is wrong except where it sits, and
    the assignment it needs IS in the function — just later.
  * Nothing ever called the function. 1,182 tests passed over a generator that
    could not generate. ``test_bom_produced_file.py`` closes that half by
    actually producing a file; this closes the half that costs nothing to run.

WHAT THIS SWEEP FLAGS, AND WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------------
Only the unambiguous shape: a name assigned EXACTLY ONCE in the function, by a
statement at the function's own body level, and read at an earlier line. That is
a certain bug — there is no path on which the read happens after the write.

It does not attempt real flow analysis. Names assigned in a branch, a loop, a
``with`` or an ``except`` are skipped, because "assigned later in the source" and
"assigned later in time" genuinely differ there and a sweep that guesses would be
turned off within a week. Names declared ``global`` or ``nonlocal`` are skipped
for the same reason: the binding is not local at all.
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_BACKEND = Path(__file__).resolve().parent.parent
_DIRS = ("app", "app/services", "app/routers", "app/parsers", "app/seed")


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _nested_node_ids(fn) -> set:
    """Everything inside a nested def/lambda.

    A closure legitimately reads an outer name that is assigned after the inner
    function is DEFINED, because the body runs later. Counting those reads would
    make the sweep wrong about correct code.
    """
    out = set()
    for sub in ast.walk(fn):
        if sub is fn or not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        for node in ast.walk(sub):
            out.add(id(node))
    return out


def _param_names(fn) -> set:
    a = getattr(fn, "args", None)
    if a is None:
        return set()
    names = {p.arg for p in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def _declared_elsewhere(fn, skip: set) -> set:
    """Names the function says are not its own."""
    out = set()
    for node in ast.walk(fn):
        if id(node) in skip:
            continue
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            out.update(node.names)
    return out


def _body_level_assignments(fn) -> dict:
    """Names assigned by a statement in the function's own body — not inside an
    if / for / while / try / with, where source order is not execution order."""
    lines: dict[str, list[int]] = {}
    for st in fn.body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    lines.setdefault(t.id, []).append(st.lineno)
        elif isinstance(st, (ast.AnnAssign, ast.AugAssign)) and isinstance(st.target, ast.Name):
            lines.setdefault(st.target.id, []).append(st.lineno)
    return lines


def read_before_assigned(source: str) -> list:
    """[(function, name, read_line, assigned_line)] for the certain cases only."""
    out = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        skip = _nested_node_ids(fn)
        ignore = _param_names(fn) | _declared_elsewhere(fn, skip)

        stores: dict[str, list[int]] = {}
        loads: dict[str, list[int]] = {}
        for node in ast.walk(fn):
            if id(node) in skip or not isinstance(node, ast.Name):
                continue
            (stores if isinstance(node.ctx, ast.Store) else loads).setdefault(
                node.id, []).append(node.lineno)

        for name, at in _body_level_assignments(fn).items():
            if name in ignore:
                continue
            # Assigned more than once anywhere? Then an earlier write may exist on
            # some path and this sweep has nothing certain to say.
            if len(stores.get(name, [])) != 1:
                continue
            reads = loads.get(name, [])
            if reads and min(reads) < at[0]:
                out.append((fn.name, name, min(reads), at[0]))
    return out


def test_no_backend_module_reads_a_local_before_assigning_it():
    hits = []
    for d in _DIRS:
        for path in sorted((_BACKEND / d).glob("*.py")):
            for fn, name, read, wrote in read_before_assigned(
                    path.read_text(encoding="utf-8")):
                hits.append(f"{path.relative_to(_BACKEND)}:{read} "
                            f"{fn}() reads {name!r} — assigned at line {wrote}")
    check("nothing reads a local before assigning it", not hits,
          "\n         " + "\n         ".join(hits))


def test_the_sweep_catches_the_shape_it_was_written_for():
    """The bug as it actually appeared, so a refactor of this file cannot quietly
    turn it into a function that always returns nothing."""
    src = (
        "async def generate(conversion):\n"
        "    template = await load(conversion)\n"
        "    log.info('phase %s took %.1fs', obj_name, elapsed())\n"
        "    obj_name = template.business_object or 'fbdi'\n"
        "    return obj_name\n"
    )
    hits = read_before_assigned(src)
    check("the shape is caught", len(hits) == 1, f"got {hits}")
    fn, name, read, wrote = hits[0]
    check("in the right function", fn == "generate")
    check("names the variable", name == "obj_name")
    check("and both lines", (read, wrote) == (3, 4), f"got {(read, wrote)}")


def test_the_sweep_does_not_flag_a_closure_reading_an_outer_name():
    """The common correct pattern this must not break: an inner function defined
    before the name it closes over is assigned, called after."""
    src = (
        "def outer():\n"
        "    def inner():\n"
        "        return later\n"
        "    later = 1\n"
        "    return inner()\n"
    )
    check("closure is not flagged", read_before_assigned(src) == [],
          f"got {read_before_assigned(src)}")


def test_the_sweep_does_not_flag_a_global():
    """``global x`` means the binding is not local, so 'assigned later in this
    function' says nothing about whether the read succeeds. services/
    client_service.py does exactly this and is correct."""
    src = (
        "_cache = None\n"
        "def get():\n"
        "    global _cache\n"
        "    if _cache is not None:\n"
        "        return _cache\n"
        "    _cache = compute()\n"
        "    return _cache\n"
    )
    check("global is not flagged", read_before_assigned(src) == [],
          f"got {read_before_assigned(src)}")


def test_the_sweep_does_not_flag_a_conditional_assignment():
    """Assigned in a branch and read after: source order is not execution order,
    and a sweep that guessed here would be switched off inside a week."""
    src = (
        "def f(flag):\n"
        "    if flag:\n"
        "        x = 1\n"
        "    else:\n"
        "        x = 2\n"
        "    return x\n"
    )
    check("branches are not flagged", read_before_assigned(src) == [])


def test_the_sweep_does_not_flag_a_loop_accumulator():
    src = (
        "def f(items):\n"
        "    total = 0\n"
        "    for i in items:\n"
        "        total = total + i\n"
        "    return total\n"
    )
    check("accumulator is not flagged", read_before_assigned(src) == [])


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall use-before-assignment checks passed")
