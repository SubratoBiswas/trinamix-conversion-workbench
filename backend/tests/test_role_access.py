"""Admin / Normal roles: the control is on the ROUTE, not on the nav link.

Hiding a sidebar entry is not access control. Every screen is backed by an API
route, so a Normal user who cannot see "Gold Standards" can still call
``/api/gold/...`` if they know the path — and the FBDI templates, the learning
library and the audit trail all sit behind sections this split restricts.

The model, and why it is not simply "restricted section, restricted router":

    The open screens read from the restricted ones constantly. Mapping Review,
    Recommendations, Project Overview and Conversion Detail all call the
    datasets, FBDI and learning APIs; Migration Monitor reads governance. Locking
    those routers whole would have handed Normal users an app that 403s on the
    screens they are supposed to keep. So on the shared libraries the split is by
    METHOD: read the library, do not change it.

These tests are the sweep the handoff asks for. They walk ``main.py`` as a syntax
tree rather than as text, so a comment cannot satisfy them and a new router
cannot be mounted without being classified.

Pure: stdlib only. No database, no app import — importing ``main`` would pull in
Beanie and Mongo, and a security test that can only run with infrastructure is a
security test that stops being run.
"""
import ast
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException                                # noqa: E402
from app.services.access_control import (                        # noqa: E402
    ADMIN, OPEN, PUBLIC, READ_ONLY, SAFE_METHODS, SECTIONS, is_admin,
    mount, require_admin, require_admin_to_write,
)

_BACKEND = Path(__file__).resolve().parent.parent
_MAIN = _BACKEND / "app" / "main.py"
_ROUTERS = _BACKEND / "app" / "routers"

ADMIN_USER = SimpleNamespace(email="a@x.com", role="admin")
NORMAL_USER = SimpleNamespace(email="n@x.com", role="normal")

# The sections these routers must be in. Pinned so the decision cannot drift
# quietly: moving datasets to ADMIN breaks Mapping Review for every Normal user,
# and moving governance to OPEN lets one sign off a cutover.
EXPECTED = {
    "auth": PUBLIC,
    "clients": OPEN, "projects": OPEN, "conversions": OPEN, "mapping": OPEN,
    "mapping_proposals": OPEN, "manual_map": OPEN, "quality": OPEN,
    "operations": OPEN, "cutover": OPEN, "cutover_slice6": OPEN, "fusion": OPEN,
    "copilot": OPEN,
    "datasets": READ_ONLY, "fbdi": READ_ONLY, "fbdi_seed": READ_ONLY,
    "gold": READ_ONLY, "learned": READ_ONLY, "source_systems": READ_ONLY,
    "fusion_modules": READ_ONLY, "audit_events": READ_ONLY, "governance": READ_ONLY,
    "audit": ADMIN, "coa": ADMIN, "dq_rules": ADMIN, "settings": ADMIN,
    "discovery": ADMIN, "source_connections": ADMIN,
}


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# main.py names the sections by their constant, so the tree carries the
# IDENTIFIER and not its value. Resolved through the module's own constants
# rather than a second literal list, so renaming a section cannot leave this
# sweep quietly matching nothing.
_BY_IDENT = {"PUBLIC": PUBLIC, "OPEN": OPEN, "READ_ONLY": READ_ONLY, "ADMIN": ADMIN}


def _mount_calls() -> list:
    """Every _mount(...) call in main.py, as (name, section). AST, not text — a
    comment quoting a mount line must not be able to satisfy this sweep."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id.lstrip("_") == "mount"):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        section = kw.get("section")
        name = kw.get("name")
        out.append((
            name.value if isinstance(name, ast.Constant) else None,
            _BY_IDENT.get(section.id) if isinstance(section, ast.Name)
            else (section.value if isinstance(section, ast.Constant) else None),
        ))
    return out


def test_nothing_registers_a_router_behind_mounts_back():
    """A bare app.include_router bypasses the guard entirely. There must not be
    one — that is the single line that would reopen everything."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    bare = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router"):
            bare.append(node.lineno)
    check("no direct include_router in main", not bare, f"at lines {bare}")


