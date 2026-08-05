"""The Users screen: the control that makes the role split apply to anybody.

WHAT THIS IS GUARDING
---------------------
``test_role_access.py`` already holds the ENFORCEMENT in place — which section
each router is mounted under, and that a Normal user is refused. It could pass
completely while the feature did nothing for anyone, because until this screen
existed there was no way to make somebody a Normal user other than editing Mongo
by hand. Every account was an administrator, so the guard had never once said no
to a real person.

That is the failure this codebase repeats (CODEBASE_GUIDE §7.1): data that says
something and code that never asks. So the sweeps at the bottom assert the
CALLERS — the router is mounted, the route is guarded, the page is routed, the
page calls the API — not merely that each file exists.

The behaviour tests run the real endpoint functions against a substituted
``_all_users``. No Mongo: a security test that only runs with infrastructure is
a security test that stops being run.
"""
import ast
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from beanie import PydanticObjectId                              # noqa: E402
from fastapi import HTTPException                                # noqa: E402

from app.routers import users as users_router                    # noqa: E402
from app.services.access_control import ADMIN, is_admin          # noqa: E402
from app.services.auth_service import (                          # noqa: E402
    NO_PASSWORD, hash_password, password_is_set, verify_password,
)

_BACKEND = Path(__file__).resolve().parent.parent
_FRONTEND = _BACKEND.parent / "frontend" / "src"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# ---------------------------------------------------------------------------
# Fakes. A User document stands in as a namespace with an async save, which is
# all the endpoints touch.
# ---------------------------------------------------------------------------
class FakeUser(SimpleNamespace):
    def __init__(self, name, email, role, password_hash=NO_PASSWORD, _id=None):
        super().__init__(
            name=name, email=email, role=role,
            password_hash=password_hash, created_at=None,
            id=_id or PydanticObjectId(),
        )
        self.saved = 0

    async def save(self):
        self.saved += 1


def _with_users(users):
    """Substitute the one place the router reads the collection."""
    async def _all():
        return list(users)
    users_router._all_users = _all  # noqa: SLF001


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# An invited account cannot sign in. This is the half of the feature that would
# be silently catastrophic to get wrong, so it is tested first and hardest.
# ---------------------------------------------------------------------------
def test_an_unset_password_never_verifies():
    """The sentinel, the empty string, whitespace and None all mean 'no password
    has been set'. Any of them matching ANY input is a public account."""
    for hashed in (NO_PASSWORD, "", "   ", None):
        check(f"{hashed!r} rejects a guess", verify_password("hunter2", hashed) is False)
        check(f"{hashed!r} rejects an empty guess", verify_password("", hashed) is False)
        check(f"{hashed!r} rejects its own value", verify_password(str(hashed), hashed) is False)


def test_password_is_set_agrees_with_verify():
    """One predicate for 'is this account usable'. The screen reports it and the
    login route obeys it; two answers here is a row that says ready beside a
    login that says no."""
    real = hash_password("hunter2")
    check("a real hash counts as set", password_is_set(real) is True)
    check("and verifies", verify_password("hunter2", real) is True)
    check("the sentinel does not", password_is_set(NO_PASSWORD) is False)
    check("nor does blank", password_is_set("") is False)
    check("nor does None", password_is_set(None) is False)


