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
    ("services/catalog_seed_service.py", "seed_supplier_source_mapping"):
        "The prior-edition lookup REWRITES the row the previous workbook wrote, so a "
        "rebound field keeps one answer instead of two. Finding a RETIRED row there "
        "and rewriting it would resurrect a tombstone under a new value — the analyst "
        "retired that binding on purpose. Hiding it is correct: the new edition's "
        "mapping is then inserted as its own row, which is what a fresh instruction "
        "should do. The other lookup in this function does pass include_deleted=True, "
        "because that one exists precisely to detect the tombstone and skip.",
}


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


# Functions that legitimately construct a LearnedMapping with no tombstone lookup.
# Deliberately short, and each entry is a claim that has to stay true.
_INSERT_ALLOWED = {
    # The Learning Center's explicit "add a learning" button. There is nothing to
    # resurrect: the analyst is creating a row, by hand, on purpose.
    ("routers/learned.py", "create_learned"),
}

_GET_LINES: dict = {}


def _is_get(rel_path, lineno):
    return _GET_LINES.get((rel_path, lineno), False)


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
            # `get` matters as much as `find`: Beanie's Document.get() DELEGATES to
            # find_one, and LearnedMapping.find injects {'is_deleted': {'$ne': True}},
            # so get() is tombstone-blind too — and it takes no include_deleted
            # argument at all, so a get() on this model can never be made safe. Two
            # live resurrections hid behind it, including the Learning Center's own
            # delete endpoint, where it meant a retired row could never be purged.
            if n.func.attr not in ("find", "find_one", "find_all", "get"):
                continue
            if not (isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "LearnedMapping"):
                continue
            owner = None
            for f in funcs:
                if f.lineno <= n.lineno <= (f.end_lineno or f.lineno):
                    if owner is None or f.lineno > owner.lineno:
                        owner = f
            _rel = str(p.relative_to(_APP)).replace(os.sep, "/")
            _GET_LINES[(_rel, n.lineno)] = (n.func.attr == "get")
            out.append((
                _rel,
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


def test_no_write_path_inserts_without_first_looking_for_a_tombstone():
    """THE test that should have existed. The old audit anchored on "mentions
    is_deleted AND does not pass include_deleted", which can only ever catch a DEAD
    guard — a function that already knows about tombstones. It could not catch a
    MISSING one, and six write paths had no idea tombstones existed: manual-map save,
    apply-proposal, accept-value-map, mapping-workbook import, prompt steering and
    classify-learn. Every one of them re-created a learning the analyst had deleted.

    Anchor on the INSERT instead: any function that constructs a LearnedMapping must
    have queried with include_deleted=True somewhere on its own body, or it cannot
    have seen the tombstone it is about to step over.
    """
    bad = []
    for p in sorted(_APP.rglob("*.py")):
        src = p.read_text(encoding="utf-8")
        if "LearnedMapping(" not in src:
            continue
        tree = ast.parse(src)
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for f in funcs:
            body = ast.unparse(f)
            constructs = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "LearnedMapping"
                for n in ast.walk(f))
            if not constructs:
                continue
            rel = str(p.relative_to(_APP)).replace(os.sep, "/")
            if (rel, f.name) in _INSERT_ALLOWED:
                continue
            if "include_deleted=True" not in body:
                bad.append(f"{rel}:{f.lineno} in {f.name}()")
    check("every insert path can see tombstones first", not bad,
          "\n        " + "\n        ".join(bad))


def test_get_is_never_used_on_this_model():
    """LearnedMapping.get() cannot be made tombstone-safe — it takes no
    include_deleted. Use find_one(LearnedMapping.id == ..., include_deleted=True)."""
    gets = [(f, fn, ln) for f, fn, ln, inc, _m in _call_sites()
            if ln and f and fn and _is_get(f, ln)]
    check("no LearnedMapping.get() call sites remain", not gets,
          "\n        " + "\n        ".join(f"{f}:{ln} in {fn}()" for f, fn, ln in gets))


def test_the_interactive_save_path_can_see_tombstones():
    """The specific regression, at its new address.

    Approving a mapping, adding a fixed value, saving a rule, uploading a
    workbook, typing a steer — every one of them now records through
    ``mapping_store.record_decision``, which finds the rows it is about to write
    over with ``find_rows_for_key``. So there is one place this guarantee has to
    hold, instead of six that each had to remember."""
    sites = [s for s in _call_sites() if s[1] == "find_rows_for_key"]
    check("the store queries the library", sites, "call site vanished")
    for f, fn, ln, inc, _m in sites:
        check(f"{f}:{ln} passes include_deleted", inc,
              "without it the tombstone is invisible and a deleted learning is "
              "re-inserted as a duplicate on the next approve")
    src = (_APP / "services" / "mapping_store.py").read_text(encoding="utf-8")
    body = src.split("async def record_decision(")[1].split("\nasync def ")[0]
    check("the writer asks for retired rows too", "include_deleted=True" in body,
          "record_decision must see the tombstone it is about to step over")


def test_the_auto_capture_path_can_see_tombstones_too():
    """Auto-capture after Generate Output goes through the same writer, so it
    inherits the same guarantee rather than repeating it."""
    src = (_APP / "services" / "learning_service.py").read_text(encoding="utf-8")
    body = src.split("async def _upsert_learned(")[1].split("\nasync def ")[0]
    check("auto-capture records through the store", "_upsert(" in body)
    check("and does not query the library itself",
          "LearnedMapping.find" not in body)


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