def test_every_mount_names_a_section_and_a_router():
    calls = _mount_calls()
    check("main mounts routers", len(calls) >= 25, f"found {len(calls)}")
    for name, section in calls:
        check(f"{name or '?'} names its section", section in SECTIONS,
              f"got {section!r}")
        check("and names itself", bool(name))


def test_every_router_module_on_disk_is_classified():
    """The sweep the handoff asks for. A new router file that nobody mounted is
    dead code; one that is mounted without a section cannot start the app. This
    catches the third case — a router mounted under a name nobody recognises."""
    on_disk = {p.stem for p in _ROUTERS.glob("*.py") if p.stem != "__init__"}
    mounted = {(n or "").split(".")[0] for n, _s in _mount_calls()}
    missing = sorted(on_disk - mounted)
    check("every router module is mounted", not missing, f"unmounted: {missing}")
    unknown = sorted(mounted - on_disk)
    check("and every mount names a real module", not unknown, f"unknown: {unknown}")


def test_the_section_of_each_router_is_what_was_decided():
    got = {}
    for name, section in _mount_calls():
        got.setdefault((name or "").split(".")[0], section)
    for mod, want in EXPECTED.items():
        check(f"{mod} is {want}", got.get(mod) == want, f"got {got.get(mod)!r}")


def test_the_restricted_sections_are_actually_restricted():
    """Datasets, FBDI Library, AI Engine and Governance are the four the request
    names. None of them may be OPEN."""
    got = {(n or "").split(".")[0]: s for n, s in _mount_calls()}
    for mod in ("datasets", "fbdi", "fbdi_seed", "gold", "learned", "dq_rules",
                "governance", "audit", "audit_events", "settings"):
        check(f"{mod} is not open to everyone", got.get(mod) != OPEN,
              f"got {got.get(mod)!r}")


def test_credentials_are_admin_only_outright():
    """Source connections hold credentials. Read access to a connection is not a
    lesser thing than write access here, so this one is not READ_ONLY."""
    got = {(n or "").split(".")[0]: s for n, s in _mount_calls()}
    for mod in ("source_connections", "discovery"):
        check(f"{mod} is admin only", got.get(mod) == ADMIN, f"got {got.get(mod)!r}")


def test_mount_refuses_an_unknown_section():
    """Fail loud. A typo in a section name must stop the app, not fall through to
    something permissive."""
    class _App:
        def include_router(self, *a, **k):
            raise AssertionError("should never be reached")
    try:
        mount(_App(), object(), section="everyone", name="x")
    except ValueError as e:
        check("unknown section raises", "everyone" in str(e))
        check("and lists the valid ones", "admin" in str(e))
        return
    raise AssertionError("an unknown section was accepted")


def test_mount_refuses_a_router_with_no_section_at_all():
    """The section is keyword-only with no default, so forgetting it is a
    TypeError at import. A default of OPEN is how an endpoint becomes public
    because somebody was in a hurry."""
    class _App:
        def include_router(self, *a, **k):
            raise AssertionError("should never be reached")
    try:
        mount(_App(), object())
    except TypeError:
        check("a missing section is a TypeError", True)
        return
    raise AssertionError("a router mounted with no section")


def test_require_admin_lets_an_admin_through_and_stops_a_normal_user():
    check("admin passes", asyncio.run(require_admin(ADMIN_USER)) is ADMIN_USER)
    try:
        asyncio.run(require_admin(NORMAL_USER))
    except HTTPException as e:
        check("normal user is refused", e.status_code == 403, f"got {e.status_code}")
        check("and is told why", "administrator" in str(e.detail).lower())
        return
    raise AssertionError("a normal user reached an admin route")


