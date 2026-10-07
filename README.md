# BUILDBIX — Build anything, simply.

![status: early prototype](https://img.shields.io/badge/status-early%20prototype-orange) ![license: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)

> **Status: early prototype.** Everything is covered by automated tests, but external services (GitHub, Docker builds, Hetzner, OSV, the AI provider)
> are **mocked** in those tests. Real builds and the CI templates are **not yet verified**; see `docs/ROADMAP.md`. Do not expose it to the
> public internet without hardening (`docs/SECURITY.md`). Contributions welcome: `CONTRIBUTING.md`.

AI-assisted cloud build platform. **MVP v0.1 (backend + web UI + CLI)**: auth, workspaces, project
import (zip + GitHub), project analyzer, build-plan/CI generation, isolated build worker, artifacts, live logs,
secrets, automations, webhooks, read-only AI chat, audit log, a dependency-free web dashboard (served at `/`) and the `buildbix` CLI. See `docs/ROADMAP.md` for what is not built yet.

## Run it
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export BUILDBIX_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
cd backend
uvicorn buildbix.main:app --reload          # API + docs at http://127.0.0.1:8000/docs
python -m buildbix.workers                  # in a second terminal: build worker + scheduler
```
Android builds need **Docker** on the worker machine (about 10 GB free; first build downloads the SDK/NDK).
Without Docker the build fails with a clear message — nothing is faked.
AI chat needs `BUILDBIX_AI_API_KEY` (server-side env var; never sent to clients or stored in the repo).

## Web UI and CLI
Open http://127.0.0.1:8000/ for the dashboard (login, workspaces, zip import, analysis, build buttons, live logs,
artifact download, automations, read-only AI chat). The UI is plain JS (no build step) and has **not been tested in a real
browser by me** — only served and syntax-checked.
```bash
buildbix login --register --url http://127.0.0.1:8000 me@example.com
buildbix workspaces --create MyShop ; buildbix import 1 demo project.zip ; buildbix build 1 --accept-sdk-license --wait
buildbix logs 1 -f ; buildbix download 1 -o app.apk ; buildbix automate 1 nightly "every day at 03:00"
```

## Try it (curl)
```bash
B=http://127.0.0.1:8000/api
TOKEN=$(curl -s $B/auth/register -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a long passphrase"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
H="Authorization: Bearer $TOKEN"
WS=$(curl -s $B/workspaces -H "$H" -H 'content-type: application/json' -d '{"name":"MyShop"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -s $B/workspaces/$WS/projects -H "$H" -F name=demo -F file=@my_kivy_project.zip     # analyzes on upload
curl -s $B/projects/1/pipeline -H "$H"                                                    # inspect the plan
curl -s $B/projects/1/builds -H "$H" -H 'content-type: application/json' -d '{"accept_sdk_license":true}'
curl -s "$B/builds/1/logs?offset=0" -H "$H"                                               # poll with X-Log-Next-Offset
```

## Tests
`pytest` (50 tests, incl. CLI end-to-end, GitHub/Hetzner/OSV mocks, Alembic drift check: analyzer, archive safety, redaction, RBAC, build flow, webhooks, automations, AI permissions).
The build-flow tests use a **fake sandbox test double** to exercise queue/worker/artifact logic without Docker;
the real `DockerSandbox` is not exercised by the test suite.

## Layout
`backend/buildbix/{core,analyzer,pipeline,services,workers,automation,ai,api}` — see `docs/ARCHITECTURE.md`.

## Build on GitHub Actions instead of your own Docker
1. Import your project from GitHub (`import-git`). 2. Commit the workflow printed by `buildbix pipeline <id> --workflow`
to your repo as `.github/workflows/<workflow_file>` (the file name is in the plan; `GET /projects/{id}/pipeline`).
3. Store a GitHub token as workspace secret `GITHUB_TOKEN` (fine-grained: Actions read/write + Contents read).
4. `buildbix build <id> --runner github_actions --accept-sdk-license --wait` — BUILDBIX dispatches the workflow, polls the run,
copies the job logs (redacted) and the `buildbix-artifacts` artifact into its own storage. GitHub deletes artifacts after its retention period; BUILDBIX keeps its copy.
Windows/macOS/Linux installers are **not** built yet: they need new adapters whose workflows use `windows-latest` / `macos-latest` / `ubuntu-latest` runners.

## Auto-Fix, scan, servers
- `buildbix fixes <project> [--build <id>]` suggests fixes; `buildbix apply-fix <id> --yes` applies one (zip projects only; edits are limited to `buildozer.spec`). Nothing changes without that explicit approval.
- `buildbix scan <project>` checks exact-pinned `requirements.txt` entries against OSV (needs internet).
- `buildbix server-create ... --confirm-cost` (Hetzner; needs `HETZNER_TOKEN` secret; **billable**) and `server-delete --confirm <name>`.

## Production database migrations
```bash
export BUILDBIX_DATABASE_URL=postgresql+psycopg://user:pass@host/db BUILDBIX_AUTO_CREATE_TABLES=0
cd backend && alembic upgrade head      # (install a driver such as psycopg yourself)
```

## CI templates + AI (the design you asked for)
Workflows come from **pre-approved template files** (`backend/buildbix/ci/templates/`). The AI only returns a JSON object of
whitelisted parameters (e.g. Python version, extra apt packages); everything is validated, and the AI can never add a command.
`buildbix ci <id>` shows the result, `buildbix ci-adapt <id> [--save]` previews the AI's parameters + diff, `buildbix ci-commit <id> --approve`
commits the workflow to your repo. **Read `docs/YOUR_TODO.md` for what you must paste/create yourself.**

## AI agents (all need `BUILDBIX_AI_API_KEY`)
`buildbix ai <id> "add dark mode" --share-source` routes to a code/docs/ci/test/analysis agent. Code/docs agents only create a
**proposal**; you review (`GET /api/fixes/{id}`) and approve (`buildbix apply-fix <id> --yes`). `buildbix ai-project <ws> <name> "<description>"`
generates a new project (never executed). `buildbix catalog` / `from-starter` give starters (kivy, fastapi, vite-react).

## Events
Webhook `push` (project branch) builds; `push` of a tag and `release: published` fire automations created with `--event tag|release|push`
(they build the branch's latest commit, not the tag itself). Pull-request events are not supported.

## License
Apache License 2.0 — see `LICENSE` and `NOTICE`. Contributions are accepted under the same license with a DCO sign-off (`git commit -s`); see `CONTRIBUTING.md`.
