# Architecture (MVP)

```
Client/CLI -> FastAPI (api/) -> services/ -> DB (SQLAlchemy)         events -> audit_logs
                                   |-> jobs table (queue) -> workers/runner -> Sandbox (Docker) -> storage (artifacts, logs)
```
- **API-first.** Routers contain no business logic beyond validation + authorization; logic is in `services/`.
- **Analyzer** (`analyzer/engine.py`): list of detector functions over a `Context`; add a function to add a language/framework.
- **Adapters** (`pipeline/adapters.py`): `supports()` + `plan()` per target. MVP: `KivyAndroidAdapter` (APK).
  A plan is stored on the build, includes Dockerfile, command and a GitHub Actions workflow (inspectable via `GET /projects/{id}/pipeline`).
- **Queue** (`services/jobs.py`): DB-backed, atomic claim, priority, retry (`max_attempts`), cancel, timeout. Same interface can be
  re-implemented over Redis/arq without touching callers.
- **Sandbox** (`workers/sandbox.py`): `Sandbox` protocol; `DockerSandbox` runs as non-root with `--cap-drop ALL`,
  `no-new-privileges`, CPU/RAM/PID limits, per-workspace cache. **Outbound network is open** (SDK downloads) — use an egress proxy in production.
- **Storage** (`core/storage.py`): `ObjectStorage` interface; `LocalStorage` now, S3/MinIO later.
- **Events** (`core/events.py`): `build.*`, `project.*`, `automation.*`... persisted to `audit_logs`, subscribable in-process.
- **AI** (`ai/`): provider-agnostic `LLMClient`; read-only assistant; `permissions.py` encodes safe / approval / high-risk tiers
  that future mutating agents must pass through. Only analysis metadata (never source code) is sent to the model.

## Data model
users, workspaces, memberships(role), projects, builds, artifacts, jobs, automations, secrets, audit_logs, ai_messages.
Unique constraints: (workspace,user), (workspace,name), (project,number), (workspace,name,environment).
