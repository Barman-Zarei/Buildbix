import base64
import difflib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai.llm import AINotConfigured, LLMClient, get_llm
from ..ci import describe, effective_params, status
from ..ci import registry as R
from ..ci.adapt import adapt_params
from ..core import events
from ..core.db import get_db
from ..models import Project, User
from ..pipeline import UnsupportedTarget, get_plan
from ..services.github_actions import GitHubClient, GitHubError
from ..services.projects import workspace_secret
from ..workers.gha import REPO_RE
from .deps import current_user, project_for

router = APIRouter(tags=["ci"])


def github_factory():
    return lambda token: GitHubClient(token)  # overridden in tests


def _plan(p: Project, target: str, params=None) -> dict:
    try:
        plan = get_plan(p.analysis or {}, target, False, p.ci_params if params is None else params)
    except UnsupportedTarget as e:
        raise HTTPException(422, str(e))
    if not plan.get("template_id") or not plan.get("github_workflow"):
        raise HTTPException(422, "This target has no CI template")
    return plan


def _overrides(p: Project, tid: str) -> dict:
    return (p.ci_params or {}).get(tid, {})


def adapt_preview(db: Session, p: Project, target: str, llm: LLMClient) -> dict:
    """Ask the AI for parameter values only; nothing is saved here."""
    plan = _plan(p, target)
    tid, analysis = plan["template_id"], p.analysis or {}
    ok, rejected = adapt_params(llm, tid, analysis, effective_params(tid, analysis, _overrides(p, tid)))
    merged = {**(p.ci_params or {}), tid: {**_overrides(p, tid), **ok}}
    new = _plan(p, target, merged)["github_workflow"]
    base = _plan(p, target, {})["github_workflow"]
    diff = "\n".join(difflib.unified_diff(base.splitlines(), new.splitlines(), "template", "adapted", lineterm=""))
    return {"template": tid, "status": status(tid), "proposed_params": ok, "rejected": rejected, "diff": diff, "workflow": new}


@router.get("/ci/templates")
def templates(_user: User = Depends(current_user)):
    return [{"id": t, "status": status(t), "params": describe(t)} for t in R.TEMPLATES]


@router.get("/projects/{project_id}/ci")
def show(project_id: int, target: str = "android-apk", user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id)
    plan = _plan(p, target)
    tid = plan["template_id"]
    return {"template": tid, "status": status(tid), "params": effective_params(tid, p.analysis, _overrides(p, tid)),
            "workflow_file": plan["workflow_file"], "workflow": plan["github_workflow"]}


class TargetIn(BaseModel):
    target: str = "android-apk"


@router.post("/projects/{project_id}/ci/adapt")
def adapt(project_id: int, body: TargetIn, user: User = Depends(current_user), db: Session = Depends(get_db),
          llm: LLMClient = Depends(get_llm)):
    p = project_for(db, user, project_id, "developer")
    try:
        return adapt_preview(db, p, body.target, llm)
    except AINotConfigured as e:
        raise HTTPException(503, str(e))


class SaveIn(BaseModel):
    target: str = "android-apk"
    params: dict


@router.put("/projects/{project_id}/ci")
def save(project_id: int, body: SaveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id, "developer")
    tid = _plan(p, body.target)["template_id"]
    try:
        clean = R.validate_user_params(tid, body.params)
    except R.ParamError as e:
        raise HTTPException(422, str(e))
    p.ci_params = {**(p.ci_params or {}), tid: {**_overrides(p, tid), **clean}}
    db.commit()
    events.publish(db, "ci.params_saved", workspace_id=p.workspace_id, actor_id=user.id, payload={"template": tid, "params": clean})
    return {"template": tid, "params": effective_params(tid, p.analysis, _overrides(p, tid)),
            "workflow": _plan(p, body.target)["github_workflow"]}


class CommitIn(BaseModel):
    target: str = "android-apk"
    approve: bool = False
    overwrite: bool = False


@router.post("/projects/{project_id}/ci/commit")
def commit(project_id: int, body: CommitIn, user: User = Depends(current_user), db: Session = Depends(get_db),
           factory=Depends(github_factory)):
    """Writes the workflow into the user's repo (one commit on the project's branch). Needs explicit approval."""
    p = project_for(db, user, project_id, "admin")
    if not body.approve:
        raise HTTPException(422, "This creates a commit in your repository; send approve=true to confirm")
    m = REPO_RE.match(p.repo_url or "")
    if p.source_kind != "git" or not m:
        raise HTTPException(422, "Only GitHub projects can receive a commit")
    token = workspace_secret(db, p.workspace_id, "GITHUB_TOKEN")
    if not token:
        raise HTTPException(422, "Set the workspace secret GITHUB_TOKEN first (Contents: write)")
    plan = _plan(p, body.target)
    path, content = f".github/workflows/{plan['workflow_file']}", plan["github_workflow"].encode()
    owner, repo = m.groups()
    client = factory(token)
    try:
        existing = client.get_file(owner, repo, path, p.branch)
        sha = None
        if existing:
            if base64.b64decode(existing.get("content", "")) == content:
                return {"status": "unchanged", "path": path}
            if not body.overwrite:
                raise HTTPException(409, f"{path} already exists with different content; send overwrite=true to replace it")
            sha = existing["sha"]
        res = client.put_file(owner, repo, path, content, "Add BUILDBIX CI workflow", p.branch, sha)
    except GitHubError as e:
        raise HTTPException(502, f"GitHub: {e}")
    events.publish(db, "ci.committed", workspace_id=p.workspace_id, actor_id=user.id, payload={"path": path})
    return {"status": "committed", "path": path, "commit": (res.get("commit") or {}).get("sha")}
