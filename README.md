<div align="center">

<img src="docs/assets/file_00000000ceac81f5a62e90eacc8c91eb.png" alt="BuildBix — concept artwork" width="100%">

# BUILDBIX
### Build anything, simply.

**An AI-assisted cloud build platform for developers.**

[![Status: Early Prototype](https://img.shields.io/badge/status-early%20prototype-orange)](docs/ROADMAP.md)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)

[Getting Started](#-getting-started) ·
[Features](#-what-buildbix-includes) ·
[Architecture](#-project-structure) ·
[Roadmap](docs/ROADMAP.md) ·
[Contributing](CONTRIBUTING.md)

</div>

> [!WARNING]
> **BuildBix is an early prototype, not a production-ready service.** The automated test suite uses mocks for external services such as GitHub, Docker builds, Hetzner, OSV, and the AI provider. Real builds and CI templates have not yet been verified end to end. Review [`docs/ROADMAP.md`](docs/ROADMAP.md) for current limitations. Do not expose an instance to the public internet without additional security hardening; see [`docs/SECURITY.md`](docs/SECURITY.md).

> **Artwork note:** The banner above is concept artwork for the project, not a screenshot of the current application or proof of implemented features.

---

## Table of contents

- [Overview](#-overview)
- [What BuildBix includes](#-what-buildbix-includes)
- [Getting started](#-getting-started)
- [Web dashboard and CLI](#-web-dashboard-and-cli)
- [Try the API](#-try-the-api-with-curl)
- [Testing and verification](#-testing-and-verification)
- [GitHub Actions builds](#-github-actions-builds)
- [Additional capabilities](#-additional-capabilities)
- [Production database migrations](#-production-database-migrations)
- [Project structure](#-project-structure)
- [Security](#-security)
- [Roadmap and contribution](#-roadmap-and-contribution)
- [License](#-license)

## 🚀 Overview

BuildBix is an AI-assisted cloud build platform designed to bring project import, analysis, build planning, execution, logs, and artifacts into one workflow.

The current **MVP v0.1** includes a backend, a lightweight web dashboard, and a command-line interface (CLI). It is an early prototype: some integrations are represented by mocks in automated tests, and real-world build infrastructure still needs end-to-end verification.

### Design goals

- **One workflow:** import a project, inspect its build plan, start a build, and retrieve its artifacts.
- **Automation:** generate build plans and CI workflow suggestions from project analysis.
- **Visibility:** follow build logs and review generated artifacts.
- **Controlled AI assistance:** keep AI output constrained and require explicit approval for proposed changes.
- **Extensibility:** connect external services through dedicated adapters and integrations.

## ✨ What BuildBix includes

The current MVP codebase includes the following areas. Their presence in the prototype should not be interpreted as proof that every external integration has been validated in production.

| Area | Current scope |
|---|---|
| Authentication and workspaces | User authentication and workspace management |
| Project import | ZIP uploads and GitHub import |
| Project analysis | Analyze imported project files |
| Build planning | Generate build plans and CI workflow templates |
| Build execution | Isolated worker architecture and build queue |
| Logs and artifacts | Live log access and artifact downloads |
| Secrets | Workspace-scoped secret handling |
| Automations and webhooks | Scheduled and event-driven automation flows |
| AI assistant | Read-only AI chat and proposal-based agents |
| Audit log | Record important application actions |
| Web dashboard | Dependency-free JavaScript UI served from `/` |
| CLI | `buildbix` command-line tool |

For features that are planned, incomplete, or awaiting verification, consult [`docs/ROADMAP.md`](docs/ROADMAP.md).

## 🧰 Getting started

### Requirements

- Python 3.8 or newer
- Git (for cloning the repository)
- Docker on the worker machine for Android builds
- Approximately 10 GB of free disk space for an Android build environment; the first build downloads the required SDK/NDK components

Without Docker, an Android build fails with an explicit error; the application does not pretend that a build succeeded.

### 1. Clone the repository

```bash
git clone https://github.com/Barman-Zarei/buildbix.git
cd buildbix
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows, activate it with:

```powershell
.venv\Scripts\activate
```

### 3. Install the project

```bash
pip install -e ".[dev]"
```

### 4. Configure the application secret

```bash
export BUILDBIX_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

PowerShell equivalent:

```powershell
$env:BUILDBIX_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 5. Start the API

```bash
cd backend
uvicorn buildbix.main:app --reload
```

The API documentation is available at:

- Dashboard: `http://127.0.0.1:8000/`
- API documentation: `http://127.0.0.1:8000/docs`

### 6. Start the worker in a second terminal

Activate the same virtual environment, move to the repository's `backend` directory, and run:

```bash
python -m buildbix.workers
```

Keep the API and worker processes running while using the application.

> **AI configuration:** AI chat and AI-agent commands require `BUILDBIX_AI_API_KEY` as a server-side environment variable. Never put this key in frontend code, commit it to Git, or send it to clients.

## 🖥️ Web dashboard and CLI

Open `http://127.0.0.1:8000/` to access the dashboard. The current UI includes login, workspaces, ZIP import, project analysis, build actions, live logs, artifact downloads, automations, and read-only AI chat.

The dashboard is written in plain JavaScript and has no frontend build step. **It has been served and syntax-checked, but has not yet been tested by the project author in a real browser.**

Example CLI workflow:

```bash
buildbix login --register --url http://127.0.0.1:8000 me@example.com

buildbix workspaces --create MyShop
buildbix import 1 demo project.zip
buildbix build 1 --accept-sdk-license --wait

buildbix logs 1 -f
buildbix download 1 -o app.apk
buildbix automate 1 nightly "every day at 03:00"
```

Check `buildbix --help` for the commands and options available in your checkout.

## 🔌 Try the API with curl

The following example registers a user, creates a workspace, imports a ZIP project, inspects its pipeline, and requests a build.

```bash
B=http://127.0.0.1:8000/api

TOKEN=$(curl -s "$B/auth/register" \
  -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"a long passphrase"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

H="Authorization: Bearer $TOKEN"

WS=$(curl -s "$B/workspaces" \
  -H "$H" \
  -H 'content-type: application/json' \
  -d '{"name":"MyShop"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

curl -s "$B/workspaces/$WS/projects" \
  -H "$H" \
  -F name=demo \
  -F file=@my_kivy_project.zip

curl -s "$B/projects/1/pipeline" -H "$H"

curl -s "$B/projects/1/builds" \
  -H "$H" \
  -H 'content-type: application/json' \
  -d '{"accept_sdk_license":true}'

curl -s "$B/builds/1/logs?offset=0" -H "$H"
```

The log endpoint supports offset-based polling; see the API documentation for the `X-Log-Next-Offset` response header and the current API contract.

## 🧪 Testing and verification

Run the automated test suite from the repository root:

```bash
pytest
```

The project currently reports **50 automated tests**, covering areas such as:

- CLI end-to-end flows
- Project analysis and archive safety
- Redaction and role-based access control
- Build-flow and artifact logic
- Webhooks and automations
- AI permission boundaries
- GitHub, Hetzner, and OSV integration mocks
- Alembic migration drift checks

### Important testing limitation

Build-flow tests use a fake sandbox test double to exercise queue, worker, and artifact behavior without launching Docker. The real `DockerSandbox` is not exercised by the test suite. External service mocks do not establish that live integrations work.

## ☁️ GitHub Actions builds

BuildBix can dispatch a GitHub Actions workflow instead of running a build in its own Docker worker.

High-level workflow:

1. Import a project from GitHub using `import-git`.
2. Generate a workflow with `buildbix pipeline <id> --workflow`.
3. Commit the generated workflow to your repository under `.github/workflows/`.
4. Store a GitHub token as the workspace secret `GITHUB_TOKEN`. The documented fine-grained permissions are Actions read/write and Contents read.
5. Start the build using the GitHub Actions runner:

```bash
buildbix build <id> --runner github_actions --accept-sdk-license --wait
```

BuildBix then dispatches the workflow, polls the run, copies redacted job logs, and stores the `buildbix-artifacts` artifact in its own storage.

GitHub removes its hosted artifacts after their retention period; BuildBix keeps its stored copy. This workflow still requires real-world verification.

**Not currently built:** Windows, macOS, and Linux desktop installers. These require additional adapters and workflows using the appropriate GitHub-hosted runners.

## 🧩 Additional capabilities

### Auto-Fix

```bash
buildbix fixes <project> [--build <id>]
buildbix apply-fix <id> --yes
```

Auto-Fix suggests changes. Applying a fix requires explicit approval. The current ZIP-project implementation limits edits to `buildozer.spec`.

### Dependency scanning

```bash
buildbix scan <project>
```

Checks exact-pinned entries in `requirements.txt` against OSV. Internet access is required.

### Server management

BuildBix includes Hetzner server-management commands. These require a `HETZNER_TOKEN` workspace secret and can incur charges.

```bash
buildbix server-create ... --confirm-cost
buildbix server-delete --confirm <name>
```

Use cost-confirmation options carefully and review provider pricing before creating infrastructure.

### Production database migrations

For a PostgreSQL deployment, disable automatic table creation and run Alembic migrations:

```bash
export BUILDBIX_DATABASE_URL=postgresql+psycopg://user:pass@host/db
export BUILDBIX_AUTO_CREATE_TABLES=0

cd backend
alembic upgrade head
```

Install the appropriate PostgreSQL driver (for example, `psycopg`) in your environment before running the migration.

## 🤖 AI and controlled agents

All AI features require `BUILDBIX_AI_API_KEY`.

```bash
buildbix ai <id> "add dark mode" --share-source
buildbix ai-project <ws> <name> "<description>"
```

The `ai` command routes requests to code, documentation, CI, test, or analysis agents. Code and documentation agents create proposals rather than silently changing project files. Review a proposal through the fixes API and explicitly approve it before applying:

```bash
buildbix apply-fix <id> --yes
```

`ai-project` generates a project but does not execute it. Use `buildbix catalog` and `from-starter` to explore starter projects such as Kivy, FastAPI, and Vite + React.

### CI template safety model

CI workflows are generated from pre-approved template files in `backend/buildbix/ci/templates/`. The AI returns only a JSON object containing whitelisted parameters (for example, a Python version or additional apt packages). Those parameters are validated; the AI is not allowed to introduce arbitrary shell commands.

```bash
buildbix ci <id>
buildbix ci-adapt <id> [--save]
buildbix ci-commit <id> --approve
```

Review the generated result and diff before approving a workflow commit. See [`docs/YOUR_TODO.md`](docs/YOUR_TODO.md) for manual setup steps.

## 📡 Events and automations

Supported event behavior in the current prototype:

- A `push` event on a project branch triggers a build.
- A tag push and `release: published` can trigger automations created with `--event tag|release|push`.
- These automations build the branch's latest commit, **not the tag itself**.
- Pull-request events are not supported.

## 🗂️ Project structure

```text
buildbix/
├── backend/
│   └── buildbix/
│       ├── core/
│       ├── analyzer/
│       ├── pipeline/
│       ├── services/
│       ├── workers/
│       ├── automation/
│       ├── ai/
│       └── api/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ROADMAP.md
│   └── SECURITY.md
├── CONTRIBUTING.md
├── LICENSE
└── NOTICE
```

This is a high-level view of the paths referenced by this README. Consult the repository for the complete tree.

## 🔐 Security

BuildBix handles credentials, source archives, build artifacts, and potentially billable cloud infrastructure. Before deploying it beyond a local development environment:

- Read [`docs/SECURITY.md`](docs/SECURITY.md).
- Keep secret keys and provider tokens on the server.
- Do not expose an un-hardened prototype to the public internet.
- Review archive handling, access control, log redaction, and artifact access.
- Understand the cost implications of cloud server creation and external builds.

Please use the repository's documented security-reporting process for vulnerabilities.

## 🛣️ Roadmap and contribution

BuildBix is an early prototype and welcomes careful testing, bug reports, documentation improvements, and code contributions.

- **Roadmap:** [`docs/ROADMAP.md`](docs/ROADMAP.md)
- **Contribution guide:** [`CONTRIBUTING.md`](CONTRIBUTING.md)
- **Security policy:** [`docs/SECURITY.md`](docs/SECURITY.md)
- **Issues:** [Open an issue](https://github.com/Barman-Zarei/buildbix/issues)

Before opening an issue, include the relevant environment details, reproduction steps, expected behavior, and actual behavior. Do not include secrets, access tokens, or private project source.

## 📄 License

BuildBix is distributed under the **Apache License 2.0**. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

Contributions are accepted under the same license with a Developer Certificate of Origin (DCO) sign-off:

```bash
git commit -s
```

---

<div align="center">

**BUILDBIX — Build anything, simply.**

Built to make project builds easier to understand, automate, and manage.

[Repository](https://github.com/Barman-Zarei/buildbix) ·
[Issues](https://github.com/Barman-Zarei/buildbix/issues) ·
[Roadmap](docs/ROADMAP.md)

</div>
