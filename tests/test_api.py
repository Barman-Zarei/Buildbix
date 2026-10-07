import hashlib
import hmac
import json
from pathlib import Path

from buildbix.ai.llm import LLMClient
from buildbix.core.config import settings
from buildbix.main import app
from buildbix.api.projects import get_llm
from buildbix.services import jobs
from buildbix.workers.runner import process_job
from buildbix.workers.sandbox import SandboxResult, SandboxUnavailable
from buildbix.workers.scheduler import tick
from conftest import KIVY_FILES, make_user, make_zip


class FakeSandbox:
    """TEST DOUBLE ONLY: simulates the container so queue/worker/artifact logic can be exercised without Docker."""

    def __init__(self, exit_code=0, produce=True, cancel=False):
        self.exit_code, self.produce = exit_code, produce

    def run(self, spec, log, should_cancel):
        log("fake buildozer output")
        log("API_KEY=supersecretvalue123")
        if self.produce:
            (spec.source_dir / "bin").mkdir(exist_ok=True)
            (spec.source_dir / "bin" / "demo-1.0-debug.apk").write_bytes(b"APKDATA" * 100)
        return SandboxResult(self.exit_code)


class NoDocker:
    def run(self, spec, log, should_cancel):
        raise SandboxUnavailable("Docker is not installed on this worker. Builds need Docker; no build was run.")


def run_one(db, sandbox):
    job = jobs.claim_next(db)
    assert job
    process_job(db, job, sandbox)
    return job


def test_health_and_auth(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/auth/me").status_code == 401
    h, email = make_user(client)
    assert client.get("/api/auth/me", headers=h).json()["email"] == email
    assert client.post("/api/auth/login", json={"email": email, "password": "wrong password!!"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": "correct horse battery"}).status_code == 200
    assert client.post("/api/auth/register", json={"email": email, "password": "correct horse battery"}).status_code == 409
    assert client.post("/api/auth/register", json={"email": "x@y.zz", "password": "short"}).status_code == 422


def test_import_analyze_pipeline(client, kivy_project):
    h, wid, p = kivy_project
    assert "Kivy" in p["analysis"]["frameworks"]  # wrapped zip root detected
    plan = client.get(f"/api/projects/{p['id']}/pipeline", headers=h).json()
    assert plan["adapter"] == "kivy-android" and plan["github_workflow"]
    assert client.get(f"/api/projects/{p['id']}/pipeline?target=android-aab", headers=h).status_code == 422
    assert client.post(f"/api/projects/{p['id']}/analyze", headers=h).status_code == 200


def test_bad_uploads_rejected(client, workspace):
    h, _, wid = workspace
    r = client.post(f"/api/workspaces/{wid}/projects", headers=h, data={"name": "bad"},
                    files={"file": ("p.zip", b"not a zip", "application/zip")})
    assert r.status_code == 422
    evil = make_zip({"../evil.txt": "x"})
    r = client.post(f"/api/workspaces/{wid}/projects", headers=h, data={"name": "evil"},
                    files={"file": ("p.zip", evil, "application/zip")})
    assert r.status_code == 422


def test_rbac_and_isolation(client, kivy_project):
    h, wid, p = kivy_project
    other, _ = make_user(client, "other")
    assert client.get(f"/api/projects/{p['id']}", headers=other).status_code == 404  # no existence leak
    viewer, vemail = make_user(client, "viewer")
    assert client.post(f"/api/workspaces/{wid}/members", headers=h, json={"email": vemail, "role": "viewer"}).status_code == 201
    assert client.get(f"/api/projects/{p['id']}", headers=viewer).status_code == 200
    r = client.post(f"/api/projects/{p['id']}/builds", headers=viewer, json={"accept_sdk_license": True})
    assert r.status_code == 403
    assert client.post(f"/api/workspaces/{wid}/members", headers=viewer, json={"email": vemail, "role": "admin"}).status_code == 403
    assert client.put(f"/api/workspaces/{wid}/secrets", headers=viewer, json={"name": "X", "value": "y"}).status_code == 403


def test_build_requires_license_confirmation(client, kivy_project):
    h, _, p = kivy_project
    r = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={})
    assert r.status_code == 422 and "license" in r.json()["detail"].lower()
    assert client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"target": "android-aab", "accept_sdk_license": True}).status_code == 422


def test_full_build_flow_with_artifact_and_redacted_logs(client, db, kivy_project):
    h, wid, p = kivy_project
    client.put(f"/api/workspaces/{wid}/secrets", headers=h, json={"name": "MY_TOKEN", "value": "supersecretvalue123"})
    b = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True}).json()
    assert b["status"] == "queued" and b["number"] == 1
    run_one(db, FakeSandbox())
    done = client.get(f"/api/builds/{b['id']}", headers=h).json()
    assert done["status"] == "success" and done["duration_seconds"] is not None
    art = done["artifacts"][0]
    assert art["filename"] == "demo-1.0-debug.apk" and art["sha256"] == hashlib.sha256(b"APKDATA" * 100).hexdigest()
    dl = client.get(f"/api/artifacts/{art['id']}/download", headers=h)
    assert dl.content == b"APKDATA" * 100 and dl.headers["x-checksum-sha256"] == art["sha256"]
    logs = client.get(f"/api/builds/{b['id']}/logs", headers=h)
    assert "supersecretvalue123" not in logs.text and "API_KEY=***" in logs.text
    assert logs.headers["x-build-status"] == "success"
    tail = client.get(f"/api/builds/{b['id']}/logs?offset={logs.headers['x-log-next-offset']}", headers=h)
    assert tail.text == ""
    assert "artifact" in client.get(f"/api/builds/{b['id']}/logs?search=artifact", headers=h).text.lower()
    events = [e["event"] for e in client.get(f"/api/workspaces/{wid}/activity", headers=h).json()]
    assert {"build.queued", "build.started", "build.completed", "project.created"} <= set(events)
    secret_list = client.get(f"/api/workspaces/{wid}/secrets", headers=h).json()
    assert secret_list == [{"name": "MY_TOKEN", "environment": "production"}]  # no values


