"""A call that passes an argument the callee cannot accept — the Customer outage.

WHAT HAPPENED, 05-Aug
---------------------
``generate_output_artifact`` writes the Customer package through a nested wrapper:

    def _apply_customer_layout(sdf, sheet_name, for_csv=True):
        return _customer_layout(sdf, sheet_name, _is_customer, for_csv=for_csv)

V2_2 of the sequence workbook added the END record terminator to all fifteen
Customer CSVs. The module function ``apply_customer_layout`` grew a ``with_end``
parameter, the call site was updated to pass it:

    sdf = _apply_customer_layout(sdf, s.sheet_name, for_csv=True, with_end=True)

...and the WRAPPER IN BETWEEN was not. Every Customer conversion died with

    _apply_customer_layout() got an unexpected keyword argument 'with_end'

There was already a test for this feature. It read the generator's source and
asserted the call was written:

    check("with the END terminator", "with_end=True)" in out)

It passed. It was always going to pass — it proves a caller EXISTS, never that
the callee can be called. That is the inert-feature failure with the polarity
reversed: not a feature nothing calls, but a call nothing could answer.

WHAT THIS SWEEP FLAGS
---------------------
Only the unambiguous shape, resolved entirely within one module:

  * a keyword the callee has no parameter for and no ``**kwargs`` to absorb
  * more positional arguments than parameters, with no ``*args``
  * a required parameter with no argument at all

WHAT IT DELIBERATELY DOES NOT
-----------------------------
Anything where the name at the call site might not be the def in this file:
imported names, names rebound by an assignment, names defined twice with
different signatures, decorated functions (a decorator may replace the signature
outright), and methods. Calls that unpack ``*args`` or ``**kwargs`` are skipped
because the count is not knowable statically.

The point is a sweep that stays on. One false positive and it gets deleted.
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_BACKEND = Path(__file__).resolve().parent.parent
_DIRS = ("app", "app/services", "app/routers", "app/parsers", "app/seed",
         "app/models")


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _py_files():
    seen = set()
    for d in _DIRS:
        p = _BACKEND / d
        if not p.is_dir():
            continue
        for f in sorted(p.glob("*.py")):
            if f.resolve() not in seen:
                seen.add(f.resolve())
                yield f


def _class_bodies(tree) -> set:
    """ids of functions defined directly in a class body — methods.

    A bare-name call to a method is either recursion inside the class or a
    different function entirely; either way the ``self`` offset makes the
    positional count wrong. Not worth the ambiguity.
    """
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.add(id(item))
    return out


def _signatures(tree) -> dict:
    """name -> list of def nodes, at every nesting depth."""
    methods = _class_bodies(tree)
    out: dict[str, list] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if id(node) in methods or node.decorator_list:
            continue
        out.setdefault(node.name, []).append(node)
    return out


def _shadowed(tree) -> set:
    """Names that might not be the def: imported, or assigned to somewhere.

    ``log = logging.getLogger(...)`` and ``from x import parse`` both mean a
    bare ``parse(...)`` need not be the local ``def parse``.
    """
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name):
                out.add(node.target.id)
    return out


def _accepts(fn) -> tuple:
    a = fn.args
    positional = list(a.posonlyargs) + list(a.args)
    by_keyword = {p.arg for p in a.args} | {p.arg for p in a.kwonlyargs}
    n_defaults = len(a.defaults)
    required_positional = positional[:len(positional) - n_defaults]
    required_kwonly = {p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is None}
    return (positional, by_keyword, required_positional, required_kwonly,
            a.vararg is not None, a.kwarg is not None)


def _mismatches(path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sigs = _signatures(tree)
    shadowed = _shadowed(tree)
    bad = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        name = node.func.id
        defs = sigs.get(name)
        if not defs or len(defs) != 1 or name in shadowed:
            continue
        if any(a is None or getattr(a, "arg", "") is None for a in node.keywords):
            pass
        if any(isinstance(a, ast.Starred) for a in node.args):
            continue
        if any(k.arg is None for k in node.keywords):
            continue

        (positional, by_keyword, required_positional, required_kwonly,
         has_star, has_kwargs) = _accepts(defs[0])
        given_kw = {k.arg for k in node.keywords}
        n_pos = len(node.args)
        where = f"{path.name}:{node.lineno} {name}()"

        if not has_kwargs:
            for kw in sorted(given_kw - by_keyword):
                bad.append(f"{where} passes '{kw}', which it does not accept "
                           f"(defined line {defs[0].lineno})")
        if not has_star and n_pos > len(positional):
            bad.append(f"{where} passes {n_pos} positional args, "
                       f"accepts {len(positional)} (defined line {defs[0].lineno})")

        filled = {p.arg for p in positional[:n_pos]} | given_kw
        for p in required_positional:
            if p.arg not in filled:
                bad.append(f"{where} never supplies required '{p.arg}' "
                           f"(defined line {defs[0].lineno})")
        for p in sorted(required_kwonly - given_kw):
            bad.append(f"{where} never supplies required keyword-only '{p}' "
                       f"(defined line {defs[0].lineno})")
    return bad


def test_no_call_passes_an_argument_its_callee_cannot_accept():
    """The sweep. Runs in well under a second and needs no database."""
    found = []
    for f in _py_files():
        found.extend(_mismatches(f))
    check("every resolvable call matches its definition", not found,
          "\n    " + "\n    ".join(found))


def test_the_customer_wrapper_forwards_with_end():
    """The specific regression, named, so the reason survives the sweep.

    Asserted against the real signature rather than the source text — the text
    test is what let this ship.
    """
    import inspect
    import textwrap
    from app.services import output_service

    src = textwrap.dedent(inspect.getsource(output_service.generate_output_artifact))
    tree = ast.parse(src)
    wrapper = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "_apply_customer_layout"), None)
    check("the wrapper exists", wrapper is not None)
    names = {p.arg for p in wrapper.args.args} | {p.arg for p in wrapper.args.kwonlyargs}
    check("it accepts with_end", "with_end" in names,
          f"accepts {sorted(names)}")

    forwards = [n for n in ast.walk(wrapper)
                if isinstance(n, ast.Call)
                and any(k.arg == "with_end" for k in n.keywords)]
    check("and forwards it to the layout module", bool(forwards),
          "it accepts with_end and drops it, which is worse than not accepting it")


def test_the_layout_module_still_takes_with_end():
    """The other half of the seam: the callee the wrapper delegates to."""
    import inspect
    from app.services.supplier_fbdi_layout import apply_customer_layout

    params = inspect.signature(apply_customer_layout).parameters
    check("apply_customer_layout takes with_end", "with_end" in params)
    check("and defaults it to None so for_csv decides",
          params["with_end"].default is None,
          f"default is {params['with_end'].default!r}")


if __name__ == "__main__":
    for fn in (test_no_call_passes_an_argument_its_callee_cannot_accept,
               test_the_customer_wrapper_forwards_with_end,
               test_the_layout_module_still_takes_with_end):
        print(fn.__name__)
        fn()
