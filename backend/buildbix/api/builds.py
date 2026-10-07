from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..automation.schedule import ScheduleError, next_run, parse_schedule
from ..core import events
from ..core.db import get_db, utcnow
from ..core.storage import get_storage
from ..models import Artifact, Automation, Build, Job, User
from ..services.builds import BuildRequestError, create_build
from ..workers.scheduler import run_automation
from .deps import automation_for, build_for, current_user, project_for, require_member

router = APIRouter(tags=["builds"])


def build_out(b: Build, with_plan: bool = False) -> dict:
    out = {"id": b.id, "project_id": b.project_id, "number": b.number, "target": b.target, "status": b.status,
           "version": b.version, "commit_sha": b.commit_sha, "trigger": b.trigger, "runner": b.plan.get("runner", "docker"), "error": b.error_message,
           "created_at": b.created_at.isoformat(),
           "started_at": b.started_at.isoformat() if b.started_at else None,
           "finished_at": b.finished_at.isoformat() if b.finished_at else None,
           "duration_seconds": int((b.finished_at - b.started_at).total_seconds()) if b.started_at and b.finished_at else None}
    if with_plan:
        out["plan"] = b.plan
    return out


class BuildIn(BaseModel):
    target: str = "android-apk"
    accept_sdk_license: bool = False
    ignore_analysis_errors: bool = False
    runner: Literal["docker", "github_actions"] = "docker"


@router.post("/projects/{project_id}/builds", status_code=201)
def start_build(project_id: int, body: BuildIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id, "developer")
    try:
        b = create_build(db, p, body.target, actor_id=user.id, accept_sdk_license=body.accept_sdk_license,
                         ignore_analysis_errors=body.ignore_analysis_errors, runner=body.runner)
    except BuildRequestError as e:
        raise HTTPException(422, str(e))
    return build_out(b)


@router.get("/projects/{project_id}/builds")
def list_builds(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id)
    rows = db.scalars(select(Build).where(Build.project_id == p.id).order_by(Build.number.desc()).limit(100)).all()
    return [build_out(b) for b in rows]


@router.get("/builds/{build_id}")
def get_build(build_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b, _ = build_for(db, user, build_id)
    out = build_out(b, with_plan=True)
    out["plan"] = {k: v for k, v in out["plan"].items() if k != "dockerfile"}
    out["artifacts"] = [{"id": a.id, "filename": a.filename, "size": a.size, "sha256": a.sha256}
                        for a in db.scalars(select(Artifact).where(Artifact.build_id == b.id))]
    return out


@router.get("/builds/{build_id}/logs", response_class=PlainTextResponse)
def build_logs(build_id: int, offset: int = Query(0, ge=0), limit: int = Query(1 << 20, ge=1, le=1 << 22),
               search: str | None = Query(None, max_length=100), errors_only: bool = False,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Poll with offset=X-Log-Next-Offset for live logs. Lines were already redacted when stored."""
    b, _ = build_for(db, user, build_id)
    data = get_storage().read_range(b.log_key, offset, limit)
    text = data.decode("utf-8", errors="replace")
    if search or errors_only:
        lines = text.splitlines()
        if search:
            lines = [l for l in lines if search.lower() in l.lower()]
        if errors_only:
            lines = [l for l in lines if "error" in l.lower() or "failed" in l.lower()]
        text = "\n".join(lines)
    return PlainTextResponse(text, headers={"X-Log-Next-Offset": str(offset + len(data)), "X-Build-Status": b.status})


@router.post("/builds/{build_id}/cancel")
def cancel_build(build_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b, p = build_for(db, user, build_id, "developer")
    if b.status not in ("queued", "running"):
        raise HTTPException(409, f"Build already {b.status}")
    job = next((j for j in db.scalars(select(Job).where(Job.kind == "build", Job.status.in_(("queued", "running"))))
                if j.payload.get("build_id") == b.id), None)
    if job:
        job.cancel_requested = True
        if job.status == "queued":  # not picked up yet: cancel immediately
            job.status, job.finished_at = "cancelled", utcnow()
            b.status, b.finished_at, b.error_message = "cancelled", utcnow(), "Cancelled by user"
    db.commit()
    events.publish(db, "build.cancel_requested", workspace_id=p.workspace_id, actor_id=user.id, payload={"build_id": b.id})
    return build_out(b)


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.get(Artifact, artifact_id)
    if not a:
        raise HTTPException(404, "Not found")
    build_for(db, user, a.build_id)

    def stream():
        with get_storage().open(a.storage_key) as f:
            while chunk := f.read(1 << 20):
                yield chunk

    return StreamingResponse(stream(), media_type="application/octet-stream",
                             headers={"Content-Disposition": f'attachment; filename="{a.filename}"',
                                      "X-Checksum-SHA256": a.sha256})


class AutomationIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    schedule: str | None = Field(default=None, max_length=200, description="e.g. 'every day at 03:00' (UTC)")
    event: Literal["push", "tag", "release"] | None = None
    target: str = "android-apk"
    accept_sdk_license: bool = False

    @model_validator(mode="after")
    def _one_trigger(self):
        if bool(self.schedule) == bool(self.event):
            raise ValueError("Provide exactly one of 'schedule' or 'event'")
        return self


def automation_out(a: Automation) -> dict:
    return {"id": a.id, "project_id": a.project_id, "name": a.name, "schedule_text": a.schedule_text, "schedule": a.schedule,
            "event": a.event, "target": a.target, "enabled": a.enabled,
            "next_run_at": a.next_run_at.isoformat() if a.next_run_at else None,
            "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None}


@router.post("/projects/{project_id}/automations", status_code=201)
def create_automation(project_id: int, body: AutomationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id, "developer")
    sched = nxt = None
    if body.schedule:
        try:
            sched = parse_schedule(body.schedule)
        except ScheduleError as e:
            raise HTTPException(422, str(e))
        nxt = next_run(sched, utcnow())
    a = Automation(workspace_id=p.workspace_id, project_id=p.id, name=body.name, schedule_text=body.schedule, schedule=sched,
                   event=body.event, target=body.target, accept_sdk_license=body.accept_sdk_license, next_run_at=nxt)
    db.add(a)
    db.commit()
    return automation_out(a)


@router.get("/projects/{project_id}/automations")
def list_automations(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id)
    return [automation_out(a) for a in db.scalars(select(Automation).where(Automation.project_id == p.id))]


@router.post("/automations/{automation_id}/run")
def run_now(automation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = automation_for(db, user, automation_id, "developer")
    run_automation(db, a, actor_id=user.id, trigger="manual")
    return {"triggered": True}


@router.delete("/automations/{automation_id}", status_code=204)
def delete_automation(automation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = automation_for(db, user, automation_id, "developer")
    db.delete(a)
    db.commit()