def test_403_is_not_401():
    """They are different questions and the frontend must tell them apart. 401
    means sign in again; 403 means this account will never be allowed and
    re-authenticating is a waste of the user's time."""
    try:
        asyncio.run(require_admin(NORMAL_USER))
    except HTTPException as e:
        check("not a 401", e.status_code != 401, f"got {e.status_code}")


def test_a_normal_user_may_read_a_shared_library():
    for method in ("GET", "HEAD", "OPTIONS"):
        req = SimpleNamespace(method=method)
        got = asyncio.run(require_admin_to_write(req, NORMAL_USER))
        check(f"{method} is allowed", got is NORMAL_USER)


def test_a_normal_user_may_not_change_one():
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        req = SimpleNamespace(method=method)
        try:
            asyncio.run(require_admin_to_write(req, NORMAL_USER))
        except HTTPException as e:
            check(f"{method} is refused", e.status_code == 403)
            continue
        raise AssertionError(f"{method} was allowed for a normal user")


def test_an_admin_may_do_both():
    for method in ("GET", "POST", "DELETE"):
        req = SimpleNamespace(method=method)
        check(f"admin {method}", asyncio.run(
            require_admin_to_write(req, ADMIN_USER)) is ADMIN_USER)


def test_a_lowercase_method_is_still_a_write():
    """The method comes off the request. Never trust its casing to decide whether
    something is allowed to change data."""
    try:
        asyncio.run(require_admin_to_write(SimpleNamespace(method="post"), NORMAL_USER))
    except HTTPException as e:
        check("lowercase post is refused", e.status_code == 403)
        return
    raise AssertionError("a lowercase write slipped through")


def test_preflight_is_never_refused():
    """A 403 on the CORS preflight reads to the browser as a network failure, not
    as a permission error, which is a very expensive hour to spend."""
    check("OPTIONS is safe", "OPTIONS" in SAFE_METHODS)
    check("HEAD is safe", "HEAD" in SAFE_METHODS)
    check("POST is not", "POST" not in SAFE_METHODS)


def test_only_the_exact_role_counts_as_admin():
    check("admin", is_admin(SimpleNamespace(role="admin")) is True)
    check("padded and shouted still admin",
          is_admin(SimpleNamespace(role="  ADMIN ")) is True)
    check("normal", is_admin(SimpleNamespace(role="normal")) is False)
    check("administrator is not the role name",
          is_admin(SimpleNamespace(role="administrator")) is False)
    check("empty", is_admin(SimpleNamespace(role="")) is False)
    check("missing attribute", is_admin(SimpleNamespace()) is False)
    check("no user at all", is_admin(None) is False)


def test_the_guard_reads_no_data_file():
    """This must not become a bundled JSON spec like the column orders.

    Those accessors swallow a read error and return an empty mapping, because a
    missing column order should disable a reorder rather than take the deliverable
    down. The same shape here degrades to "everybody is an administrator" —
    a control whose failure mode is silent and open.
    """
    src = (_BACKEND / "app" / "services" / "access_control.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            check("no file reads in the guard", node.attr not in ("read_text", "read_bytes", "open"),
                  f"found {node.attr}")
        if isinstance(node, ast.Name):
            check("no json in the guard", node.id != "json")


