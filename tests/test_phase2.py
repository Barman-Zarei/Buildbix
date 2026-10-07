import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import inspect, create_engine

from buildbix.api.servers import provider_factory
from buildbix.core.db import Base
from buildbix.main import app
from buildbix.models import Project
from buildbix.providers.base import CloudProvider, ProviderError, ServerInfo
from buildbix.providers.hetzner import HetznerProvider
from buildbix.services import autofix, jobs, vulnscan
from buildbix.services.github_actions import GitHubClient
from buildbix.workers.runner import process_job
from buildbix.workers.sandbox import DockerSandbox, SandboxResult, SandboxSpec
from conftest import KIVY_FILES, make_user, make_zip
from test_api import FakeSandbox, NoDocker


def mk_project(client, h, wid, files, name="p"):
    r = client.post(f"/api/workspaces/{wid}/projects", headers=h, data={"name": name},
                    files={"file": ("p.zip", make_zip(files), "application/zip")})
    assert r.status_code == 201, r.text
    return r.json()


def run_job_for(db, build_id, sandbox, **kw):
    while (job := jobs.claim_next(db)):
        process_job(db, job, sandbox, **kw)
        if job.payload.get("build_id") == build_id:
            return job
    raise AssertionError("job not found")


def to_git(db, pid):
    row = db.get(Project, pid)
    row.source_kind, row.repo_url, row.branch = "git", "https://github.com/o/r", "main"
    db.commit()


# ------------------------------------------------------------------ token revocation
def test_logout_all_revokes_tokens(client):
    h, _ = make_user(client)
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout-all", headers=h).status_code == 204
    assert client.get("/api/auth/me", headers=h).status_code == 401


# ------------------------------------------------------------------ Auto-Fix
BROKEN = {**KIVY_FILES, "buildozer.spec": "[app]\ntitle = D\npackage.name = demo\npackage.domain = org.x\nversion = 1.0\n"
          "requirements = python3,kivy\n\n[buildozer]\nlog_level = 2\n"}


def test_autofix_suggest_apply_and_reanalyze(client, workspace):
    h, _, wid = workspace
    p = mk_project(client, h, wid, BROKEN)
    before = {f["code"] for f in p["analysis"]["findings"]}
    assert {"REQ_NOT_IN_SPEC", "ANDROID_NO_INTERNET"} <= before
    fixes = client.post(f"/api/projects/{p['id']}/fixes/suggest", headers=h, json={}).json()
    assert {f["code"] for f in fixes} == {"add_requirements", "add_internet_permission"}
    again = client.post(f"/api/projects/{p['id']}/fixes/suggest", headers=h, json={}).json()
    assert len(again) == len(fixes)  # deduplicated
    for f in fixes:
        assert client.post(f"/api/fixes/{f['id']}/apply", headers=h).status_code == 200
    after = client.get(f"/api/projects/{p['id']}", headers=h).json()["analysis"]
    assert not {"REQ_NOT_IN_SPEC", "ANDROID_NO_INTERNET"} & {f["code"] for f in after["findings"]}
    assert client.post(f"/api/fixes/{fixes[0]['id']}/apply", headers=h).status_code == 409  # already applied


def test_autofix_is_gated_and_scoped(client, workspace, db):
    h, _, wid = workspace
    p = mk_project(client, h, wid, BROKEN)
    fix = client.post(f"/api/projects/{p['id']}/fixes/suggest", headers=h, json={}).json()[0]
    viewer, vemail = make_user(client, "fxv")
    client.post(f"/api/workspaces/{wid}/members", headers=h, json={"email": vemail, "role": "viewer"})
    assert client.post(f"/api/fixes/{fix['id']}/apply", headers=viewer).status_code == 403
    assert client.post(f"/api/fixes/{fix['id']}/reject", headers=viewer).status_code == 403
    to_git(db, p["id"])
    r = client.post(f"/api/fixes/{fix['id']}/apply", headers=h)
    assert r.status_code == 409 and "repository" in r.json()["detail"]
    with pytest.raises(autofix.FixError):
        autofix.apply_edit(Path("/tmp"), {"file": "../../etc/passwd", "op": "add_requirement", "value": "x"})
    with pytest.raises(autofix.FixError):
        autofix.apply_edit(Path("/tmp"), {"file": "buildozer.spec", "op": "add_requirement", "value": "x;rm -rf /"})


