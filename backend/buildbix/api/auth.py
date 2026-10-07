import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.ratelimit import auth_limiter
from ..core.security import DUMMY_HASH, create_token, hash_password, verify_password
from ..models import User
from .deps import current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=10, max_length=256)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("invalid email")
        return v


@router.post("/register", status_code=201, dependencies=[Depends(auth_limiter)])
def register(body: Credentials, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "Email already registered")
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return {"access_token": create_token(user.id, user.token_version), "token_type": "bearer", "user": {"id": user.id, "email": user.email}}


@router.post("/login", dependencies=[Depends(auth_limiter)])
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not (user and ok):
        raise HTTPException(401, "Invalid email or password")
    return {"access_token": create_token(user.id, user.token_version), "token_type": "bearer", "user": {"id": user.id, "email": user.email}}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email}


@router.post("/logout-all", status_code=204)
def logout_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Revoke every token issued so far for this user (all devices)."""
    user.token_version += 1
    db.commit()
