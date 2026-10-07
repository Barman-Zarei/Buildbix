import logging
import re
import shutil
import tempfile
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import events
from ..core.archive import ArchiveError, find_project_root, safe_extract_zip
from ..core.config import settings
from ..core.crypto import decrypt
from ..core.db import SessionLocal, utcnow
from ..core.redaction import Redactor
from ..core.storage import ObjectStorage, get_storage
from ..models import Artifact, Build, Job, Project, Secret
from ..services import jobs
from . import gha
from .sandbox import DockerSandbox, Sandbox, SandboxSpec, SandboxUnavailable

log = logging.getLogger("buildbix.worker")


def execute_build(db: Session, job: Job, sandbox: Sandbox, storage: ObjectStorage | None = None, *, gh_client=None,
                  poll: float = 5.0) -> None:
    storage = storage or get_storage()
    build = db.get(Build, job.payload["build_id"])
    project = db.get(Project, build.project_id)
    ws = project.workspace_id
    redact = Redactor([decrypt(s.value_enc) for s in db.scalars(select(Secret).where(Secret.workspace_id == ws))])

    def write(line: str) -> None:  # redacted BEFORE it touches storage
        storage.append(build.log_key, (redact(f"[{utcnow():%H:%M:%S}] {line}") + "\n").encode())

    state = {"last": 0.0, "flag": False}

    def should_cancel() -> bool:
        if time.monotonic() - state["last"] >= 2:
            state["last"] = time.monotonic()
            db.refresh(job)
            state["flag"] = job.cancel_requested
        return state["flag"]

    def finish(status: str, message: str | None = None) -> None:
        build.status, build.error_message, build.finished_at = status, message, utcnow()
        db.commit()
        ev = {"success": events.BUILD_COMPLETED, "failed": events.BUILD_FAILED, "cancelled": events.BUILD_CANCELLED}[status]
        events.publish(db, ev, workspace_id=ws, payload={"build_id": build.id, "status": status})
        jobs.finish(db, job, "done" if status == "success" else status, message)

    build.status, build.started_at = "running", utcnow()
    db.commit()
    events.publish(db, events.BUILD_STARTED, workspace_id=ws, payload={"build_id": build.id})
    write(f"Build #{build.number} started (target {build.target}, adapter {build.plan.get('adapter')})")
    if should_cancel():
        return finish("cancelled", "Cancelled before start")
    if build.plan.get("runner") == "github_actions":
        return gha.run_remote(db, build, project, write, should_cancel, finish, storage, client=gh_client, poll=poll)
    tmp = Path(tempfile.mkdtemp(prefix="bbx-build-"))
    try:
        write("Extracting source")
        with storage.open(build.source_key) as f:
            safe_extract_zip(f, tmp / "src", max_bytes=settings.max_extracted_bytes, max_files=settings.max_zip_files)
        root = find_project_root(tmp / "src")
        plan = build.plan
        spec = SandboxSpec(name=f"bbx-build-{build.id}", source_dir=root,
                           cache_dir=settings.data_dir / "cache" / str(ws), image=plan["image"],
                           dockerfile=plan["dockerfile"], command=plan["command"], timeout=settings.build_timeout_seconds,
                           memory=settings.build_memory, cpus=settings.build_cpus,
                           network=settings.sandbox_network, proxy=settings.sandbox_proxy)
        res = sandbox.run(spec, write, should_cancel)
        if res.cancelled:
            write("Build cancelled")
            return finish("cancelled", "Cancelled by user")
        if res.timed_out:
            write("Build timed out")
            return finish("failed", f"Timed out after {settings.build_timeout_seconds}s")
        if res.exit_code != 0:
            write(f"Build failed with exit code {res.exit_code}")
            return finish("failed", f"Build command exited with code {res.exit_code}")
        if not plan.get("artifact_glob"):  # e.g. test runs: success == exit code 0
            write("Completed successfully")
            return finish("success")
        found = sorted(p for p in root.glob(plan["artifact_glob"]) if p.is_file())
        if not found:
            write("ERROR: build succeeded but produced no artifact matching " + plan["artifact_glob"])
            return finish("failed", "No artifact produced")
        for p in found:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", p.name)
            key = f"artifacts/{ws}/{build.id}/{safe}"
            size, sha = storage.put_file(key, p)
            db.add(Artifact(build_id=build.id, filename=safe, storage_key=key, size=size, sha256=sha))
            write(f"Artifact stored: {safe} ({size} bytes, sha256 {sha})")
        db.commit()
        finish("success")
    except SandboxUnavailable as e:
        write(f"ERROR: {e}")
        finish("failed", str(e))
    except ArchiveError as e:
        write(f"ERROR: {e}")
        finish("failed", str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def process_job(db: Session, job: Job, sandbox: Sandbox, storage: ObjectStorage | None = None, *, gh_client=None,
                poll: float = 5.0) -> None:
    try:
        if job.kind == "build":
            execute_build(db, job, sandbox, storage, gh_client=gh_client, poll=poll)
        else:
            jobs.finish(db, job, "failed", f"Unknown job kind {job.kind}")
    except Exception as e:  # infrastructure error: retry if allowed
        log.exception("job %s crashed", job.id)
        db.rollback()
        if job.attempts < job.max_attempts:
            jobs.requeue(db, job, f"{type(e).__name__}: {e}")
        else:
            jobs.finish(db, job, "failed", f"{type(e).__name__}: {e}")
            b = db.get(Build, job.payload.get("build_id", 0))
            if b and b.status in ("queued", "running"):
                b.status, b.error_message, b.finished_at = "failed", "Internal worker error", utcnow()
                db.commit()


def run_forever(poll_seconds: float = 2.0, sandbox: Sandbox | None = None, stop=lambda: False) -> None:
    from .scheduler import tick

    sandbox = sandbox or DockerSandbox()
    last_tick = 0.0
    while not stop():
        with SessionLocal() as db:
            if time.monotonic() - last_tick > 30:
                last_tick = time.monotonic()
                tick(db)
            job = jobs.claim_next(db, ("build",))
            if job:
                process_job(db, job, sandbox)
                continue
        time.sleep(poll_seconds)
