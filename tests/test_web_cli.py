import hashlib

import pytest
from fastapi.testclient import TestClient

from buildbix import cli
from buildbix.main import app
from buildbix.services import jobs
from buildbix.workers.runner import process_job
from conftest import KIVY_FILES, make_zip
from test_api import FakeSandbox


def test_web_ui_served_with_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "BUILDBIX" in r.text and "/app.js" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
    js = client.get("/app.js").text
    assert "innerHTML" not in js  # DOM built via textContent only
    assert client.get("/docs").status_code == 200
    assert "content-security-policy" not in client.get("/docs").headers  # Swagger UI needs CDN assets
    assert client.get("/api/health").status_code == 200  # static mount does not shadow the API


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BUILDBIX_CLI_CONFIG", str(tmp_path / "cfg.json"))
    monkeypatch.setenv("BUILDBIX_PASSWORD", "correct horse battery")

    def factory():
        cfg = cli.load_cfg()
        h = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {}
        return TestClient(app, base_url="http://testserver/api", headers=h)

    monkeypatch.setattr(cli, "_client", factory)
    return tmp_path


def test_cli_end_to_end(cli_env, db, capsys):
    zp = cli_env / "p.zip"
    zp.write_bytes(make_zip(KIVY_FILES))
    cli.main(["login", "--register", "--url", "http://testserver", "cli-user@example.com"])
    cli.main(["workspaces", "--create", "CLI"])
    capsys.readouterr()
    cli.main(["workspaces"])
    wid = int(capsys.readouterr().out.split("\t")[0])
    cli.main(["import", str(wid), "demo", str(zp)])
    assert "Kivy" in capsys.readouterr().out
    cli.main(["projects", str(wid)])
    pid = int(capsys.readouterr().out.split("\t")[0])
    with pytest.raises(SystemExit) as e:  # license not confirmed -> refused
        cli.main(["build", str(pid)])
    assert "license" in str(e.value).lower()
    cli.main(["build", str(pid), "--accept-sdk-license"])
    assert "queued" in capsys.readouterr().out
    while (job := jobs.claim_next(db)):  # the shared test queue may hold jobs from earlier tests
        process_job(db, job, FakeSandbox())
    cli.main(["builds", str(pid)])
    line = capsys.readouterr().out.split("\t")
    bid, status = int(line[0]), line[2]
    assert status == "success"
    with pytest.raises(SystemExit) as e:
        cli.main(["logs", str(bid), "--follow"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "supersecret" not in out and "sha256=" in out
    aid = int(out.split("artifact ")[1].split(":")[0])
    cli.main(["download", str(aid), "-o", str(cli_env / "app.apk")])
    assert hashlib.sha256((cli_env / "app.apk").read_bytes()).hexdigest() == hashlib.sha256(b"APKDATA" * 100).hexdigest()
    cli.main(["automate", str(pid), "nightly", "every day at 03:00", "--accept-sdk-license"])
    assert "next_run_at" in capsys.readouterr().out
    assert oct((cli_env / "cfg.json").stat().st_mode & 0o777) == "0o600"  # token file is private


def test_cli_fixes_and_guarded_commands(cli_env, capsys):
    from test_phase2 import BROKEN
    zp = cli_env / "b.zip"
    zp.write_bytes(make_zip(BROKEN))
    cli.main(["login", "--register", "--url", "http://testserver", "cli-fix@example.com"])
    cli.main(["workspaces", "--create", "W"])
    capsys.readouterr()
    cli.main(["workspaces"])
    wid = int(capsys.readouterr().out.split("\t")[0])
    cli.main(["import", str(wid), "broken", str(zp)])
    capsys.readouterr()
    cli.main(["projects", str(wid)])
    pid = int(capsys.readouterr().out.split("\t")[0])
    cli.main(["fixes", str(pid)])
    out = capsys.readouterr().out
    assert "Add missing packages" in out and "applicable" in out
    with pytest.raises(SystemExit) as e:
        cli.main(["apply-fix", "1"])  # no --yes: must refuse before calling the API
    assert "--yes" in str(e.value)
    with pytest.raises(SystemExit) as e:
        cli.main(["server-create", str(wid), "s1", "--type", "x", "--region", "y"])
    assert "billable" in str(e.value)