def test_failed_build_and_no_docker_are_reported_honestly(client, db, kivy_project):
    h, _, p = kivy_project
    b1 = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True}).json()
    run_one(db, FakeSandbox(exit_code=2))
    assert client.get(f"/api/builds/{b1['id']}", headers=h).json()["status"] == "failed"
    b2 = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True}).json()
    run_one(db, FakeSandbox(produce=False))
    assert "No artifact" in client.get(f"/api/builds/{b2['id']}", headers=h).json()["error"]
    b3 = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True}).json()
    run_one(db, NoDocker())
    d = client.get(f"/api/builds/{b3['id']}", headers=h).json()
    assert d["status"] == "failed" and "no build was run" in d["error"] and not d["artifacts"]


def test_cancel_queued_build(client, kivy_project):
    h, _, p = kivy_project
    b = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True}).json()
    assert client.post(f"/api/builds/{b['id']}/cancel", headers=h).json()["status"] == "cancelled"
    assert client.post(f"/api/builds/{b['id']}/cancel", headers=h).status_code == 409


def test_automation_creates_scheduled_build(client, db, kivy_project):
    h, _, p = kivy_project
    assert client.post(f"/api/projects/{p['id']}/automations", headers=h,
                       json={"name": "bad", "schedule": "sometime"}).status_code == 422
    a = client.post(f"/api/projects/{p['id']}/automations", headers=h,
                    json={"name": "nightly", "schedule": "every day at 03:00", "accept_sdk_license": True}).json()
    assert a["next_run_at"]
    from buildbix.core.db import utcnow
    from buildbix.models import Automation
    from datetime import timedelta
    row = db.get(Automation, a["id"])
    row.next_run_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert tick(db) == 1
    builds = client.get(f"/api/projects/{p['id']}/builds", headers=h).json()
    assert builds and builds[0]["trigger"] == "automation"
    assert db.get(Automation, a["id"]).next_run_at > utcnow()


def test_github_webhook_signature_and_flow(client, db, kivy_project, monkeypatch):
    h, wid, p = kivy_project
    assert client.post(f"/api/projects/{p['id']}/webhook", headers=h, json={}).status_code == 422  # zip project
    from buildbix.models import Project
    row = db.get(Project, p["id"])
    row.source_kind, row.repo_url, row.branch = "git", "https://github.com/o/r", "main"
    db.commit()
    secret = client.post(f"/api/projects/{p['id']}/webhook", headers=h, json={"accept_sdk_license": True}).json()["secret"]
    synced = []
    monkeypatch.setattr("buildbix.services.projects.sync_project_from_git", lambda d, pr, s=None: synced.append(pr.id))
    body = json.dumps({"ref": "refs/heads/main"}).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    url = f"/api/webhooks/github/{p['id']}"
    hdr = {"X-GitHub-Event": "push", "X-Hub-Signature-256": sig, "Content-Type": "application/json"}
    assert client.post(url, content=body, headers={**hdr, "X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    assert client.post(url, content=body, headers=hdr).json() == {"accepted": True}
    assert synced == [p["id"]]
    other = json.dumps({"ref": "refs/heads/dev"}).encode()
    s2 = "sha256=" + hmac.new(secret.encode(), other, hashlib.sha256).hexdigest()
    assert client.post(url, content=other, headers={**hdr, "X-Hub-Signature-256": s2}).json() == {"ignored": "other branch"}
    builds = client.get(f"/api/projects/{p['id']}/builds", headers=h).json()
    assert any(b["trigger"] == "webhook" for b in builds)


def test_ai_chat_read_only_and_not_configured(client, kivy_project):
    h, _, p = kivy_project
    assert client.post(f"/api/projects/{p['id']}/chat", headers=h, json={"message": "hi"}).status_code == 503

    class Fake(LLMClient):
        def complete(self, system, messages):
            assert "READ-ONLY" in system and "Kivy" in system and "def " not in system  # facts only, no source
            return "This is a Kivy project."

    app.dependency_overrides[get_llm] = lambda: Fake()
    try:
        r = client.post(f"/api/projects/{p['id']}/chat", headers=h, json={"message": "what is this?"})
        assert r.json()["reply"] == "This is a Kivy project."
    finally:
        app.dependency_overrides.clear()
    assert len(client.get(f"/api/projects/{p['id']}/chat", headers=h).json()) == 2