def test_autofix_spec_editing_preserves_file(tmp_path):
    spec = "[app]\ntitle = T\n# android.permissions = CAMERA\nrequirements = python3,Kivy==2.3.0\n\n[buildozer]\nlog_level = 2\n"
    (tmp_path / "buildozer.spec").write_text(spec)
    autofix.apply_edit(tmp_path, {"file": "buildozer.spec", "op": "add_requirement", "value": "kivy"})  # already there
    autofix.apply_edit(tmp_path, {"file": "buildozer.spec", "op": "add_requirement", "value": "requests"})
    autofix.apply_edit(tmp_path, {"file": "buildozer.spec", "op": "add_permission", "value": "internet"})
    out = (tmp_path / "buildozer.spec").read_text()
    assert "requirements = python3,Kivy==2.3.0,requests" in out and "android.permissions = INTERNET" in out
    assert "# android.permissions = CAMERA" in out and out.rstrip().endswith("log_level = 2")


def test_autofix_log_diagnosis(client, db, kivy_project):
    log = "Traceback...\nModuleNotFoundError: No module named 'PIL'\nlicenses have not been accepted\nNo space left on device\n"
    codes = {d["code"]: d for d in autofix.diagnose_log(log)}
    assert codes["missing_module"]["edits"][0]["value"] == "pillow"
    assert {"sdk_license", "disk_full"} <= set(codes) and not codes["disk_full"]["edits"]
    h, _, p = kivy_project
    b = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True}).json()
    run_job_for(db, b["id"], NoDocker())
    fixes = client.post(f"/api/projects/{p['id']}/fixes/suggest", headers=h, json={"build_id": b["id"]}).json()
    nodocker = next(f for f in fixes if f["code"] == "no_docker")
    assert not nodocker["applicable"]
    assert client.post(f"/api/fixes/{nodocker['id']}/apply", headers=h).status_code == 409


# ------------------------------------------------------------------ new targets / adapters
def test_run_tests_target(client, db, workspace):
    h, _, wid = workspace
    p = mk_project(client, h, wid, {"main.py": "x=1\n", "requirements.txt": "pytest\n", "tests/test_x.py": "def test_a(): pass\n"})
    assert any(t["id"] == "run-tests" and t["supported"] for t in p["analysis"]["targets"])
    b = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"target": "run-tests"}).json()  # no license needed
    run_job_for(db, b["id"], FakeSandbox(produce=False))
    d = client.get(f"/api/builds/{b['id']}", headers=h).json()
    assert d["status"] == "success" and d["artifacts"] == []
    b2 = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"target": "run-tests"}).json()
    run_job_for(db, b2["id"], FakeSandbox(exit_code=1, produce=False))
    assert client.get(f"/api/builds/{b2['id']}", headers=h).json()["status"] == "failed"


def test_react_and_flutter_plans(client, workspace):
    h, _, wid = workspace
    react = mk_project(client, h, wid, {"package.json": json.dumps({"dependencies": {"react": "18"}, "devDependencies": {"vite": "5"},
                                                                    "scripts": {"build": "vite build"}}), "package-lock.json": "{}"}, "react")
    plan = client.get(f"/api/projects/{react['id']}/pipeline?target=web-bundle", headers=h).json()
    assert plan["adapter"] == "node-web" and "npm ci" in plan["command"] and "/work/dist" in plan["command"] and plan["experimental"]
    flutter = mk_project(client, h, wid, {"pubspec.yaml": "dependencies:\n  flutter:\n    sdk: flutter\n", "android/app/build.gradle": "x",
                                          "lib/main.dart": "void main(){}"}, "flutter")
    fp = client.get(f"/api/projects/{flutter['id']}/pipeline", headers=h).json()
    assert fp["adapter"] == "flutter-android" and fp["experimental"] and "flutter build apk" in fp["command"]
    nobuild = mk_project(client, h, wid, {"package.json": json.dumps({"dependencies": {"react": "18"}, "devDependencies": {"vite": "5"}})}, "nb")
    assert client.get(f"/api/projects/{nobuild['id']}/pipeline?target=web-bundle", headers=h).status_code == 422


