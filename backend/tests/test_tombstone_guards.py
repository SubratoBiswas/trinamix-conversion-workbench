"""A tombstone guard that cannot see tombstones is not a guard.

``LearnedMapping.find/find_one/find_all`` inject ``{"is_deleted": {"$ne": True}}`` so a
retired learning disappears from every read path — roughly 40 query sites across 18
modules, which is why the filter lives on the model rather than at each call site.

That default has a sharp edge. Code whose JOB is to reason about deleted rows must opt
back in with ``include_deleted=True``, and if it forgets, the row is invisible and the
``is_deleted`` check it wrote is dead code that can never fire. It does not fail loudly;
it does the opposite of what it says.

That is exactly what had happened in ``learning_service._upsert`` — the path every
interactive save runs through: approving a mapping, adding a fixed value, saving a rule.
The tombstoned row was filtered out, the guard could not fire, the "does it already
exist" loop found nothing, and the function fell through to INSERT A FRESH DUPLICATE.
A learning the analyst had deleted came back on the next approve that touched the field.
Third instance of CW #5, after auto-capture and the seeds.

The test below is the general form, because the specific bug was found by an audit and
the audit is worth keeping. Two call sites legitimately rely on the default filter and
are listed as exceptions WITH their reasons, so the list stays honest.

Pure: stdlib AST, no DB.
"""
import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_APP = Path(__file__).resolve().parent.parent / "app"
_failures = []

# Call sites that mention is_deleted but correctly rely on the default filter.
# Each is here because EXCLUDING retired rows is the intended behaviour, not an
# oversight — so the entry records why, and a new name cannot be added silently.
_ALLOWED = {
    ("services/catalog_seed_service.py", "_seed_catalog_file"):
        "The supersession loop deletes prior rows. A row the user already retired "
        "should not be deleted again — resurrecting it just to re-delete would clear "
        "the tombstone. Hiding it is the correct behaviour.",
    ("services/mapping_ingest_service.py", "supersede_previous"):
        "Retires learnings a superseded document asserted. An already-retired row "
        "needs no second tombstone, so excluding it is right.",
}


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def _call_sites():
    """(relative path, function, line, passes include_deleted, mentions is_deleted)."""
    out = []
    for p in sorted(_APP.rglob("*.py")):
        src = p.read_text(encoding="utf-8")
        if "LearnedMapping" not in src:
            continue
        tree = ast.parse(src)
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            if n.func.attr not in ("find", "find_one", "find_all"):
                continue
            if not (isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "LearnedMapping"):
                continue
            owner = None
            for f in funcs:
                if f.lineno <= n.lineno <= (f.end_lineno or f.lineno):
                    if owner is None or f.lineno > owner.lineno:
                        owner = f
            out.append((
                str(p.relative_to(_APP)).replace(os.sep, "/"),
                owner.name if owner else "?",
                n.lineno,
                any(k.arg == "include_deleted" for k in n.keywords),
                "is_deleted" in ast.unparse(owner) if owner else False,
            ))
    return out


def test_the_audit_finds_the_query_sites_at_all():
    """If this drops to nothing the audit has silently stopped auditing."""
    sites = _call_sites()
    check("many call sites found", len(sites) >= 30, f"got {len(sites)}")


def test_no_dead_tombstone_guard():
    """The general form of the bug: a function that reasons about is_deleted while
    querying with the filter that removes those rows."""
    dead = [(f, fn, ln) for f, fn, ln, inc, mentions in _call_sites()
            if mentions and not inc and (f, fn) not in _ALLOWED]
    check("no function guards on is_deleted while filtering it out", not dead,
          "\n        " + "\n        ".join(f"{f}:{ln} in {fn}()" for f, fn, ln in dead))


def test_the_interactive_save_path_can_see_tombstones():
    """The specific regression. _upsert is what runs when the analyst approves a
    mapping, adds a fixed value or saves a rule — every interactive write."""
    sites = [s for s in _call_sites() if s[1] == "_upsert"]
    check("_upsert queries the library", sites, "call site vanished")
    for f, fn, ln, inc, _m in sites:
        check(f"{f}:{ln} passes include_deleted", inc,
              "without it the tombstone is invisible and a deleted learning is "
              "re-inserted as a duplicate on the next approve")


def test_the_auto_capture_path_can_see_tombstones_too():
    """_upsert_learned was fixed earlier for the same reason. Pinned so a later edit
    cannot quietly drop it."""
    sites = [s for s in _call_sites() if s[1] == "_upsert_learned"]
    check("call site present", sites)
    check("passes include_deleted", all(s[3] for s in sites))


def test_every_allowed_exception_still_exists():
    """A stale allow-list is how a real defect gets waved through later. If one of
    these functions is renamed or removed, the entry must go with it."""
    present = {(f, fn) for f, fn, _ln, _i, _m in _call_sites()}
    stale = [k for k in _ALLOWED if k not in present]
    check("no stale entries", not stale, f"remove: {stale}")


def test_each_allowed_exception_records_why():
    for key, reason in _ALLOWED.items():
        check(f"{key[1]} has a reason", len(reason) > 60, "one line of intent, so the "
              "next reader can tell an exception from an oversight")


def test_the_restore_and_retire_endpoints_opt_in():
    """These exist to SHOW retired rows; without include_deleted they would list
    nothing and the restore feature would appear broken."""
    for fn in ("restore_learned", "list_retired"):
        sites = [s for s in _call_sites() if s[1] == fn]
        check(f"{fn} present", sites, "endpoint gone?")
        check(f"{fn} opts in", all(s[3] for s in sites))


def test_the_model_still_filters_by_default():
    """The whole guarantee rests on this. If the override is removed, retired
    learnings reappear everywhere and every call site above becomes wrong at once."""
    src = (_APP / "models" / "learned.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "LearnedMapping")
    overrides = {n.name for n in cls.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for m in ("find", "find_one", "find_all"):
        check(f"{m} overridden", m in overrides, f"got {overrides}")
    body = ast.unparse(cls)
    check("the filter is the tombstone", '"is_deleted"' in body or "'is_deleted'" in body)
    check("and it is opt-out, not opt-in", "include_deleted" in body)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except AssertionError:
            pass
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("all checks passed")
