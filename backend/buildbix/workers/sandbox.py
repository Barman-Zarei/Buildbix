"""Build sandbox. Untrusted project code runs ONLY here, never in the API/worker process."""
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


class SandboxUnavailable(RuntimeError):
    pass


@dataclass
class SandboxSpec:
    name: str
    source_dir: Path
    cache_dir: Path
    image: str
    dockerfile: str | None
    command: str
    timeout: int
    memory: str = "6g"
    cpus: str = "2"
    network: str = ""
    proxy: str = ""


@dataclass
class SandboxResult:
    exit_code: int
    cancelled: bool = False
    timed_out: bool = False


class Sandbox(Protocol):
    def run(self, spec: SandboxSpec, log: Callable[[str], None], should_cancel: Callable[[], bool]) -> SandboxResult: ...


class DockerSandbox:
    """Docker-based sandbox: non-root user, all capabilities dropped, no-new-privileges, CPU/RAM/PID limits.
    NOTE: outbound network stays enabled (SDK/NDK/pip downloads). Put workers behind an egress proxy in production."""

    def _ensure(self) -> None:
        if not shutil.which("docker"):
            raise SandboxUnavailable("Docker is not installed on this worker. Builds need Docker; no build was run.")
        try:
            ok = subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
        except subprocess.TimeoutExpired:
            ok = False
        if not ok:
            raise SandboxUnavailable("Cannot reach the Docker daemon. No build was run.")

    def run(self, spec, log, should_cancel):
        self._ensure()
        spec.cache_dir.mkdir(parents=True, exist_ok=True)
        if spec.dockerfile and subprocess.run(["docker", "image", "inspect", spec.image], capture_output=True).returncode != 0:
            log("Building sandbox image (first run only; this takes a while)...")
            res = self._stream(["docker", "build", "-t", spec.image, "-"], log, should_cancel, spec.timeout,
                               stdin_data=spec.dockerfile)
            if res.exit_code != 0 or res.cancelled or res.timed_out:
                return res
        args = ["docker", "run", "--rm", "--name", spec.name, "--user", f"{os.getuid()}:{os.getgid()}",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "2048",
                "--memory", spec.memory, "--cpus", spec.cpus,
                "-e", "HOME=/home/build", "-v", f"{spec.cache_dir}:/home/build",
                "-v", f"{spec.source_dir}:/work", "-w", "/work",
                *(["--network", spec.network] if spec.network else []),
                *(["-e", f"HTTP_PROXY={spec.proxy}", "-e", f"HTTPS_PROXY={spec.proxy}"] if spec.proxy else []),
                spec.image, "bash", "-c", spec.command]
        return self._stream(args, log, should_cancel, spec.timeout, container=spec.name)

    @staticmethod
    def _kill(proc, container):
        if container:
            subprocess.run(["docker", "kill", container], capture_output=True)
        proc.kill()

    def _stream(self, args, log, should_cancel, timeout, *, stdin_data=None, container=None):
        proc = subprocess.Popen(args, stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        if stdin_data is not None:
            proc.stdin.write(stdin_data)
            proc.stdin.close()
        q: queue.Queue = queue.Queue()

        def reader():
            for line in proc.stdout:
                q.put(line)
            q.put(None)

        threading.Thread(target=reader, daemon=True).start()
        deadline, cancelled, timed_out, done = time.monotonic() + timeout, False, False, False
        while not done:
            try:
                item = q.get(timeout=1)
            except queue.Empty:
                item = ""
            if item is None:
                done = True
            elif item:
                log(item.rstrip("\n"))
            if not (cancelled or timed_out):
                if should_cancel():
                    cancelled = True
                    self._kill(proc, container)
                elif time.monotonic() > deadline:
                    timed_out = True
                    self._kill(proc, container)
        return SandboxResult(proc.wait(), cancelled, timed_out)
