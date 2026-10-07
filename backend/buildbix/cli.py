"""BUILDBIX CLI. Thin client over the same HTTP API the web UI uses."""
import argparse
import getpass
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

TERMINAL = {"success", "failed", "cancelled"}


def _cfg_path() -> Path:
    return Path(os.getenv("BUILDBIX_CLI_CONFIG") or Path.home() / ".buildbix" / "config.json")


def load_cfg() -> dict:
    p = _cfg_path()
    return json.loads(p.read_text()) if p.exists() else {}


def save_cfg(cfg: dict) -> None:
    p = _cfg_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg))
    p.chmod(0o600)  # contains the access token


def _client() -> httpx.Client:
    cfg = load_cfg()
    headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {}
    return httpx.Client(base_url=cfg.get("url", "http://127.0.0.1:8000").rstrip("/") + "/api", headers=headers, timeout=60)


def call(method: str, path: str, **kw) -> httpx.Response:
    with _client() as c:
        r = c.request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        sys.exit(f"Error {r.status_code}: {detail}")
    return r


def out(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_login(a):
    cfg = {"url": a.url}
    save_cfg(cfg)
    pw = os.getenv("BUILDBIX_PASSWORD") or getpass.getpass("Password: ")
    r = call("POST", "/auth/register" if a.register else "/auth/login", json={"email": a.email, "password": pw})
    save_cfg({"url": a.url, "token": r.json()["access_token"]})
    print(f"Logged in as {a.email}")


def cmd_logout(_a):
    save_cfg({"url": load_cfg().get("url", "http://127.0.0.1:8000")})
    print("Logged out")


def cmd_workspaces(a):
    if a.create:
        out(call("POST", "/workspaces", json={"name": a.create}).json())
    else:
        for w in call("GET", "/workspaces").json():
            print(f"{w['id']}\t{w['role']}\t{w['name']}")


def cmd_projects(a):
    for p in call("GET", f"/workspaces/{a.workspace}/projects").json():
        an = p["analysis"]
        print(f"{p['id']}\t{p['name']}\t{', '.join(an.get('frameworks', [])) or '-'}")


def cmd_import(a):
    with open(a.file, "rb") as f:
        r = call("POST", f"/workspaces/{a.workspace}/projects", data={"name": a.name}, files={"file": (Path(a.file).name, f)})
    _print_analysis(r.json())


def cmd_import_git(a):
    r = call("POST", f"/workspaces/{a.workspace}/projects/git", json={"name": a.name, "repo_url": a.url, "branch": a.branch})
    _print_analysis(r.json())


def _print_analysis(p: dict) -> None:
    an = p["analysis"]
    print(f"Project #{p['id']} '{p['name']}'")
    print("Languages:", ", ".join(an.get("languages", [])) or "-")
    print("Frameworks:", ", ".join(an.get("frameworks", [])) or "-")
    print("Build systems:", ", ".join(an.get("build_systems", [])) or "-")
    for t in an.get("targets", []):
        print(f"  target {t['id']}: {'ready' if t['supported'] else 'not available - ' + str(t['reason'])}")
    for f in an.get("findings", []):
        print(f"  [{f['severity']}] {f['message']}")


def cmd_analyze(a):
    _print_analysis(call("POST", f"/projects/{a.project}/analyze").json())


def cmd_pipeline(a):
    plan = call("GET", f"/projects/{a.project}/pipeline", params={"target": a.target}).json()
    print(plan["github_workflow"] if a.workflow else json.dumps({k: v for k, v in plan.items() if k != "github_workflow"}, indent=2))


def cmd_build(a):
    b = call("POST", f"/projects/{a.project}/builds",
             json={"target": a.target, "accept_sdk_license": a.accept_sdk_license, "runner": a.runner}).json()
    print(f"Build #{b['number']} (id {b['id']}) {b['status']}")
    if a.wait:
        follow(b["id"])


def cmd_builds(a):
    for b in call("GET", f"/projects/{a.project}/builds").json():
        print(f"{b['id']}\t#{b['number']}\t{b['status']}\t{b['target']}\t{b['duration_seconds'] or '-'}s")


def follow(build_id: int, interval: float = 2.0) -> None:
    offset = 0
    while True:
        r = call("GET", f"/builds/{build_id}/logs", params={"offset": offset})
        if r.text:
            print(r.text, end="")
        offset = int(r.headers["x-log-next-offset"])
        if r.headers["x-build-status"] in TERMINAL and not r.text:
            b = call("GET", f"/builds/{build_id}").json()
            print(f"\nBuild {b['status']}" + (f": {b['error']}" if b["error"] else ""))
            for art in b["artifacts"]:
                print(f"artifact {art['id']}: {art['filename']} sha256={art['sha256']}")
            sys.exit(0 if b["status"] == "success" else 1)
        time.sleep(interval)


def cmd_logs(a):
    if a.follow:
        follow(a.build)
    else:
        print(call("GET", f"/builds/{a.build}/logs", params={"errors_only": a.errors_only}).text, end="")


def cmd_cancel(a):
    out(call("POST", f"/builds/{a.build}/cancel").json())


def cmd_download(a):
    r = call("GET", f"/artifacts/{a.artifact}/download")
    expected = r.headers["x-checksum-sha256"]
    if hashlib.sha256(r.content).hexdigest() != expected:
        sys.exit("Checksum mismatch - file discarded")
    dest = Path(a.output or f"artifact-{a.artifact}")
    dest.write_bytes(r.content)
    print(f"Saved {dest} (sha256 verified)")


def cmd_automate(a):
    out(call("POST", f"/projects/{a.project}/automations", json={
        "name": a.name, "schedule": a.schedule, "event": a.event, "target": a.target, "accept_sdk_license": a.accept_sdk_license}).json())


def cmd_chat(a):
    print(call("POST", f"/projects/{a.project}/chat", json={"message": a.message}).json()["reply"])


def cmd_fixes(a):
    for f in call("POST", f"/projects/{a.project}/fixes/suggest", json={"build_id": a.build}).json():
        print(f"fix {f['id']} [{'applicable' if f['applicable'] else 'info only'}] {f['title']}\n    {f['explanation']}")


def cmd_apply_fix(a):
    if not a.yes:
        sys.exit("Auto-Fix changes your project source. Re-run with --yes to approve.")
    out(call("POST", f"/fixes/{a.fix}/apply").json())


def cmd_scan(a):
    out(call("GET", f"/projects/{a.project}/scan").json())


def cmd_server_create(a):
    if not a.confirm_cost:
        sys.exit("Creating a server is billable at your provider. Re-run with --confirm-cost.")
    out(call("POST", f"/workspaces/{a.workspace}/servers", json={"name": a.name, "server_type": a.type, "region": a.region,
                                                                  "confirm_cost": True}).json())


def cmd_servers(a):
    for s in call("GET", f"/workspaces/{a.workspace}/servers").json():
        print(f"{s['id']}\t{s['name']}\t{s['status']}\t{s['ip'] or '-'}\t{s['provider']}/{s['server_type']}/{s['region']}")


def cmd_server_delete(a):
    call("DELETE", f"/servers/{a.server}", params={"confirm": a.confirm})
    print("Deleted")


def cmd_catalog(_a):
    for i in call("GET", "/marketplace").json():
        print(f"{i['id']}\t{i['type']}\t{i.get('status') or ('verified' if i['verified'] else 'unverified')}\t{i['title']}")


def cmd_from_starter(a):
    _print_analysis(call("POST", f"/workspaces/{a.workspace}/projects/from-starter", json={"starter": a.starter, "name": a.name}).json())


def cmd_ci(a):
    r = call("GET", f"/projects/{a.project}/ci", params={"target": a.target}).json()
    print(f"# template {r['template']} [{r['status']}] file: {r['workflow_file']}\n# params: {json.dumps(r['params'])}\n{r['workflow']}")


def cmd_ci_adapt(a):
    r = call("POST", f"/projects/{a.project}/ci/adapt", json={"target": a.target}).json()
    print("Proposed:", json.dumps(r["proposed_params"]), "| rejected:", json.dumps(r["rejected"]))
    print(r["diff"] or "(no differences from the template)")
    if a.save and r["proposed_params"]:
        call("PUT", f"/projects/{a.project}/ci", json={"target": a.target, "params": r["proposed_params"]})
        print("Saved.")


def cmd_ci_commit(a):
    if not a.approve:
        sys.exit("This creates a commit in your GitHub repository. Re-run with --approve.")
    out(call("POST", f"/projects/{a.project}/ci/commit", json={"target": a.target, "approve": True, "overwrite": a.overwrite}).json())


def cmd_ai(a):
    r = call("POST", f"/projects/{a.project}/ai", json={"message": a.message, "share_source": a.share_source}).json()
    print(f"[{r['agent']}] {r['reply']}")
    if r.get("proposal"):
        print(f"proposal {r['proposal']['id']}: review with GET /api/fixes/{r['proposal']['id']}, apply with: buildbix apply-fix {r['proposal']['id']} --yes")
    if r.get("preview"):
        print(r["preview"]["diff"])


def cmd_ai_project(a):
    r = call("POST", f"/workspaces/{a.workspace}/ai/project", json={"name": a.name, "description": a.description}).json()
    print(r["warning"])
    _print_analysis(r)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="buildbix", description="BUILDBIX - Build anything, simply.")
    s = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, *args, **flags):
        sp = s.add_parser(name, help=help_)
        for arg in args:
            sp.add_argument(arg[0], **arg[1]) if isinstance(arg, tuple) else sp.add_argument(arg)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("login", cmd_login, "Log in (or --register)", ("--url", {"default": "http://127.0.0.1:8000"}), "email")
    sp.add_argument("--register", action="store_true")
    add("logout", cmd_logout, "Forget the saved token")
    sp = add("workspaces", cmd_workspaces, "List workspaces, or --create NAME")
    sp.add_argument("--create")
    add("projects", cmd_projects, "List projects", ("workspace", {"type": int}))
    add("import", cmd_import, "Import a zip", ("workspace", {"type": int}), "name", "file")
    sp = add("import-git", cmd_import_git, "Import a GitHub repo", ("workspace", {"type": int}), "name", "url")
    sp.add_argument("--branch", default="main")
    add("analyze", cmd_analyze, "Re-analyze a project", ("project", {"type": int}))
    sp = add("pipeline", cmd_pipeline, "Show the generated build plan", ("project", {"type": int}))
    sp.add_argument("--target", default="android-apk"); sp.add_argument("--workflow", action="store_true")
    sp = add("build", cmd_build, "Start a build", ("project", {"type": int}))
    sp.add_argument("--target", default="android-apk"); sp.add_argument("--wait", action="store_true")
    sp.add_argument("--accept-sdk-license", action="store_true", help="confirm you accept the Android SDK license")
    sp.add_argument("--runner", choices=["docker", "github_actions"], default="docker")
    add("builds", cmd_builds, "List builds", ("project", {"type": int}))
    sp = add("logs", cmd_logs, "Show build logs", ("build", {"type": int}))
    sp.add_argument("--follow", "-f", action="store_true"); sp.add_argument("--errors-only", action="store_true")
    add("cancel", cmd_cancel, "Cancel a build", ("build", {"type": int}))
    sp = add("download", cmd_download, "Download an artifact (verifies SHA-256)", ("artifact", {"type": int}))
    sp.add_argument("-o", "--output")
    sp = add("automate", cmd_automate, "Schedule builds ('every day at 03:00', UTC) or use --event", ("project", {"type": int}), "name", ("schedule", {"nargs": "?"}))
    sp.add_argument("--event", choices=["push", "tag", "release"])
    sp.add_argument("--target", default="android-apk"); sp.add_argument("--accept-sdk-license", action="store_true")
    sp = add("fixes", cmd_fixes, "Suggest fixes from analysis (and a failed build's log)", ("project", {"type": int}))
    sp.add_argument("--build", type=int)
    sp = add("apply-fix", cmd_apply_fix, "Approve and apply a suggested fix", ("fix", {"type": int}))
    sp.add_argument("--yes", action="store_true")
    add("scan", cmd_scan, "Scan pinned dependencies for known vulnerabilities (OSV)", ("project", {"type": int}))
    sp = add("server-create", cmd_server_create, "Create a cloud server (billable)", ("workspace", {"type": int}), "name")
    sp.add_argument("--type", required=True); sp.add_argument("--region", required=True); sp.add_argument("--confirm-cost", action="store_true")
    add("servers", cmd_servers, "List servers", ("workspace", {"type": int}))
    sp = add("server-delete", cmd_server_delete, "Delete a server (type its exact name to confirm)", ("server", {"type": int}))
    sp.add_argument("--confirm", required=True)
    add("catalog", cmd_catalog, "List starters and CI templates")
    add("from-starter", cmd_from_starter, "Create a project from a starter (see catalog)", ("workspace", {"type": int}), "starter", "name")
    sp = add("ci", cmd_ci, "Show the CI workflow BUILDBIX would use", ("project", {"type": int}))
    sp.add_argument("--target", default="android-apk")
    sp = add("ci-adapt", cmd_ci_adapt, "Ask the AI to tune template parameters (preview; --save to keep)", ("project", {"type": int}))
    sp.add_argument("--target", default="android-apk"); sp.add_argument("--save", action="store_true")
    sp = add("ci-commit", cmd_ci_commit, "Commit the workflow into your GitHub repo", ("project", {"type": int}))
    sp.add_argument("--target", default="android-apk"); sp.add_argument("--approve", action="store_true"); sp.add_argument("--overwrite", action="store_true")
    sp = add("ai", cmd_ai, "Talk to the AI coordinator (code/docs/ci/test/analysis agents)", ("project", {"type": int}), "message")
    sp.add_argument("--share-source", action="store_true", help="allow sending your text files to the AI provider")
    add("ai-project", cmd_ai_project, "Generate a new project from a description", ("workspace", {"type": int}), "name", "description")
    add("chat", cmd_chat, "Ask the project assistant", ("project", {"type": int}), "message")
    return p


def main(argv=None) -> None:
    a = parser().parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
