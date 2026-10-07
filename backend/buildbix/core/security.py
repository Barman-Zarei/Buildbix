import hashlib
import hmac
import os
from datetime import timedelta

import jwt

from .config import settings
from .db import utcnow

ROLES = ("viewer", "developer", "admin", "owner")
RANK = {r: i for i, r in enumerate(ROLES)}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split("$")
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h.hex(), expected)
    except Exception:
        return False


DUMMY_HASH = hash_password("buildbix-dummy-password")  # equalise timing for unknown users


def _jwt_key() -> bytes:
    return hashlib.sha256(b"bbx-jwt:" + settings.secret_key.encode()).digest()


def create_token(user_id: int, version: int = 0) -> str:
    exp = utcnow() + timedelta(minutes=settings.token_ttl_minutes)
    return jwt.encode({"sub": str(user_id), "tv": version, "exp": exp}, _jwt_key(), algorithm="HS256")


def decode_token(token: str) -> tuple[int, int] | None:
    try:
        p = jwt.decode(token, _jwt_key(), algorithms=["HS256"])
        return int(p["sub"]), int(p.get("tv", 0))
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
