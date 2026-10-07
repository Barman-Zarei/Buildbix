import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import events
from ..core.crypto import encrypt
from ..core.db import get_db
from ..models import AuditLog, Membership, Secret, User, Workspace
from .deps import current_user, require_member

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
Env = Literal["development", "testing", "production"]


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class MemberIn(BaseModel):
    email: str
    role: Literal["viewer", "developer", "admin"]


class SecretIn(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    value: str = Field(min_length=1, max_length=8192)
    environment: Env = "production"


@router.post("", status_code=201)
def create_workspace(body: WorkspaceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = Workspace(name=body.name.strip(), created_by=user.id)
    db.add(ws)
    db.flush()
    db.add(Membership(workspace_id=ws.id, user_id=user.id, role="owner"))
    db.commit()
    events.publish(db, "workspace.created", workspace_id=ws.id, actor_id=user.id)
    return {"id": ws.id, "name": ws.name, "role": "owner"}


@router.get("")
def list_workspaces(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Workspace, Membership.role).join(Membership, Membership.workspace_id == Workspace.id)
                      .where(Membership.user_id == user.id).order_by(Workspace.id)).all()
    return [{"id": w.id, "name": w.name, "role": role} for w, role in rows]


@router.get("/{workspace_id}")
def get_workspace(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = require_member(db, user, workspace_id)
    ws = db.get(Workspace, workspace_id)
    return {"id": ws.id, "name": ws.name, "role": m.role}


@router.post("/{workspace_id}/members", status_code=201)
def add_member(workspace_id: int, body: MemberIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id, "admin")
    target = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not target:
        raise HTTPException(404, "No registered user with that email")
    if db.scalar(select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == target.id)):
        raise HTTPException(409, "Already a member")
    db.add(Membership(workspace_id=workspace_id, user_id=target.id, role=body.role))
    db.commit()
    events.publish(db, "member.added", workspace_id=workspace_id, actor_id=user.id,
                   payload={"user_id": target.id, "role": body.role})
    return {"user_id": target.id, "role": body.role}


@router.get("/{workspace_id}/members")
def list_members(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id)
    rows = db.execute(select(User.id, User.email, Membership.role).join(Membership, Membership.user_id == User.id)
                      .where(Membership.workspace_id == workspace_id)).all()
    return [{"user_id": i, "email": e, "role": r} for i, e, r in rows]


@router.put("/{workspace_id}/secrets")
def set_secret(workspace_id: int, body: SecretIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id, "admin")
    s = db.scalar(select(Secret).where(Secret.workspace_id == workspace_id, Secret.name == body.name,
                                       Secret.environment == body.environment))
    if s:
        s.value_enc = encrypt(body.value)
    else:
        db.add(Secret(workspace_id=workspace_id, name=body.name, environment=body.environment,
                      value_enc=encrypt(body.value)))
    db.commit()
    events.publish(db, "secret.set", workspace_id=workspace_id, actor_id=user.id,
                   payload={"name": body.name, "environment": body.environment})  # never the value
    return {"name": body.name, "environment": body.environment}


@router.get("/{workspace_id}/secrets")
def list_secrets(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id, "admin")
    rows = db.scalars(select(Secret).where(Secret.workspace_id == workspace_id).order_by(Secret.name)).all()
    return [{"name": s.name, "environment": s.environment} for s in rows]  # values are never returned


@router.delete("/{workspace_id}/secrets/{name}", status_code=204)
def delete_secret(workspace_id: int, name: str, environment: Env = "production",
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id, "admin")
    s = db.scalar(select(Secret).where(Secret.workspace_id == workspace_id, Secret.name == name,
                                       Secret.environment == environment))
    if not s:
        raise HTTPException(404, "Not found")
    db.delete(s)
    db.commit()
    events.publish(db, "secret.deleted", workspace_id=workspace_id, actor_id=user.id, payload={"name": name})


@router.get("/{workspace_id}/activity")
def activity(workspace_id: int, limit: int = 100, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id)
    rows = db.scalars(select(AuditLog).where(AuditLog.workspace_id == workspace_id)
                      .order_by(AuditLog.id.desc()).limit(min(limit, 500))).all()
    return [{"id": r.id, "event": r.event, "actor_id": r.actor_id, "payload": r.payload,
             "created_at": r.created_at.isoformat()} for r in rows]