# ------------------------------------------------------------------ GitHub Actions runner (GitHub API is MOCKED)
def gh_mock(state, conclusion="success", dispatch=204):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    apk = make_zip({"app-debug.apk": "APK" * 50})

    def handler(req):
        assert req.headers["authorization"] == "Bearer ghp_" + "t" * 30
        path = req.url.path
        if req.method == "POST" and path.endswith("/dispatches"):
            state["dispatch"] = json.loads(req.content)
            return httpx.Response(dispatch, json={"message": "Not Found"} if dispatch >= 400 else None)
        if path.endswith("/runs"):
            return httpx.Response(200, json={"workflow_runs": [{"id": 77, "created_at": now, "html_url": "https://github.com/o/r/actions/runs/77"}]})
        if path.endswith("/actions/runs/77"):
            state["polls"] = state.get("polls", 0) + 1
            done = state["polls"] > 1
            return httpx.Response(200, json={"status": "completed" if done else "in_progress", "conclusion": conclusion if done else None})
        if path.endswith("/runs/77/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 5, "name": "build", "conclusion": conclusion}]})
        if path.endswith("/jobs/5/logs"):
            return httpx.Response(200, text="step ok\nusing MY_TOKEN supersecretvalue123 here\nghp_" + "t" * 30 + "\n")
        if path.endswith("/runs/77/artifacts"):
            return httpx.Response(200, json={"artifacts": [{"id": 9, "name": "buildbix-artifacts", "expired": False}]})
        if path.endswith("/artifacts/9/zip"):
            return httpx.Response(200, content=apk)
        return httpx.Response(404, json={"message": "unexpected " + path})

    return GitHubClient("ghp_" + "t" * 30, transport=httpx.MockTransport(handler))


def gha_project(client, db, workspace):
    h, _, wid = workspace
    p = mk_project(client, h, wid, KIVY_FILES, "gh")
    to_git(db, p["id"])
    client.put(f"/api/workspaces/{wid}/secrets", headers=h, json={"name": "MY_TOKEN", "value": "supersecretvalue123"})
    return h, wid, p


def test_gha_requires_git_project_and_token(client, db, workspace):
    h, _, wid = workspace
    p = mk_project(client, h, wid, KIVY_FILES, "zipproj")
    r = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True, "runner": "github_actions"})
    assert r.status_code == 422 and "GitHub project" in r.json()["detail"]
    to_git(db, p["id"])
    r = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True, "runner": "github_actions"})
    assert r.status_code == 422 and "GITHUB_TOKEN" in r.json()["detail"]


def test_gha_build_success_stores_artifact_and_redacts(client, db, workspace):
    h, wid, p = gha_project(client, db, workspace)
    client.put(f"/api/workspaces/{wid}/secrets", headers=h, json={"name": "GITHUB_TOKEN", "value": "ghp_" + "t" * 30})
    b = client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True, "runner": "github_actions"}).json()
    assert b["runner"] == "github_actions"
    state = {}
    run_job_for(db, b["id"], NoDocker(), gh_client=gh_mock(state), poll=0)  # local sandbox must NOT be used
    d = client.get(f"/api/builds/{b['id']}", headers=h).json()
    assert d["status"] == "success", d
    assert state["dispatch"] == {"ref": "main"}
    assert d["artifacts"][0]["sha256"] == hashlib.sha256(b"APK" * 50).hexdigest()
    logs = client.get(f"/api/builds/{b['id']}/logs", headers=h).text
    assert "supersecretvalue123" not in logs and "ghp_" not in logs and "actions/runs/77" in logs


