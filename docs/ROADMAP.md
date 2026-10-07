# Status

## Built and tested (automated tests; external services MOCKED where noted)
Auth + token revocation, workspaces/RBAC, secrets, zip + GitHub import, analyzer, build adapters (Kivy->APK, Flutter->APK*, React/Vite->web*,
Python desktop->Windows/Linux/macOS via GitHub Actions*, pytest runner), queue + hardened Docker sandbox, GitHub Actions runner (GitHub mocked),
**template-based CI with AI parameter tuning**, workflow commit to repo (GitHub mocked), logs/artifacts/SHA-256, schedule + event automations,
webhooks (push/tag/release), Auto-Fix (approval-gated), AI code-change proposals, AI Project Builder, agent coordinator, starters + built-in catalog,
OSV dependency scan (mocked), server lifecycle via CloudProvider/Hetzner (mocked), Alembic migrations (drift-checked), web UI, CLI. (* experimental)

## NOT verified against the real thing
Real builds (Docker or GitHub Actions), the CI templates themselves, real AI output quality, real Hetzner/OSV/GitHub APIs,
the web UI in a browser (and the UI does not yet expose CI tuning, AI changes, starters or event automations: use CLI/API).

## Not built
Windows/macOS/Linux *signed* installers, AAB, Docker-image builds, PR events, hosted databases, domains/SSL provisioning, deployments,
marketplace publishing/ratings, plugin sandbox, observability dashboards, cost estimates, billing, email verification, refresh tokens,
egress filtering (only a proxy hook), Redis/S3 backends, PostgreSQL test coverage.
