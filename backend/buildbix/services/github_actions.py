"""Minimal GitHub REST client for running builds on GitHub Actions. Tokens never appear in errors or logs."""
import base64

import httpx


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self.c = httpx.Client(base_url="https://api.github.com", follow_redirects=True, timeout=60, transport=transport,
                              headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                       "X-GitHub-Api-Version": "2022-11-28"})

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self.c.request(method, path, **kw)
        except httpx.HTTPError:
            raise GitHubError("network error talking to api.github.com") from None
        if r.status_code >= 400:
            try:
                msg = str(r.json().get("message", ""))[:200]
            except ValueError:
                msg = ""
            raise GitHubError(f"{r.status_code} {msg}".strip())
        return r

    def dispatch(self, owner, repo, workflow_file, ref):
        self._req("POST", f"/repos/{owner}/{repo}/actions/workflows/{workflow_file}/dispatches", json={"ref": ref})

    def list_runs(self, owner, repo, workflow_file, branch):
        r = self._req("GET", f"/repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs",
                      params={"event": "workflow_dispatch", "branch": branch, "per_page": 10})
        return r.json().get("workflow_runs", [])

    def get_run(self, owner, repo, run_id):
        return self._req("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}").json()

    def list_jobs(self, owner, repo, run_id):
        return self._req("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs").json().get("jobs", [])

    def job_logs(self, owner, repo, job_id) -> str:
        return self._req("GET", f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs").text

    def list_artifacts(self, owner, repo, run_id):
        return self._req("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts").json().get("artifacts", [])

    def download_artifact(self, owner, repo, artifact_id) -> bytes:
        return self._req("GET", f"/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip").content

    def get_file(self, owner, repo, path, ref):
        try:
            return self._req("GET", f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref}).json()
        except GitHubError as e:
            if str(e).startswith("404"):
                return None
            raise

    def put_file(self, owner, repo, path, content: bytes, message, branch, sha=None):
        body = {"message": message, "content": base64.b64encode(content).decode(), "branch": branch}
        if sha:
            body["sha"] = sha
        return self._req("PUT", f"/repos/{owner}/{repo}/contents/{path}", json=body).json()

    def cancel(self, owner, repo, run_id):
        self._req("POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/cancel")
