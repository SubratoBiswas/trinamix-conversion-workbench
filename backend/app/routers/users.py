"""User management: list the accounts, invite one, change a role.

WHY THIS EXISTS
---------------
Enforcement shipped before this screen did. ``services/access_control.py`` has
decided for a while which sections a Normal user keeps and which are
administrator-only, and ``test_role_access.py`` holds that shape in place. What
was missing was any way to MAKE somebody a Normal user other than editing Mongo
by hand — so every account in every install is still an administrator and
nothing the guard says has ever applied to anyone. This router is the missing
half: a control that a person can actually reach.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not set passwords. Inviting creates the account and assigns the role;
the credential is created by a human, out of band. An invited account carries
``auth_service.NO_PASSWORD`` until then and cannot sign in at all — see
``password_is_set``, which the login route consults through ``verify_password``
and this screen reports through ``password_set``. One predicate, so the row in
the table cannot say "ready" while the login route says otherwise.

THE TWO WAYS THIS SCREEN COULD LOCK EVERYBODY OUT
-------------------------------------------------
Both are guarded here, and both are guarded by counting the SAME way the route
guard counts:

1. Demoting the last administrator. There would then be no account that can
   reach this screen to undo it, and the only remaining fix is editing Mongo by
   hand — which is the problem this screen was built to remove.
2. Demoting yourself. Recoverable in principle, because rule 1 guarantees
   another administrator exists — but "another admin account exists" and
   "somebody has that account's password" are different facts, and a stale
   colleague's row satisfies the first while helping with neither. So a role
   change always names somebody else, and the recovery path is a second person
   rather than a second row.

``_admins()`` filters with ``access_control.is_admin`` rather than querying
``role == "admin"``. That is the point: the set this screen protects and the set
the guard admits are computed by one function, so they cannot disagree about a
padded or shouted role string. Six copies of a rule is how a rule that was fixed
once stayed broken five times.

ROLE CHANGES TAKE EFFECT ON THE NEXT REQUEST, NOT THE NEXT SIGN-IN.
``get_current_user`` re-reads the user document per request and the guard tests
that, so the ``role`` claim baked into an already-issued JWT never decides
anything. Revoking access does not wait for a token to expire.
"""
from __future__ import annotations

from datetime import datetime

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app.models.user import User
from app.schemas.oid import ApiOut
from app.services.access_control import ADMIN_ROLE, is_admin
from app.services.auth_service import NO_PASSWORD, get_current_user, password_is_set

router = APIRouter(prefix="/api/users", tags=["users"])

NORMAL_ROLE = "normal"

# The roles a person may be given from this screen, in the order the screen
# offers them. Anything else is refused rather than stored: an unrecognised role
# is not an administrator, so a typo silently removes access instead of
# reporting a mistake.
ROLES: tuple[str, ...] = (ADMIN_ROLE, NORMAL_ROLE)


class UserRow(ApiOut):
    id: str
    name: str
    email: EmailStr
    role: str
    created_at: datetime | None = None
    #: False while the account is still waiting on a human to set a password.
    password_set: bool
    #: True for the account making the request — the screen greys its own row
    #: rather than offering a control the API will refuse.
    is_self: bool


class InviteRequest(BaseModel):
    name: str
    email: EmailStr
    role: str = NORMAL_ROLE


class RoleUpdate(BaseModel):
    role: str


def _row(user: User, *, current: User) -> dict:
    return {
        "id": str(user.id),
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "created_at": getattr(user, "created_at", None),
        "password_set": password_is_set(getattr(user, "password_hash", None)),
        "is_self": str(user.id) == str(current.id),
    }


def _canonical_role(raw: str) -> str:
    """Normalise on the way in, so Mongo only ever holds a spelling the guard
    recognises. ``is_admin`` already trims and lowercases when it reads, but
    storing "Admin" would leave a row that looks wrong to everyone reading the
    collection and right only to the one function that normalises."""
    role = (raw or "").strip().lower()
    if role not in ROLES:
        raise HTTPException(
            422,
            f"Unknown role {raw!r}. Choose one of: {', '.join(ROLES)}.",
        )
    return role


