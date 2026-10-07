# Contributing to BUILDBIX

**خلاصه به فارسی:** هر مشارکتی (باگ، تست، مستندات، adapter، قالب CI) خوش‌آمد است. با `git commit -s` امضا کنید،
تست بنویسید، سرویس‌های بیرونی را در تست mock کنید و هرگز راز واقعی (توکن، کلید) در کد یا Issue نگذارید.
مشارکت‌ها تحت همان لایسنس پروژه (Apache-2.0) پذیرفته می‌شوند.

Thanks for helping! BUILDBIX is an early-stage project: a lot works in tests but has **not yet been verified with real
builds**. Honest bug reports from real runs are among the most valuable contributions.

## Ways to contribute
- **Run a real build** (Docker or GitHub Actions) and report the exact log/error. This is the single most useful thing right now.
- **Contribute a verified CI template** (see below).
- Add a **build adapter** (Flutter fixes, Electron, Go, Rust, ...), a **starter project**, or an **Auto-Fix rule**.
- Improve the **web UI** (it does not yet expose CI tuning, AI changes, starters or event automations).
- Improve **tests, docs and translations**.
- Look at `docs/ROADMAP.md` ("Not built") for larger items: pull-request events, AAB, signed installers, Redis/S3 backends.

## Ground rules
1. **Be honest about verification.** Do not mark something "verified" unless it ran for real; say what you did and did not test.
2. **Tests are required** for behaviour changes. External services (GitHub, Hetzner, OSV, the AI provider, Docker) must be **mocked**
   in the test suite; see `tests/test_phase2.py` and `tests/test_phase3.py` for examples (`httpx.MockTransport`, fake sandbox, fake LLM).
3. **Security first.** Untrusted input (uploads, AI output, webhook payloads, project files) is validated; nothing the AI proposes is applied
   without explicit user approval. Read `docs/SECURITY.md` before touching `core/`, `services/autofix.py`, `services/aichanges.py` or `ci/`.
4. **Never commit secrets** (tokens, keys, `.env`, keystores) and never paste them into issues or PRs. Revoke anything that leaks.
5. Keep PRs focused. One topic per PR.

## Development setup
```bash
git clone <your fork> && cd buildbix
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                      # all tests must pass
python -m buildbix.ci.check    # CI templates must validate
cd backend && uvicorn buildbix.main:app --reload     # API + web UI on :8000
python -m buildbix.workers                           # worker (needs Docker for real builds)
```
Windows users: use WSL2. Database migrations: after changing `models.py`, generate one with
`cd backend && alembic revision --autogenerate -m "..."`; a test fails if models and migrations drift apart.

## Project layout
`backend/buildbix/`: `core/` (config, db, security, storage, redaction, events) · `analyzer/` · `pipeline/` (build adapters) · `ci/` (templates + AI parameter tuning) ·
`services/` · `workers/` (queue runner, Docker sandbox, GitHub Actions runner, scheduler) · `ai/` · `providers/` · `api/` · `web/` (UI) · `cli.py`.
See `docs/ARCHITECTURE.md`.

## Making a change
1. Open an issue first for anything big, so we can agree on the approach.
2. Create a branch, make your change, add/adjust tests and docs.
3. Run `pytest -q` and `python -m buildbix.ci.check`.
4. Commit with a sign-off: `git commit -s -m "Short imperative summary"`.
5. Open a PR and fill in the checklist.

### Sign-off (Developer Certificate of Origin)
We use the [DCO](https://developercertificate.org/) instead of a CLA: by adding `Signed-off-by: Your Name <you@example.com>`
(`git commit -s`) you certify you wrote the code or have the right to submit it under the project's license.

### License of contributions
BUILDBIX is licensed under the **Apache License 2.0** (`LICENSE`). Unless you state otherwise, any contribution you submit is licensed
under the same terms (Section 5 of the license).

## Adding a build adapter
Implement `supports()` and `plan()` in `pipeline/adapters.py`, add it to `ADAPTERS`, add a CI template + analyzer target if needed, and
test the plan (command, artifact glob, workflow). Mark it `experimental: True` until it has been proven on real projects.

## Contributing a verified CI template
Templates live in `backend/buildbix/ci/templates/`. A template is a workflow that **ran successfully on a real project**:
1. Keep the header comment lines; put your proven workflow below them.
2. Use `{{placeholder}}`s only for values declared in `ci/registry.py` (add a whitelist/regex for new ones, plus a test that bad values are rejected).
3. Pin third-party actions to commit SHAs.
4. Set `# STATUS: VERIFIED <date> <link to the successful run>` and run `python -m buildbix.ci.check`.
The AI may only tune whitelisted parameters; it must never be able to add steps or commands.

## Reporting security issues
Please **do not** open a public issue. See `SECURITY.md`.

## Conduct
Be kind, assume good intent, keep feedback about the code. Harassment or discrimination is not tolerated; maintainers may remove
comments or ban contributors who engage in it. (A full code of conduct can be adopted as the community grows.)