def test_the_refusal_does_not_depend_on_passlib_raising():
    """The unset check must come BEFORE the try/except, or the guarantee is only
    that today's passlib happens to raise on today's sentinel."""
    src = (_BACKEND / "app" / "services" / "auth_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "verify_password")
    first = fn.body[0]
    check("the first statement is the unset guard", isinstance(first, ast.If))
    names = {n.id for n in ast.walk(first) if isinstance(n, ast.Name)}
    check("and it calls password_is_set", "password_is_set" in names, f"got {names}")
    check("before any try", not isinstance(fn.body[0], ast.Try))


# ---------------------------------------------------------------------------
# Roles in, roles out
# ---------------------------------------------------------------------------
def test_only_the_two_real_roles_are_accepted():
    check("admin", users_router._canonical_role("admin") == "admin")
    check("normal", users_router._canonical_role("normal") == "normal")
    check("padded and shouted is normalised", users_router._canonical_role("  ADMIN ") == "admin")
    for bad in ("administrator", "superuser", "", "  ", "read-only"):
        try:
            users_router._canonical_role(bad)
        except HTTPException as e:
            check(f"{bad!r} refused", e.status_code == 422)
            check("and the valid roles are named", "admin" in str(e.detail))
            continue
        raise AssertionError(f"{bad!r} was stored as a role")


def test_an_unknown_role_is_refused_rather_than_stored():
    """The dangerous direction. An unrecognised role is not an administrator, so
    storing a typo silently REMOVES access and reports success."""
    target = FakeUser("T", "t@x.com", "admin")
    me = FakeUser("Me", "me@x.com", "admin")
    other = FakeUser("O", "o@x.com", "admin")
    _with_users([me, target, other])
    try:
        _run(users_router.set_role(str(target.id), users_router.RoleUpdate(role="Adminstrator"), me))
    except HTTPException as e:
        check("typo refused", e.status_code == 422)
        check("and nothing was written", target.saved == 0)
        check("and the role is untouched", target.role == "admin")
        return
    raise AssertionError("a misspelled role was stored")


def test_who_counts_as_an_administrator_is_the_guards_own_predicate():
    """Not a Mongo equality test on the string. ``role`` arrives from documents
    written before this screen existed and nothing normalised them; a query for
    role == "admin" misses " Admin " while the guard goes on admitting it."""
    padded = FakeUser("P", "p@x.com", "  ADMIN ")
    normal = FakeUser("N", "n@x.com", "normal")
    admins = users_router._admins([padded, normal])
    check("the padded admin is counted", admins == [padded])
    check("and the guard agrees", is_admin(padded) is True)
    src = (_BACKEND / "app" / "routers" / "users.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_admins")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    check("_admins calls is_admin", "is_admin" in names, f"got {names}")


# ---------------------------------------------------------------------------
# The two ways this screen could lock everybody out
# ---------------------------------------------------------------------------
def test_the_last_administrator_cannot_be_demoted():
    """There would be no account left that can reach this screen, and the only
    repair is the hand-edited Mongo document the screen exists to retire."""
    me = FakeUser("Me", "me@x.com", "admin")
    only = FakeUser("Only", "only@x.com", "admin")
    plain = FakeUser("N", "n@x.com", "normal")
    # `me` is an admin too, so demoting `only` is legal here.
    _with_users([me, only, plain])
    _run(users_router.set_role(str(only.id), users_router.RoleUpdate(role="normal"), me))
    check("demotion works while another admin remains", only.role == "normal")

    # Now `me` is the last one, and `me` cannot be demoted by anyone.
    boss = FakeUser("Boss", "boss@x.com", "admin")
    lone = FakeUser("Lone", "lone@x.com", "admin")
    _with_users([lone, plain])          # only `lone` is an administrator
    try:
        _run(users_router.set_role(str(lone.id), users_router.RoleUpdate(role="normal"), boss))
    except HTTPException as e:
        check("the last admin is protected", e.status_code == 409)
        check("and told what to do instead", "administrator" in str(e.detail).lower())
        check("and nothing was written", lone.saved == 0)
        check("and the role is untouched", lone.role == "admin")
        return
    raise AssertionError("the last administrator was demoted")


def test_the_last_administrator_may_still_be_confirmed_as_an_administrator():
    """The guard is about REMOVING the last one. Setting it to the role it
    already has must not 409 — a rule that refuses a no-op reads as broken."""
    lone = FakeUser("Lone", "lone@x.com", "admin")
    boss = FakeUser("Boss", "boss@x.com", "admin")
    _with_users([lone])
    row = _run(users_router.set_role(str(lone.id), users_router.RoleUpdate(role="admin"), boss))
    check("no-op allowed", row["role"] == "admin")
    check("and nothing was written", lone.saved == 0)


def test_you_cannot_change_your_own_role():
    """Recoverable in principle — another administrator exists by the rule above.
    But 'another admin row exists' and 'somebody has that password' are different
    facts, and a stale colleague's account satisfies only the first."""
    me = FakeUser("Me", "me@x.com", "admin")
    other = FakeUser("O", "o@x.com", "admin")
    _with_users([me, other])
    try:
        _run(users_router.set_role(str(me.id), users_router.RoleUpdate(role="normal"), me))
    except HTTPException as e:
        check("self change refused", e.status_code == 409)
        check("and it says who can", "administrator" in str(e.detail).lower())
        check("and nothing was written", me.saved == 0)
        return
    raise AssertionError("an administrator demoted themselves")


def test_a_promotion_and_a_demotion_both_take():
    me = FakeUser("Me", "me@x.com", "admin")
    other = FakeUser("O", "o@x.com", "admin")
    plain = FakeUser("N", "n@x.com", "normal")
    _with_users([me, other, plain])

    row = _run(users_router.set_role(str(plain.id), users_router.RoleUpdate(role="admin"), me))
    check("promoted", plain.role == "admin" and row["role"] == "admin")
    check("and saved", plain.saved == 1)

    row = _run(users_router.set_role(str(other.id), users_router.RoleUpdate(role="normal"), me))
    check("demoted", other.role == "normal" and row["role"] == "normal")


def test_a_missing_or_malformed_id_is_a_404_not_a_500():
    me = FakeUser("Me", "me@x.com", "admin")
    _with_users([me, FakeUser("O", "o@x.com", "admin")])
    for bad in ("not-an-objectid", "", str(PydanticObjectId())):
        try:
            _run(users_router.set_role(bad, users_router.RoleUpdate(role="normal"), me))
        except HTTPException as e:
            check(f"{bad!r} is 404", e.status_code == 404, f"got {e.status_code}")
            continue
        raise AssertionError(f"{bad!r} did not 404")


# ---------------------------------------------------------------------------
# Inviting
# ---------------------------------------------------------------------------
def test_an_invite_never_carries_a_password():
    me = FakeUser("Me", "me@x.com", "admin")
    _with_users([me])
    created = {}

    class _Captured(FakeUser):
        def __init__(self, **kw):
            super().__init__(kw["name"], kw["email"], kw["role"], kw["password_hash"])
            created.update(kw)

        async def insert(self):
            return self

    users_router.User = _Captured  # noqa: SLF001
    try:
        row = _run(users_router.invite_user(
            users_router.InviteRequest(name="Priya", email="Priya@X.com", role="normal"), me))
    finally:
        from app.models.user import User as _RealUser
        users_router.User = _RealUser

    check("the hash is the sentinel", created.get("password_hash") == NO_PASSWORD)
    check("which cannot verify", verify_password("anything", created["password_hash"]) is False)
    check("the row says so", row["password_set"] is False)
    check("the email is normalised", created.get("email") == "priya@x.com")
    check("the role is normalised", created.get("role") == "normal")


def test_an_email_that_already_exists_in_another_casing_is_refused():
    """Two rows differing only in case is the 04-Aug bug in a new place: sign-in
    matches whichever row Mongo returns first, and the role an administrator set
    could be on the other one."""
    me = FakeUser("Me", "me@x.com", "admin")
    _with_users([me, FakeUser("P", "priya@x.com", "normal")])
    try:
        _run(users_router.invite_user(
            users_router.InviteRequest(name="Priya", email="PRIYA@x.com", role="admin"), me))
    except HTTPException as e:
        check("duplicate refused", e.status_code == 409)
        check("and names the address", "priya@x.com" in str(e.detail))
        return
    raise AssertionError("a case-variant duplicate account was created")


def test_a_blank_name_is_refused():
    me = FakeUser("Me", "me@x.com", "admin")
    _with_users([me])
    try:
        _run(users_router.invite_user(
            users_router.InviteRequest(name="   ", email="x@y.com", role="normal"), me))
    except HTTPException as e:
        check("blank name refused", e.status_code == 422)
        return
    raise AssertionError("an account with no name was created")


# ---------------------------------------------------------------------------
# What leaves the building
# ---------------------------------------------------------------------------
def test_no_response_ever_carries_a_password_hash():
    """Not even in a field nothing renders. 'The response carried it and the UI
    ignored it' is one refactor away from being untrue, and the refactor does not
    know it is doing anything dangerous."""
    me = FakeUser("Me", "me@x.com", "admin", password_hash=hash_password("s3cret"))
    other = FakeUser("O", "o@x.com", "normal")
    _with_users([me, other])
    rows = _run(users_router.list_users(me))
    check("both rows returned", len(rows) == 2)
    for r in rows:
        check("no hash field", "password_hash" not in r, f"got {sorted(r)}")
        check("and no hash value", "s3cret" not in str(r) and not str(r).startswith("$2"))
    check("the schema has no hash field", "password_hash" not in users_router.UserRow.model_fields)
    check("it reports whether one is set",
          {r["email"]: r["password_set"] for r in rows} == {"me@x.com": True, "o@x.com": False})
    check("and marks the caller", [r["is_self"] for r in rows] == [True, False])


# ---------------------------------------------------------------------------
# The sweeps: is any of this actually reachable?
# ---------------------------------------------------------------------------
def test_the_router_is_mounted_and_it_is_admin_only():
    """If this router were OPEN or READ_ONLY, a Normal user could promote
    themselves and the entire split becomes decoration."""
    tree = ast.parse((_BACKEND / "app" / "main.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id.lstrip("_") == "mount":
            kw = {k.arg: k.value for k in node.keywords}
            name = kw.get("name")
            if isinstance(name, ast.Constant) and name.value == "users":
                sec = kw.get("section")
                found.append(sec.id if isinstance(sec, ast.Name) else None)
    check("users is mounted exactly once", len(found) == 1, f"got {found}")
    check("under ADMIN", found[0] == "ADMIN", f"got {found[0]!r}")
    check("and ADMIN is the admin section", ADMIN == "admin")


def _strip_ts_comments(src: str) -> str:
    """Comments break source-reading tests — three failed in one session because
    a comment quoted the expression being counted. Same scanner as
    test_role_access; duplicated rather than imported so this file stays
    standalone and stdlib-only."""
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


def test_the_screen_is_routed_and_guarded():
    app = _fe("App.tsx")
    check("UsersPage is imported", "UsersPage" in app)
    idx = app.find('path="users"')
    check("there is a users route", idx >= 0)
    line = app[idx:app.find("\n", idx)]
    check("wrapped in AdminRoute", "<AdminRoute>" in line, f"got {line.strip()[:90]}")
    check("and it renders the page", "UsersPage" in line)


def test_the_nav_entry_exists_and_is_hidden_from_a_normal_user():
    side = _fe("components/layout/Sidebar.tsx")
    acc = _fe("lib/access.ts")
    check("the group exists", 'label: "Administration"' in side)
    check("with a link to the screen", 'to: "/users"' in side)
    check("the group is admin-only", '"Administration"' in acc)
    check("and so is the path", '"users"' in acc)


def test_the_page_actually_calls_the_api():
    """The §7.1 test. A screen that renders and never asks the backend anything
    is the same failure as a spec no code reads — it just fails in front of
    somebody instead of quietly."""
    page = _fe("pages/UsersPage.tsx")
    for call in ("UsersApi.list", "UsersApi.invite", "UsersApi.setRole"):
        check(f"the page calls {call}", call in page)
    api = _fe("api/index.ts")
    check("UsersApi is defined", "export const UsersApi" in api)
    for verb, path in (("get", '"/users"'), ("post", '"/users"'), ("patch", "`/users/${id}`")):
        check(f"{verb} {path} is wired", f"api.{verb}" in api and path in api)


def test_the_screen_does_not_offer_a_password_field():
    """Creating credentials stays a human action. A password input here would be
    a promise the API does not keep."""
    page = _fe("pages/UsersPage.tsx")
    lowered = page.lower()
    check("no password input", 'type="password"' not in lowered)
    check("no password state", "setpassword" not in lowered.replace(" ", ""))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall user management checks passed")
