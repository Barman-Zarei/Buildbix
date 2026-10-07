from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.security import RANK, decode_token
from ..models import Automation, Build, Membership, Project, User

bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    decoded = decode_token(creds.credentials) if creds else None
    user = db.get(User, decoded[0]) if decoded else None
    if not user or user.token_version != decoded[1]:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_member(db: Session, user: User, workspace_id: int, min_role: str = "viewer") -> Membership:
    m = db.scalar(select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user.id))
    if not m:
        raise HTTPException(404, "Not found")  # do not reveal that the workspace exists
    if RANK[m.role] < RANK[min_role]:
        raise HTTPException(403, f"This action requires the '{min_role}' role")
    return m


def project_for(db: Session, user: User, project_id: int, min_role: str = "viewer") -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Not found")
    require_member(db, user, p.workspace_id, min_role)
    return p


def build_for(db: Session, user: User, build_id: int, min_role: str = "viewer") -> tuple[Build, Project]:
    b = db.get(Build, build_id)
    if not b:
        raise HTTPException(404, "Not found")
    return b, project_for(db, user, b.project_id, min_role)


def automation_for(db: Session, user: User, automation_id: int, min_role: str = "viewer") -> Automation:
    a = db.get(Automation, automation_id)
    if not a:
        raise HTTPException(404, "Not found")
    require_member(db, user, a.workspace_id, min_role)
    return a
