from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import events
from ..core.db import get_db
from ..models import Server, User
from ..providers.base import CloudProvider, ProviderError
from ..providers.hetzner import PROVIDERS
from ..services.projects import workspace_secret
from .deps import current_user, require_member

router = APIRouter(tags=["servers"])


def provider_factory() -> Callable[[str, str], CloudProvider]:
    return lambda name, token: PROVIDERS[name][0](token)  # overridden in tests


class ServerIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    provider: Literal["hetzner"] = "hetzner"
    server_type: str = Field(min_length=2, max_length=40, description="provider-specific size, e.g. from your provider's console")
    region: str = Field(min_length=2, max_length=40)
    image: str = Field(default="ubuntu-24.04", max_length=60)
    confirm_cost: bool = False


def out(s: Server) -> dict:
    return {"id": s.id, "name": s.name, "provider": s.provider, "server_type": s.server_type, "region": s.region,
            "status": s.status, "ip": s.ip}


def _provider(db, ws_id, name, factory):
    token = workspace_secret(db, ws_id, PROVIDERS[name][1])
    if not token:
        raise HTTPException(422, f"Set the workspace secret {PROVIDERS[name][1]} first")
    return factory(name, token)


@router.post("/workspaces/{workspace_id}/servers", status_code=201)
def create_server(workspace_id: int, body: ServerIn, user: User = Depends(current_user), db: Session = Depends(get_db),
                  factory=Depends(provider_factory)):
    require_member(db, user, workspace_id, "admin")
    if not body.confirm_cost:
        raise HTTPException(422, "Creating a server creates a billable resource at your provider; send confirm_cost=true")
    if db.scalar(select(Server).where(Server.workspace_id == workspace_id, Server.name == body.name)):
        raise HTTPException(409, "A server with this name already exists")
    prov = _provider(db, workspace_id, body.provider, factory)
    try:
        info = prov.create_server(body.name, body.server_type, body.region, body.image)
    except ProviderError as e:
        raise HTTPException(502, str(e))
    s = Server(workspace_id=workspace_id, name=body.name, provider=body.provider, provider_id=info.provider_id,
               server_type=body.server_type, region=body.region, status=info.status, ip=info.ip, created_by=user.id)
    db.add(s)
    db.commit()
    events.publish(db, "server.created", workspace_id=workspace_id, actor_id=user.id, payload={"server_id": s.id})
    return out(s)


@router.get("/workspaces/{workspace_id}/servers")
def list_servers(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id)
    return [out(s) for s in db.scalars(select(Server).where(Server.workspace_id == workspace_id).order_by(Server.id))]


def _server(db, user, server_id, role):
    s = db.get(Server, server_id)
    if not s:
        raise HTTPException(404, "Not found")
    require_member(db, user, s.workspace_id, role)
    return s


@router.post("/servers/{server_id}/refresh")
def refresh_server(server_id: int, user: User = Depends(current_user), db: Session = Depends(get_db),
                   factory=Depends(provider_factory)):
    s = _server(db, user, server_id, "admin")
    try:
        info = _provider(db, s.workspace_id, s.provider, factory).get_server(s.provider_id)
    except ProviderError as e:
        raise HTTPException(502, str(e))
    s.status, s.ip = info.status, info.ip
    db.commit()
    return out(s)


@router.delete("/servers/{server_id}", status_code=204)
def delete_server(server_id: int, confirm: str = "", user: User = Depends(current_user), db: Session = Depends(get_db),
                  factory=Depends(provider_factory)):
    """HIGH RISK: destroys the machine and its data at the provider. Requires typing the server's exact name."""
    s = _server(db, user, server_id, "admin")
    if confirm != s.name:
        raise HTTPException(422, "Pass confirm=<exact server name> to delete it")
    try:
        _provider(db, s.workspace_id, s.provider, factory).delete_server(s.provider_id)
    except ProviderError as e:
        raise HTTPException(502, str(e))
    ws = s.workspace_id
    db.delete(s)
    db.commit()
    events.publish(db, "server.deleted", workspace_id=ws, actor_id=user.id, payload={"server_id": server_id})
