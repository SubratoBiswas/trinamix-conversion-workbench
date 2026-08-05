"""Authentication helpers: bcrypt + JWT."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings
from app.models.user import User


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# The password hash of an account that cannot sign in yet.
#
# Inviting somebody from the Users screen creates the account and assigns the
# role; it deliberately does not set a password, because creating credentials
# stays a human action. The record therefore has to exist in a state that is
# valid to store and impossible to authenticate against, and this is it.
#
# A sentinel rather than an empty string, so that "no password" reads as a
# decision somebody wrote down rather than a field that failed to be filled in.
NO_PASSWORD = "!"


def password_is_set(password_hash: str | None) -> bool:
    """True only for a hash that could ever match a password.

    Blank, whitespace and the sentinel all mean no password has been set. Used by
    ``verify_password`` to refuse the sign-in AND by the Users screen to show
    which invited accounts are still waiting on one — one predicate, so the
    screen cannot report an account as usable while the login route disagrees.
    """
    h = (password_hash or "").strip()
    return bool(h) and h != NO_PASSWORD


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    # An unset password is never a match, and that is decided HERE rather than
    # left to passlib raising on a hash it cannot parse. The except below would
    # swallow a library change just as happily as it swallows a bad hash, and
    # what it would be swallowing is "every invited account signs in with any
    # password" — not something to find out about in production.
    if not password_is_set(hashed):
        return False
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def create_access_token(data: dict[str, Any]) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing authentication token")
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token payload")
        user = await User.find_one(User.email == sub)
        if not user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
        return user
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Could not validate credentials")