def test_gha_failures_are_reported(client, db, workspace):
    h, wid, p = gha_project(client, db, workspace)
    client.put(f"/api/workspaces/{wid}/secrets", headers=h, json={"name": "GITHUB_TOKEN", "value": "ghp_" + "t" * 30})
    mk = lambda: client.post(f"/api/projects/{p['id']}/builds", headers=h, json={"accept_sdk_license": True, "runner": "github_actions"}).json()
    b1 = mk()
    run_job_for(db, b1["id"], NoDocker(), gh_client=gh_mock({}, conclusion="failure"), poll=0)
    d1 = client.get(f"/api/builds/{b1['id']}", headers=h).json()
    assert d1["status"] == "failed" and "failure" in d1["error"]
    b2 = mk()
    run_job_for(db, b2["id"], NoDocker(), gh_client=gh_mock({}, dispatch=404), poll=0)
    d2 = client.get(f"/api/builds/{b2['id']}", headers=h).json()
    assert d2["status"] == "failed" and "not found" in d2["error"].lower()


# ------------------------------------------------------------------ dependency scan (OSV is MOCKED)
def test_vulnerability_scan(client, workspace, monkeypatch):
    h, _, wid = workspace
    p = mk_project(client, h, wid, {"main.py": "x=1\n", "requirements.txt": "flask==0.5\nrequests==2.31.0\nnumpy\n# c\n-r other.txt\n"})
    seen = {}

    def fake(queries):
        seen["q"] = queries
        return [["GHSA-xxxx"], []]

    monkeypatch.setattr(vulnscan, "query_osv", fake)
    r = client.get(f"/api/projects/{p['id']}/scan", headers=h).json()
    assert r["checked"] == 2 and r["unpinned"] == ["numpy"]
    assert r["vulnerable"] == [{"package": "flask", "version": "0.5", "advisories": ["GHSA-xxxx"]}]
    assert seen["q"][0] == {"package": {"name": "flask", "ecosystem": "PyPI"}, "version": "0.5"}

    def boom(_q):
        raise vulnscan.ScanError("The vulnerability database could not be reached")

    monkeypatch.setattr(vulnscan, "query_osv", boom)
    assert client.get(f"/api/projects/{p['id']}/scan", headers=h).status_code == 502


# ------------------------------------------------------------------ servers / CloudProvider (provider is MOCKED)
class FakeProvider(CloudProvider):
    name = "hetzner"
    calls: list = []

    def create_server(self, name, server_type, region, image):
        FakeProvider.calls.append(("create", name, server_type, region))
        return ServerInfo("123", "initializing", "203.0.113.7")

    def get_server(self, provider_id):
        return ServerInfo(provider_id, "running", "203.0.113.7")

    def delete_server(self, provider_id):
        FakeProvider.calls.append(("delete", provider_id))


def test_server_lifecycle_guards(client, workspace):
    h, _, wid = workspace
    app.dependency_overrides[provider_factory] = lambda: (lambda name, token: FakeProvider())
    try:
        body = {"name": "api-1", "server_type": "small", "region": "eu"}
        assert client.post(f"/api/workspaces/{wid}/servers", headers=h, json=body).status_code == 422  # cost not confirmed
        r = client.post(f"/api/workspaces/{wid}/servers", headers=h, json={**body, "confirm_cost": True})
        assert r.status_code == 422 and "HETZNER_TOKEN" in r.json()["detail"]
        client.put(f"/api/workspaces/{wid}/secrets", headers=h, json={"name": "HETZNER_TOKEN", "value": "tok-123456"})
        dev, demail = make_user(client, "srvdev")
        client.post(f"/api/workspaces/{wid}/members", headers=h, json={"email": demail, "role": "developer"})
        assert client.post(f"/api/workspaces/{wid}/servers", headers=dev, json={**body, "confirm_cost": True}).status_code == 403
        s = client.post(f"/api/workspaces/{wid}/servers", headers=h, json={**body, "confirm_cost": True}).json()
        assert s["ip"] == "203.0.113.7"
        assert client.post(f"/api/workspaces/{wid}/servers", headers=h, json={**body, "confirm_cost": True}).status_code == 409
        assert client.post(f"/api/servers/{s['id']}/refresh", headers=h).json()["status"] == "running"
        assert client.delete(f"/api/servers/{s['id']}", headers=h).status_code == 422
        assert client.delete(f"/api/servers/{s['id']}?confirm=wrong", headers=h).status_code == 422
        assert ("delete", "123") not in FakeProvider.calls
        assert client.delete(f"/api/servers/{s['id']}?confirm=api-1", headers=h).status_code == 204
        assert ("delete", "123") in FakeProvider.calls and client.get(f"/api/workspaces/{wid}/servers", headers=h).json() == []
        ev = [e["event"] for e in client.get(f"/api/workspaces/{wid}/activity", headers=h).json()]
        assert {"server.created", "server.deleted"} <= set(ev)
    finally:
        app.dependency_overrides.clear()


