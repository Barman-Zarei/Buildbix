"""GitHub import. Clone happens with hooks disabled, https only, shallow; the tree is snapshotted to a zip."""
import base64
import os
import re
import subprocess
from pathlib import Path

from ..core.archive import zip_directory

REPO_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(\.git)?$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


class GitError(RuntimeError):
    pass


def validate(repo_url: str, branch: str) -> None:
    if not REPO_RE.match(repo_url):
        raise GitError("Only https://github.com/<owner>/<repo> URLs are supported")
    if not BRANCH_RE.match(branch) or ".." in branch:
        raise GitError("Invalid branch name")


def clone_to_archive(repo_url: str, branch: str, token: str | None, workdir: Path) -> str:
    """Clone into workdir/repo, write workdir/source.zip (without .git), return the commit sha."""
    validate(repo_url, branch)
    repo = workdir / "repo"
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always"]
    if token:  # header keeps the token out of the URL, process list entries of the URL and logs
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd += ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"]
    cmd += ["clone", "--depth", "1", "--single-branch", "--no-tags", "--branch", branch, "--", repo_url, str(repo)]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "https"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired as e:
        raise GitError("git clone timed out") from e
    if r.returncode != 0:
        raise GitError("git clone failed (check the URL, branch and access)")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    zip_directory(repo, workdir / "source.zip")
    return sha
