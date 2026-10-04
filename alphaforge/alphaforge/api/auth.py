"""JWT auth with a single bootstrap admin (extend to multi-user via the users table)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from sqlalchemy import select

from alphaforge.config import get_settings
from alphaforge.db import session_scope
from alphaforge.db.models import User



def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


def _verify(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
ALGO = "HS256"


def bootstrap_admin() -> None:
    s = get_settings()
    with session_scope() as db:
        if db.scalar(select(User).where(User.username == s.admin_user)) is None:
            db.add(User(username=s.admin_user, password_hash=_hash(s.admin_password), role="admin"))


def authenticate(username: str, password: str) -> User | None:
    with session_scope() as db:
        u = db.scalar(select(User).where(User.username == username))
    if u and _verify(password, u.password_hash):
        return u
    return None


def create_token(username: str, role: str) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.access_token_minutes)
    return jwt.encode({"sub": username, "role": role, "exp": exp}, s.secret_key, algorithm=ALGO)


def current_user(token: str = Depends(oauth2)) -> dict:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    return {"username": payload["sub"], "role": payload.get("role", "viewer")}


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user
