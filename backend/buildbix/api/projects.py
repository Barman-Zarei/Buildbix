import hashlib
import hmac
import json
import logging
import secrets
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import coordinator
from ..ai.llm import AINotConfigured, LLMClient, get_llm
from ..core import events
from ..core.archive import ArchiveError
from ..core.config import settings
from ..core.crypto import decrypt, encrypt
from ..core.db import SessionLocal, get_db
from ..core.storage import get_storage
from ..models import AIMessage, Project, User
from ..pipeline import UnsupportedTarget, get_plan
from ..services import git as gitsvc
from ..services import vulnscan
from ..services import projects as psvc
from ..services.builds import BuildRequestError, create_build
from ..workers.scheduler import fire_event
from .deps import current_user, project_for, require_member

log = logging.getLogger("buildbix.api")
router = APIRouter(tags=["projects"])
webhooks = APIRouter(prefix="/webhooks", tags=["webhooks"])


def project_out(p: Project) -> dict:
    return {"id": p.id, "workspace_id": p.workspace_id, "name": p.name, "source_kind": p.source_kind,
            "repo_url": p.repo_url, "branch": p.branch, "commit_sha": p.commit_sha, "analysis": p.analysis,
            "webhook_enabled": bool(p.webhook_secret_enc), "created_at": p.created_at.isoformat()}


def _save_project(db: Session, user: User, p: Project) -> dict:
    db.add(p)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(409, "A project with this name already exists in the workspace")
    events.publish(db, events.PROJECT_CREATED, workspace_id=p.workspace_id, actor_id=user.id,
                   payload={"project_id": p.id, "kind": p.source_kind})
    return project_out(p)


@router.post("/workspaces/{workspace_id}/projects", status_code=201)
def import_zip(workspace_id: int, name: str = Form(min_length=1, max_length=100), file: UploadFile = File(...),
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id, "developer")
    storage, key = get_storage(), psvc.new_source_key(workspace_id)
    with tempfile.NamedTemporaryFile(prefix="bbx-up-") as tmp:
        size = 0
        while chunk := file.file.read(1 << 20):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise HTTPException(413, "Upload too large")
            tmp.write(chunk)
        tmp.flush()
        storage.put_file(key, Path(tmp.name))
    try:
        analysis = psvc.analyze_archive(storage, key)
    except ArchiveError as e:
        raise HTTPException(422, str(e))
    return _save_project(db, user, Project(workspace_id=workspace_id, name=name.strip(), source_kind="zip",
                                           source_key=key, analysis=analysis, created_by=user.id))


class GitIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    repo_url: str = Field(max_length=300)
    branch: str = Field(default="main", max_length=100)


@router.post("/workspaces/{workspace_id}/projects/git", status_code=201)
def import_git(workspace_id: int, body: GitIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id, "developer")
    try:
        gitsvc.validate(body.repo_url, body.branch)
    except gitsvc.GitError as e:
        raise HTTPException(422, str(e))
    p = Project(workspace_id=workspace_id, name=body.name.strip(), source_kind="git", repo_url=body.repo_url,
                branch=body.branch, source_key="pending", created_by=user.id)
    try:
        psvc.sync_project_from_git(db, p)  # clones, snapshots, analyzes
    except (gitsvc.GitError, ArchiveError) as e:
        raise HTTPException(422, str(e))
    return _save_project(db, user, p)


@router.get("/workspaces/{workspace_id}/projects")
def list_projects(workspace_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id)
    rows = db.scalars(select(Project).where(Project.workspace_id == workspace_id).order_by(Project.id)).all()
    return [project_out(p) for p in rows]