def test_the_role_defaults_to_admin_on_the_model():
    """Existing installs have no role on any user document. The default has to be
    admin or the people already using the app are locked out by the migration —
    a permission system whose first act is to lock out its owner does not get
    turned on a second time."""
    src = (_BACKEND / "app" / "models" / "user.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "role":
            found = True
            check("role defaults to admin",
                  isinstance(node.value, ast.Constant) and node.value.value == "admin",
                  f"got {getattr(node.value, 'value', None)!r}")
    check("the model carries a role", found)


# ---------------------------------------------------------------------------
# The frontend half. There is no JS runtime here, so these read the source — the
# same approach as test_hook_order and test_download_timeouts. Comments are
# stripped first: three tests failed in one session because a comment quoted the
# expression the test was counting, and these files are heavily commented.
# ---------------------------------------------------------------------------
_FRONTEND = _BACKEND.parent / "frontend" / "src"


def _strip_ts_comments(src: str) -> str:
    """Remove // and /* */ comments without touching string or template literals.

    A regex would have eaten the '//' in a URL and the '/*' in a regex literal.
    Scanning character by character is duller and correct.
    """
    out, i, n = [], 0, len(src)
    quote = None
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


def _fe(rel: str) -> str:
    return _strip_ts_comments((_FRONTEND / rel).read_text(encoding="utf-8"))


def test_the_sidebar_hides_the_restricted_sections():
    src = _fe("components/layout/Sidebar.tsx")
    check("it imports the shared list", "ADMIN_ONLY_SECTIONS" in src)
    check("it filters on the role", "isAdmin(user)" in src)
    check("and renders the FILTERED list",
          "groups.map((g)" in src, "still rendering the unfiltered GROUPS")
    check("the unfiltered list is not rendered", "GROUPS.map((g)" not in src)


def test_the_four_restricted_sections_are_the_sidebar_group_labels():
    """A typo here hides nothing and looks like it works. Match the strings in
    access.ts against the group labels the sidebar actually defines."""
    side = _fe("components/layout/Sidebar.tsx")
    acc = _fe("lib/access.ts")
    for label in ("Datasets", "FBDI Library", "AI Engine", "Governance"):
        check(f"{label} is a real group", f'label: "{label}"' in side)
        check(f"{label} is restricted", f'"{label}"' in acc)
    for label in ("Conversion Workbench", "Load Management", "Overview"):
        check(f"{label} stays visible", f'"{label}"' not in acc)


def test_every_restricted_route_is_wrapped():
    """Nav filtering alone leaves the URL open, and a pasted link is exactly how
    somebody finds that. Each restricted path must be wrapped in App.tsx."""
    app = _fe("App.tsx")
    acc = _fe("lib/access.ts")
    paths = [ln.split('"')[1] for ln in acc.splitlines()
             if ln.strip().startswith('"') and ln.strip().endswith('",')]
    check("the path list was read", len(paths) >= 15, f"got {len(paths)}")
    for p in paths:
        if p in ("Datasets", "FBDI Library", "AI Engine", "Governance"):
            continue
        idx = app.find(f'path="{p}"')
        check(f"{p} is a route", idx >= 0)
        line = app[idx:app.find("\n", idx)]
        check(f"{p} is admin-guarded", "<AdminRoute>" in line, f"got {line.strip()[:90]}")


def test_the_open_screens_are_not_wrapped():
    """The other half of the same guarantee. Wrapping one of these would lock a
    Normal user out of the workbench they are supposed to keep."""
    app = _fe("App.tsx")
    for p in ("conversions", "projects", "mappings", "recommendations", "output",
              "load", "dependencies", "workflows", "clients", "cutover"):
        idx = app.find(f'path="{p}"')
        check(f"{p} is a route", idx >= 0)
        line = app[idx:app.find("\n", idx)]
        check(f"{p} stays open", "<AdminRoute>" not in line, f"got {line.strip()[:90]}")


def test_the_guard_shows_a_403_rather_than_bouncing_home():
    """A silent redirect is indistinguishable from a broken link, so the user
    tries again. Same failure as the blank panel that could not say why."""
    app = _fe("App.tsx")
    idx = app.find("const AdminRoute")
    check("AdminRoute exists", idx >= 0)
    body = app[idx:idx + 400]
    check("it renders the 403 page", "<ForbiddenPage />" in body)
    check("it does not redirect", "Navigate" not in body)


def test_the_frontend_says_it_is_not_the_control():
    """The one belief that makes this dangerous is thinking the nav filter is the
    security. Both files have to say otherwise, in the source, where the next
    person edits them."""
    for rel in ("lib/access.ts", "components/layout/Sidebar.tsx"):
        raw = (_FRONTEND / rel).read_text(encoding="utf-8").lower()
        check(f"{rel} names the real control",
              "not access control" in raw or "is not what keeps them out" in raw)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall role access checks passed")
