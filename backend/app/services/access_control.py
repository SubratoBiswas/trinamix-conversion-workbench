"""Role-based access control: which signed-in users may call which API route.

DELIBERATELY A PYTHON MODULE AND NOT A JSON DATA FILE.

Every other spec in this codebase is bundled data read through an accessor that
degrades to a no-op when the file is missing — see ``supplier_fbdi_layout``,
where a missing column order disables the reorder rather than raising. That is
right for a column order and catastrophic here, because the degraded state of an
access control is "everybody is an administrator". This module has no file to
lose and no failure mode that opens a door.

THE THING THIS EXISTS TO PREVENT
--------------------------------
Hiding a nav link is not access control. Every screen in the sidebar is backed by
an API route; a Normal user who cannot see "Gold Standards" can still call
``/api/gold/...`` if they know the path. The control is here, on the route.

THE MODEL
---------
Three sections stay open to every signed-in user — Home, Conversion Workbench and
Load Management. The rest is administrator-only.

That rule could not be applied router by router, because the open screens read
from the restricted ones constantly: Mapping Review, Recommendations, Project
Overview and Conversion Detail all call the datasets, FBDI and learning APIs, and
Migration Monitor reads governance. Locking those routers whole would have left
Normal users an app that 403s on the screens they are supposed to keep.

So the split is by METHOD on those shared routers: a Normal user may READ the
libraries the workbench depends on and may not CHANGE them. Uploading a dataset,
editing a gold standard, seeding templates, approving a learning and signing off
a cutover are all administrator actions.

FAIL CLOSED, AND FAIL LOUD
--------------------------
``mount`` requires an explicit section at every call site and raises at import on
an unknown one, so a router cannot reach the app without someone saying where it
belongs. Forgetting is a startup crash, not a silently public endpoint.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.models.user import User
from app.services.auth_service import get_current_user

# Sections. Named rather than boolean because "is this admin-only" has three
# answers here, and a bool would have forced the middle one into the wrong bucket.
PUBLIC = "public"        # no blanket guard — the router does its own (login)
OPEN = "open"            # any signed-in user, every method
READ_ONLY = "read_only"  # any signed-in user may read; writing needs admin
ADMIN = "admin"          # administrators only, every method

SECTIONS = frozenset({PUBLIC, OPEN, READ_ONLY, ADMIN})

ADMIN_ROLE = "admin"

# HTTP methods that do not change state. HEAD and OPTIONS are here because the
# browser issues them on its own — a CORS preflight that 403s reads to the
# frontend as a network failure, not as a permission error, and that is a very
# expensive hour to spend.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_admin(user: User | None) -> bool:
    """Role test in ONE place. Compared case-insensitively against a stripped
    string because the role arrives from a JWT claim and from Mongo, and neither
    guarantees the spelling that was written."""
    return bool(user) and str(getattr(user, "role", "") or "").strip().lower() == ADMIN_ROLE


def _forbid(user: User) -> None:
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"This action requires an administrator. Signed in as "
        f"{getattr(user, 'email', 'unknown')} with role "
        f"{getattr(user, 'role', 'unknown') or 'none'}.",
    )


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Administrators only.

    401 when nobody is signed in (that comes from ``get_current_user``), 403 when
    somebody is and is not an administrator. The two are different questions and
    the frontend has to tell them apart: 401 means sign in again, 403 means this
    account will never be allowed and re-authenticating is a waste of time.
    """
    if not is_admin(user):
        _forbid(user)
    return user


async def require_admin_to_write(
    request: Request, user: User = Depends(get_current_user)
) -> User:
    """Read freely; change nothing.

    Applied to the libraries the open screens depend on. The method test is the
    whole rule, so a new endpoint on one of these routers is guarded the moment it
    is written — a POST is admin-only without anyone remembering to say so.
    """
    if request.method.upper() in SAFE_METHODS:
        return user
    if not is_admin(user):
        _forbid(user)
    return user


_GUARDS = {
    PUBLIC: None,
    OPEN: None,
    READ_ONLY: require_admin_to_write,
    ADMIN: require_admin,
}


def mount(app, router, *, section: str, name: str = "") -> None:
    """Register a router under a named section and attach that section's guard.

    The section is keyword-only and has no default. That is the point: a router
    added without one is a TypeError at import, and an unknown section is a
    ValueError at import, so the app cannot start with an unclassified route. The
    alternative — a default of OPEN — is how an endpoint ends up public because
    somebody was in a hurry.
    """
    if section not in SECTIONS:
        raise ValueError(
            f"Unknown access section {section!r} for router {name or router!r}. "
            f"Choose one of: {', '.join(sorted(SECTIONS))}."
        )
    guard = _GUARDS[section]
    app.include_router(router, dependencies=[Depends(guard)] if guard else [])
