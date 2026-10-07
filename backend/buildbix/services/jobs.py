"""DB-backed job queue (priority, retry, cancel, timeout). The interface maps 1:1 onto Redis/arq later."""
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.db import utcnow
from ..models import Job


def enqueue(db: Session, kind: str, payload: dict, *, queue: str = "build", priority: int = 0,
            max_attempts: int = 1) -> Job:
    job = Job(kind=kind, queue=queue, payload=payload, priority=priority, max_attempts=max_attempts)
    db.add(job)
    db.commit()
    return job


def claim_next(db: Session, queues: tuple[str, ...] = ("build",)) -> Job | None:
    """Atomically claim the highest-priority queued job (safe with several workers)."""
    for _ in range(5):
        job = db.scalar(select(Job).where(Job.status == "queued", Job.queue.in_(queues))
                        .order_by(Job.priority.desc(), Job.id).limit(1))
        if job is None:
            return None
        res = db.execute(update(Job).where(Job.id == job.id, Job.status == "queued")
                         .values(status="running", started_at=utcnow(), attempts=Job.attempts + 1))
        db.commit()
        if res.rowcount == 1:
            db.refresh(job)
            return job
    return None


def finish(db: Session, job: Job, status: str, error: str | None = None) -> None:
    job.status, job.error, job.finished_at = status, error, utcnow()
    db.commit()


def requeue(db: Session, job: Job, error: str) -> None:
    job.status, job.error = "queued", error
    db.commit()