def test_hetzner_provider_request_shapes():
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, json.loads(req.content) if req.content else None, req.headers["authorization"]))
        if req.method == "POST":
            return httpx.Response(201, json={"server": {"id": 42, "status": "initializing", "public_net": {"ipv4": {"ip": "1.2.3.4"}}}})
        if req.url.path.endswith("/999"):
            return httpx.Response(401, json={"error": {"message": "unauthorized"}})
        return httpx.Response(200, json={"server": {"id": 42, "status": "running", "public_net": {}}})

    p = HetznerProvider("secret-token", transport=httpx.MockTransport(handler))
    info = p.create_server("a", "t1", "r1", "ubuntu-24.04")
    assert info.provider_id == "42" and info.ip == "1.2.3.4"
    assert seen[0][2] == {"name": "a", "server_type": "t1", "location": "r1", "image": "ubuntu-24.04", "start_after_create": True}
    assert p.get_server("42").ip is None
    with pytest.raises(ProviderError) as e:
        p.get_server("999")
    assert "secret-token" not in str(e.value) and "401" in str(e.value)


# ------------------------------------------------------------------ sandbox hardening flags + network/proxy hooks
def test_docker_sandbox_arguments(tmp_path):
    sb, captured = DockerSandbox(), []
    sb._ensure = lambda: None
    sb._stream = lambda args, *a, **k: captured.append(args) or SandboxResult(0)
    sb.run(SandboxSpec("n", tmp_path, tmp_path / "c", "node:20", None, "echo hi", 10, network="bbx-egress", proxy="http://proxy:3128"),
           lambda _l: None, lambda: False)
    a = captured[0]
    pairs = list(zip(a, a[1:]))
    assert ("--cap-drop", "ALL") in pairs and ("--security-opt", "no-new-privileges") in pairs
    assert ("--network", "bbx-egress") in pairs and ("-e", "HTTPS_PROXY=http://proxy:3128") in pairs
    assert ("--user", f"{os.getuid()}:{os.getgid()}") in pairs and "--privileged" not in a and "--memory" in a


# ------------------------------------------------------------------ Alembic migrations match the models
def test_alembic_upgrade_matches_models(tmp_path):
    backend = Path(__file__).resolve().parent.parent / "backend"
    env = {**os.environ, "BUILDBIX_DATA_DIR": str(tmp_path), "BUILDBIX_DATABASE_URL": f"sqlite:///{tmp_path / 'm.db'}",
           "BUILDBIX_AUTO_CREATE_TABLES": "0", "BUILDBIX_SECRET_KEY": "x"}
    for cmd in (["upgrade", "head"], ["check"]):  # 'check' fails if models and migrations drift apart
        r = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", *cmd], cwd=backend, env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    tables = set(inspect(create_engine(env["BUILDBIX_DATABASE_URL"])).get_table_names())
    assert tables == set(Base.metadata.tables) | {"alembic_version"}