@router.get("/projects/{project_id}")
def get_project(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return project_out(project_for(db, user, project_id))


@router.post("/projects/{project_id}/analyze")
def reanalyze(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id, "developer")
    p.analysis = psvc.analyze_archive(get_storage(), p.source_key)
    db.commit()
    return project_out(p)


@router.post("/projects/{project_id}/sync")
def sync(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id, "developer")
    if p.source_kind != "git":
        raise HTTPException(422, "Only Git projects can be synced")
    try:
        psvc.sync_project_from_git(db, p)
    except gitsvc.GitError as e:
        raise HTTPException(422, str(e))
    return project_out(p)


@router.get("/projects/{project_id}/scan")
def scan(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id, "developer")
    try:
        return vulnscan.scan_archive(get_storage(), p.source_key)
    except vulnscan.ScanError as e:
        raise HTTPException(502, str(e))


@router.get("/projects/{project_id}/pipeline")
def pipeline(project_id: int, target: str = "android-apk", user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    p = project_for(db, user, project_id)
    try:
        return get_plan(p.analysis or {}, target, False, p.ci_params)
    except UnsupportedTarget as e:
        raise HTTPException(422, str(e))


class WebhookIn(BaseModel):
    target: str = "android-apk"
    accept_sdk_license: bool = False


@router.post("/projects/{project_id}/webhook")
def enable_webhook(project_id: int, body: WebhookIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id, "admin")
    if p.source_kind != "git":
        raise HTTPException(422, "Webhooks are only available for Git projects")
    secret = secrets.token_urlsafe(32)
    p.webhook_secret_enc, p.auto_build_target = encrypt(secret), body.target
    p.auto_build_accept_license = body.accept_sdk_license
    db.commit()
    return {"payload_path": f"/api/webhooks/github/{p.id}", "content_type": "application/json", "events": ["push"],
            "secret": secret, "note": "Shown once. Configure it in GitHub > Settings > Webhooks."}


def _handle_event(project_id: int, kind: str) -> None:
    with SessionLocal() as db:
        p = db.get(Project, project_id)
        try:
            psvc.sync_project_from_git(db, p)  # NOTE: syncs the project's branch; tag/release builds use its latest commit
            if kind == "push":
                create_build(db, p, p.auto_build_target or "android-apk", actor_id=None, trigger="webhook",
                             accept_sdk_license=p.auto_build_accept_license)
            fire_event(db, p, kind)
        except Exception as e:
            log.warning("webhook %s for project %s failed: %s", kind, project_id, e)
            events.publish(db, "webhook.failed", workspace_id=p.workspace_id, payload={"error": str(e)[:300]})


@webhooks.post("/github/{project_id}")
async def github_webhook(project_id: int, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    body = await request.body()
    if len(body) > 5_000_000:
        raise HTTPException(413, "Payload too large")
    p = db.get(Project, project_id)
    if not p or not p.webhook_secret_enc:
        raise HTTPException(404, "Not found")
    expected = "sha256=" + hmac.new(decrypt(p.webhook_secret_enc).encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", ""), expected):
        raise HTTPException(401, "Invalid signature")
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return {"ok": True}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")
    if event == "push":
        ref = payload.get("ref", "")
        if ref == f"refs/heads/{p.branch}":
            kind = "push"
        elif ref.startswith("refs/tags/"):
            kind = "tag"
        else:
            return {"ignored": "other branch"}
    elif event == "release" and payload.get("action") == "published":
        kind = "release"
    elif event == "pull_request":
        return {"ignored": "pull_request events are not supported yet"}
    else:
        return {"ignored": event}
    background.add_task(_handle_event, p.id, kind)
    return {"accepted": True}


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post("/projects/{project_id}/chat")
def chat(project_id: int, body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db),
         llm: LLMClient = Depends(get_llm)):
    p = project_for(db, user, project_id, "developer")
    history = [{"role": m.role, "content": m.content} for m in db.scalars(
        select(AIMessage).where(AIMessage.project_id == p.id).order_by(AIMessage.id.desc()).limit(10)).all()][::-1]
    try:
        reply = coordinator.answer(llm, p.name, p.analysis or {}, history, body.message)
    except AINotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception:
        log.exception("AI call failed")
        raise HTTPException(502, "The AI provider returned an error")
    db.add_all([AIMessage(project_id=p.id, user_id=user.id, role="user", content=body.message),
                AIMessage(project_id=p.id, role="assistant", content=reply)])
    db.commit()
    return {"reply": reply}


@router.get("/projects/{project_id}/chat")
def chat_history(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id)
    rows = db.scalars(select(AIMessage).where(AIMessage.project_id == p.id).order_by(AIMessage.id.desc()).limit(50)).all()
    return [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()} for m in rows][::-1]
