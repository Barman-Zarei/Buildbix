# What YOU must do (nothing here can be done by code)

## A. Before anything works for real
1. **Install Docker** on the machine that runs the worker (Docker Desktop + WSL2 on Windows). ~10 GB free disk. Needed only for the "my own worker" runner.
2. **Create a GitHub account/repo** for each project you want to build, and a **fine-grained personal access token**:
   repo access = that repo; permissions: *Actions: read & write*, *Contents: read & write* (write only if you use `ci-commit`).
   Store it in BUILDBIX as workspace secret `GITHUB_TOKEN` (never in code or chat).
3. **Set `BUILDBIX_SECRET_KEY`** (long random string) on the server. Without it a dev key is auto-generated.
4. **Run the first real build** (Docker runner or GitHub Actions runner) and send me the log/error. Everything about real builds is unverified until then.

## B. The CI template idea (your part)
5. **Paste your proven workflows** into `backend/buildbix/ci/templates/` (kivy-android.yml, flutter-android.yml, web-bundle.yml, desktop-bundle.yml):
   - keep the header comment lines; replace everything below them with a workflow that **ran successfully** on a real project;
   - keep only the `{{placeholders}}` you want BUILDBIX to fill (allowed names are listed in `ci/registry.py`; the checker rejects unknown ones);
   - change `# STATUS: UNVERIFIED default` to `# STATUS: VERIFIED <date> <link to the successful run>`;
   - **pin third-party actions to commit SHAs** (I did not invent SHAs);
   - run `python -m buildbix.ci.check`.
6. If you want to allow more parameter values (e.g. another Python version, more apt packages), edit the whitelists in `ci/registry.py` and add a test.

## C. AI features (optional)
7. Create an **Anthropic Console account**, add billing/credits, create an **API key**, set `BUILDBIX_AI_API_KEY` on the server. AI chat, CI tuning, code changes and the project generator all need it. Check the provider's data policy before sharing source code (`share_source`).

## D. Servers (optional, billable)
8. Create a **Hetzner Cloud account**, a project and an **API token (read & write)**; store as workspace secret `HETZNER_TOKEN`.
   Look up valid `server_type` / `region` names in their console (I did not hardcode them: they change). Servers cost money until deleted.
9. To host BUILDBIX itself: a **VPS** (4+ vCPU, 8+ GB RAM, 40+ GB disk recommended for Android builds), a **domain** you own,
   and a reverse proxy with HTTPS (e.g. Caddy). Point DNS A-record to the VPS. Use PostgreSQL + `alembic upgrade head` in production.

## E. Releasing apps (only when you publish)
10. **Android:** create a keystore (`keytool`), keep it and its passwords ONLY in GitHub repo Secrets; Google Play Console registration (one-time fee) if you publish there.
11. **macOS/iOS:** Apple Developer Program membership (paid, yearly) for signing + notarization.
12. **Windows:** a code-signing certificate (paid) to avoid SmartScreen warnings.

## F. Not built (needs you + real infrastructure first)
Hosted databases, domains/SSL provisioning from the UI, deployments, billing/payments (payment-provider account + legal setup),
marketplace publishing/ratings, plugin sandbox, email verification (needs an email provider), Redis/S3 (accounts + deployment).
