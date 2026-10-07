# Security notes
- Passwords: scrypt. Tokens: HS256 JWT, 60 min. Set `BUILDBIX_SECRET_KEY` in production (dev key is auto-generated and persisted).
- RBAC: viewer < developer < admin < owner, enforced per workspace; non-members get 404 (no existence leak).
- Secrets: Fernet-encrypted at rest, values never returned by the API, never written to the audit log, redacted from logs
  (exact values + common patterns) **before** log lines are stored.
- Uploads: size cap, zip-slip / symlink / zip-bomb checks, storage keys validated against traversal.
- Git import: https://github.com only, hooks disabled, shallow clone, token passed via header (not URL).
- Webhooks: HMAC-SHA256 signature verified with constant-time compare; secret shown once.
- Builds run only in the sandbox, never in the API process. Android SDK license acceptance requires explicit user confirmation.
- Rate limiting on login/register (in-memory; use a shared store with multiple API replicas).
- Token revocation: `POST /auth/logout-all` invalidates every issued token. Dependency scan via OSV (`/projects/{id}/scan`).
- Auto-Fix edits are allow-listed (buildozer.spec only), validated, and applied only after an explicit approval call. Server deletion requires typing the exact name; server creation requires `confirm_cost`.
- Sandbox egress: set `BUILDBIX_SANDBOX_NETWORK` / `BUILDBIX_SANDBOX_PROXY` to route build containers through a filtering proxy you operate (not provided here).
- Known gaps: no email verification, no refresh tokens (only revocation), plugin sandbox not built, no egress filter shipped.
- CI templates: AI output is reduced to whitelisted parameters (regex/enum); it can never add steps or commands. Rendered YAML is parsed and checked.
- AI code changes: output is untrusted JSON; paths exclude .github/, .git, secrets/keys; size/file caps; nothing is applied without an explicit approval call; source is sent to the provider only with share_source=true. Prompt injection inside source files cannot bypass these checks, but review diffs before approving.
