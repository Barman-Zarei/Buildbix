import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..automation.schedule import next_run
from ..core import events
from ..core.db import utcnow
from ..models import Automation, Project
from ..services.builds import BuildRequestError, create_build

log = logging.getLogger("buildbix.scheduler")


def run_automation(db: Session, a: Automation, *, actor_id: int | None = None, trigger: str = "schedule") -> None:
    project = db.get(Project, a.project_id)
    events.publish(db, events.AUTOMATION_TRIGGERED, workspace_id=a.workspace_id, actor_id=actor_id,
                   payload={"automation_id": a.id, "trigger": trigger})
    try:
        create_build(db, project, a.target, actor_id=actor_id, trigger="automation",
                     accept_sdk_license=a.accept_sdk_license)
    except BuildRequestError as e:
        log.warning("automation %s could not start a build: %s", a.id, e)
        events.publish(db, "automation.failed", workspace_id=a.workspace_id, payload={"automation_id": a.id, "error": str(e)})


def fire_event(db: Session, project: Project, kind: str) -> int:
    """Run every enabled automation of this project that listens for `kind` (push | tag | release)."""
    rows = db.scalars(select(Automation).where(Automation.project_id == project.id, Automation.event == kind,
                                               Automation.enabled.is_(True))).all()
    for a in rows:
        run_automation(db, a, trigger=kind)
    return len(rows)


def tick(db: Session) -> int:
    """Start every due automation; returns how many ran."""
    now = utcnow()
    due = db.scalars(select(Automation).where(Automation.enabled.is_(True), Automation.next_run_at <= now)).all()
    for a in due:
        a.last_run_at, a.next_run_at = now, next_run(a.schedule, now)
        db.commit()
        run_automation(db, a)
    return len(due)
