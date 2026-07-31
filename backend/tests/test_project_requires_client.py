"""A project must name its client, and the analyst can create one on the spot.

Analyst, 31-Jul: "Make it mandatory to select the client while creating a project" —
"select or add new".

This is the last hole left by the decision made the same day, that everything an
analyst saves is stored as a CLIENT rule. If the client is the key the whole library
is filed and read under, then a project that does not name one files its rules
somewhere nobody will look for them. It used to fall back to the bootstrap "default"
client whenever the field was missing OR the id was malformed — which reads as
harmless and is not:

  * a correction made in a properly tagged project reached that project and was
    skipped on arriving at the untagged one, as a cross-tenant leak;
  * the untagged project's own captures were filed under "default", so they were
    invisible to every other project of the client they actually belonged to;
  * and nothing on any screen distinguished either case from "the tool is broken".

The fan-out no longer treats untagged as a foreign tenant, but leaving the hole open
only moves the problem: the rules still land under the wrong client. Asking once, at
creation, is cheaper than any of it.

"Add new" is part of the requirement, not a convenience. The alternative to an easy
"add new" is picking whatever is already in the list — so a hard-to-add client
produces mis-filed projects, which is the failure this is meant to prevent. And a
NAME matches an existing client case-insensitively before creating one, because
"NextPower" and "Nextpower" typed a week apart would otherwise become two tenants
whose libraries can never see each other: the same invisible split, reached from the
other direction.

Pure: stdlib. Drives the real resolver against an in-memory Client collection.
"""
import asyncio
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
        return
    raise AssertionError(f"{name} {detail}".strip())


class _Client:
    """A Client document. `id` is a plain string here; the resolver only compares
    and returns it, and PydanticObjectId parsing is exercised separately."""

    _items: list = []

    def __init__(self, name=None, **kw):
        self.name = name
        self.id = kw.pop("id", None) or f"client-{len(_Client._items) + 1}"
        for k, v in kw.items():
            setattr(self, k, v)

    async def insert(self):
        _Client._items.append(self)
        return self

    @classmethod
    def find_all(cls):
        class _Q:
            async def to_list(_self):
                return list(cls._items)
        return _Q()

    @classmethod
    async def get(cls, oid):
        return next((c for c in cls._items if str(c.id) == str(oid)), None)


class World:
    def __init__(self, clients=()):
        _Client._items = [_Client(name=n, id=f"client-{i}")
                          for i, n in enumerate(clients, start=1)]

    def __enter__(self):
        self._saved = sys.modules.get("app.models.client")
        m = types.ModuleType("app.models.client")
        m.Client = _Client
        sys.modules["app.models.client"] = m
        # The resolver parses an id with PydanticObjectId; a plain string is not one,
        # so for these tests it is swapped for str. The malformed-id case has its own
        # test against the real parser.
        from app.services import client_service as cs
        self._cs, self._oid = cs, cs.PydanticObjectId
        cs.PydanticObjectId = str
        return self

    def __exit__(self, *exc):
        self._cs.PydanticObjectId = self._oid
        if self._saved is not None:
            sys.modules["app.models.client"] = self._saved
        else:
            sys.modules.pop("app.models.client", None)
        return False

    @property
    def names(self):
        return [c.name for c in _Client._items]


def resolve(raw, name):
    from app.services.client_service import resolve_client_for_project
    return asyncio.run(resolve_client_for_project(raw, name))


# ── 1. mandatory ────────────────────────────────────────────────────────────
def test_a_project_with_no_client_is_refused():
    """No silent fallback to the default tenant. The message says why, because
    "422" on its own teaches nobody what the field is for."""
    with World(["NextPower"]):
        try:
            resolve(None, None)
        except ValueError as exc:
            check("it is refused", True)
            check("and the reason names the consequence",
                  "stored against its client" in str(exc), f"got {exc}")
        else:
            raise AssertionError("a project was created with no client")


def test_blank_strings_do_not_count_as_an_answer():
    """The form sends "" rather than null when a field is untouched, so a check that
    only tests for None passes the empty case straight through."""
    with World(["NextPower"]):
        for raw, name in [("", ""), ("   ", "   "), (None, "  ")]:
            try:
                resolve(raw, name)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{raw!r}/{name!r} was accepted as a client")
        check("every blank shape is refused", True)


# ── 2. select ───────────────────────────────────────────────────────────────
def test_an_existing_client_is_used_as_is():
    with World(["NextPower", "Acme"]) as w:
        got = resolve("client-2", None)
        check("the picked client is returned", str(got) == "client-2", f"got {got}")
        check("and nothing was created", len(w.names) == 2, f"got {w.names}")


def test_an_id_that_no_longer_exists_is_refused_not_defaulted():
    """A stale id in a browser tab must not quietly become the default tenant —
    that is the original bug wearing a different hat."""
    with World(["NextPower"]):
        try:
            resolve("client-99", None)
        except ValueError as exc:
            check("it is refused", "no longer exists" in str(exc), f"got {exc}")
        else:
            raise AssertionError("a dead client id was accepted")


# ── 3. add new ──────────────────────────────────────────────────────────────
def test_a_new_name_creates_the_client():
    with World(["NextPower"]) as w:
        got = resolve(None, "Contoso")
        check("a client was created", "Contoso" in w.names, f"got {w.names}")
        check("and its id is returned",
              str(got) == str(next(c.id for c in _Client._items if c.name == "Contoso")))


def test_a_name_that_already_exists_is_matched_not_duplicated():
    """Case and stray spaces included. Two tenants with the same name is the same
    invisible split as an untagged project, arrived at from the other direction:
    each holds half the rules and neither can see the other's."""
    with World(["NextPower"]) as w:
        for typed in ("NextPower", "nextpower", "  NEXTPOWER  "):
            got = resolve(None, typed)
            check(f"{typed!r} matched the existing client", str(got) == "client-1",
                  f"got {got}")
        check("and no duplicate was created", w.names == ["NextPower"], f"got {w.names}")


def test_an_id_wins_over_a_name_when_both_are_sent():
    """The picker sends the id; the free-text box sends the name. A form that sends
    both must not create a client as a side effect of an explicit selection."""
    with World(["NextPower", "Acme"]) as w:
        got = resolve("client-2", "Contoso")
        check("the selected id is used", str(got) == "client-2", f"got {got}")
        check("and no client was created", len(w.names) == 2, f"got {w.names}")


# ── 4. the router surfaces it as a 422, not a 500 ───────────────────────────
def test_the_router_turns_the_refusal_into_a_422():
    src = (Path(__file__).resolve().parent.parent / "app" / "routers"
           / "projects.py").read_text(encoding="utf-8")
    check("it calls the shared resolver", "resolve_client_for_project(" in src)
    check("and reports the reason to the analyst",
          "raise HTTPException(422, str(exc))" in src,
          "a ValueError here would surface as an unexplained 500")
    check("the old silent fallback is gone",
          "resolved_cid = await default_client_id()" not in src,
          "create_project still defaults the tenant when none is given")


def _all():
    return [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]


if __name__ == "__main__":
    for name, fn in _all():
        print(name)
        fn()
    print(f"\n{len(_all())} tests passed")
