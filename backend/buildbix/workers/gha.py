"""Run a build on GitHub Actions instead of the local Docker sandbox.
Requires: Git project, GITHUB_TOKEN workspace secret, and the plan's workflow file committed in the repo."""
import io
import re
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..core.archive import ArchiveError, safe_extract_zip
from ..core.config import settings
from ..core.db import utcnow
from ..models import Artifact
from ..services.github_actions import GitHubClient, GitHubError
from ..services.projects import workspace_secret

REPO_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$")
MAX_LOG_LINES = 20000


def _ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def _find_run(client, owner, repo, wf, branch, since, poll):
    deadline = time.monotonic() + 120
    while True:
        runs = [r for r in client.list_runs(owner, repo, wf, branch) if _ts(r["created_at"]) >= since - timedelta(seconds=10)]
        if runs:
            return max(runs, key=lambda r: r["id"])
        if time.monotonic() > deadline:
            raise GitHubError("the dispatched workflow run did not appear within 120s")
        time.sleep(poll)


def run_remote(db, build, project, write, should_cancel, finish, storage, *, client=None, poll=5.0) -> None:
    plan = build.plan
    m = REPO_RE.match(project.repo_url or "")
    if not m or not plan.get("workflow_file"):
        write("ERROR: GitHub Actions runner needs a GitHub project and a plan with a workflow file")
        return finish("failed", "GitHub Actions runner not applicable to this project/target")
    owner, repo = m.groups()
    if client is None:
        token = workspace_secret(db, project.workspace_id, "GITHUB_TOKEN")
        if not token:
            write("ERROR: workspace secret GITHUB_TOKEN is not set")
            return finish("failed", "GITHUB_TOKEN secret is missing")
        client = GitHubClient(token)
    wf = plan["workflow_file"]
    try:
        write(f"Dispatching {wf} on {owner}/{repo}@{project.branch}")
        t0 = utcnow()
        try:
            client.dispatch(owner, repo, wf, project.branch)
        except GitHubError as e:
            if str(e).startswith("404"):
                write(f"ERROR: workflow '{wf}' not found. Commit .github/workflows/{wf} (see the pipeline endpoint) to branch '{project.branch}' first.")
                return finish("failed", f"Workflow file {wf} not found in the repository")
            raise
        run = _find_run(client, owner, repo, wf, project.branch, t0, poll)
        rid = run["id"]
        write(f"GitHub run {rid}: {run.get('html_url', '')}")
        deadline, last, cancelled = time.monotonic() + settings.build_timeout_seconds, None, False
        while True:
            r = client.get_run(owner, repo, rid)
            if r["status"] != last:
                write(f"Run status: {r['status']}")
                last = r["status"]
            if r["status"] == "completed":
                break
            if not cancelled and should_cancel():
                client.cancel(owner, repo, rid)
                cancelled = True
                write("Cancel requested on GitHub")
            if time.monotonic() > deadline:
                client.cancel(owner, repo, rid)
                write("Timed out; cancelled the GitHub run")
                return finish("failed", f"Timed out after {settings.build_timeout_seconds}s")
            time.sleep(poll)
        lines = 0
        for job in client.list_jobs(owner, repo, rid):
            write(f"--- job '{job['name']}': {job.get('conclusion')}")
            try:
                for line in client.job_logs(owner, repo, job["id"]).splitlines():
                    lines += 1
                    if lines > MAX_LOG_LINES:
                        write("(log truncated)")
                        break
                    write(line)
            except GitHubError as e:
                write(f"(could not fetch job logs: {e})")
        concl = r.get("conclusion")
        if concl == "cancelled":
            return finish("cancelled", "Cancelled")
        if concl != "success":
            return finish("failed", f"GitHub run concluded: {concl}")
        arts = [a for a in client.list_artifacts(owner, repo, rid) if a["name"].startswith(plan["artifact_name"]) and not a.get("expired")]
        if not arts:
            write("ERROR: run succeeded but no artifact named " + plan["artifact_name"] + "*")
            return finish("failed", "No artifact produced")
        stored = 0
        for art in arts:
            data = client.download_artifact(owner, repo, art["id"])
            with tempfile.TemporaryDirectory(prefix="bbx-gha-") as tmp:
                safe_extract_zip(io.BytesIO(data), Path(tmp), max_bytes=settings.max_extracted_bytes, max_files=1000)
                for p in sorted(x for x in Path(tmp).rglob("*") if x.is_file()):
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", p.name)
                    suffix = art["name"].removeprefix(plan["artifact_name"]).strip("-")
                    label = safe if len(arts) == 1 or not suffix else f"{re.sub(r'[^A-Za-z0-9._-]', '_', suffix)}-{safe}"
                    key = f"artifacts/{project.workspace_id}/{build.id}/{art['id']}/{safe}"
                    size, sha = storage.put_file(key, p)
                    db.add(Artifact(build_id=build.id, filename=label, storage_key=key, size=size, sha256=sha))
                    write(f"Artifact stored: {label} ({size} bytes, sha256 {sha})")
                    stored += 1
        if not stored:
            return finish("failed", "Artifact archive was empty")
        db.commit()
        finish("success")
    except (GitHubError, ArchiveError) as e:
        write(f"ERROR: {e}")
        finish("failed", f"GitHub Actions error: {e}")