async def _all_users() -> list[User]:
    return await User.find_all().to_list()


def _admins(users: list[User]) -> list[User]:
    """Who can actually get in — decided by the route guard's own predicate.

    Not ``User.find(User.role == "admin")``. A Mongo equality test would miss
    " Admin " and this screen would then cheerfully demote the last account that
    can reach it, while the guard went on letting that account in until the
    moment it did not.
    """
    return [u for u in users if is_admin(u)]


async def _audit(actor: User, action: str, target: User, detail: dict) -> None:
    """Best effort, and that is a deliberate trade.

    A role change that cannot be written to the audit collection has still
    happened; failing the request would leave the caller believing it had not.
    The opposite risk — a change with no trail — is real, so the write is
    attempted on every path rather than only the interesting ones. If audit
    writes ever need to be a hard requirement, this is the one place to change.
    """
    try:
        from app.models.v10 import AuditEvent

        await AuditEvent(
            actor=getattr(actor, "email", "unknown"),
            action=action,
            entity_type="user",
            entity_id=str(target.id),
            detail=detail,
        ).insert()
    except Exception:  # noqa: BLE001
        pass


@router.get("", response_model=list[UserRow])
async def list_users(current: User = Depends(get_current_user)):
    """Every account, newest last. Never the password hash — not even in a field
    the frontend ignores, because "the response carried it and nothing rendered
    it" is one refactor away from being untrue."""
    users = sorted(await _all_users(), key=lambda u: getattr(u, "created_at", None) or datetime.min)
    return [_row(u, current=current) for u in users]


@router.post("", response_model=UserRow, status_code=201)
async def invite_user(payload: InviteRequest, current: User = Depends(get_current_user)):
    """Create the account and assign the role. No password is set here.

    The email is stored trimmed and lowercased, and an address that already
    exists in any casing is refused. Two rows differing only in case is the
    04-Aug bug in a new place: whoever signs in matches whichever row Mongo
    returns first, and the role an administrator set could be on the other one.
    """
    email = str(payload.email).strip().lower()
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(422, "A name is required.")
    role = _canonical_role(payload.role)

    existing = [u for u in await _all_users() if (u.email or "").strip().lower() == email]
    if existing:
        raise HTTPException(409, f"{email} already has an account.")

    user = User(name=name, email=email, role=role, password_hash=NO_PASSWORD)
    await user.insert()
    await _audit(current, "user.invited", user, {"email": email, "role": role})
    return _row(user, current=current)


@router.patch("/{user_id}", response_model=UserRow)
async def set_role(
    user_id: str, payload: RoleUpdate, current: User = Depends(get_current_user)
):
    """Change one account's role.

    Refuses, in this order: an unrecognised role, your own account, and the last
    remaining administrator. Each refusal says which rule it is and what to do
    instead — a 4xx that only says "forbidden" sends the person back to editing
    Mongo, which is what this screen exists to stop.
    """
    role = _canonical_role(payload.role)

    try:
        oid = PydanticObjectId(user_id)
    except Exception:  # noqa: BLE001
        raise HTTPException(404, "No such user.")

    users = await _all_users()
    target = next((u for u in users if u.id == oid), None)
    if target is None:
        raise HTTPException(404, "No such user.")

    if str(target.id) == str(current.id):
        raise HTTPException(
            409,
            "You cannot change your own role. Ask another administrator to do "
            "it — that way somebody who can still reach this screen is holding "
            "the other end.",
        )

    admins = _admins(users)
    if role != ADMIN_ROLE and is_admin(target) and len(admins) <= 1:
        raise HTTPException(
            409,
            f"{target.email} is the only administrator. Make somebody else an "
            f"administrator first, or there would be no account left that can "
            f"reach this screen.",
        )

    was = target.role
    if (was or "").strip().lower() == role:
        return _row(target, current=current)

    target.role = role
    await target.save()
    await _audit(current, "user.role_changed", target,
                 {"email": target.email, "from": was, "to": role})
    return _row(target, current=current)
