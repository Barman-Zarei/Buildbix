"""Validation of AI-proposed file changes. The model's output is untrusted: nothing is written without these checks
AND an explicit user approval."""
import json
import os
import re
import tempfile
from pathlib import Path

from ..core.archive import find_project_root, safe_extract_zip
from ..core.config import settings


class ChangeError(ValueError):
    pass


PATH_RE = re.compile(r"^\.?[A-Za-z0-9_][A-Za-z0-9_./-]{0,150}$")
BLOCKED_SEGMENTS = {".git", ".github", ".gitmodules"}
BLOCKED_FILE = re.compile(r"(^|/)(\.env(\..+)?|id_rsa|id_ed25519|[^/]+\.(pem|jks|keystore|p12))$", re.I)
TEXT_EXT = {".py", ".kv", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt", ".spec", ".yaml", ".yml", ".toml",
            ".cfg", ".ini", ".html", ".css", ".dart", ".java", ".kt", ".go", ".rs"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".buildozer", "build", "dist", "bin"}


def safe_path(path) -> str:
    if not isinstance(path, str) or not PATH_RE.match(path):
        raise ChangeError(f"invalid path: {str(path)[:60]!r}")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts) or any(p in BLOCKED_SEGMENTS for p in parts) or BLOCKED_FILE.search(path):
        raise ChangeError(f"path not allowed: {path}")
    return path


def parse_files(raw: str, *, max_files: int = 10, max_file: int = 100_000, max_total: int = 300_000) -> tuple[str, list[dict]]:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ChangeError("the AI did not return JSON")
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise ChangeError("the AI returned invalid JSON") from None
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list) or not files:
        raise ChangeError("no files proposed")
    if len(files) > max_files:
        raise ChangeError(f"too many files (max {max_files})")
    out, seen, total = [], set(), 0
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("content"), str):
            raise ChangeError("malformed file entry")
        path = safe_path(f.get("path"))
        size = len(f["content"].encode())
        total += size
        if path in seen or size > max_file or total > max_total:
            raise ChangeError("duplicate path or file too large")
        seen.add(path)
        out.append({"path": path, "content": f["content"]})
    return (str(data.get("summary", ""))[:500] or "AI change"), out


def collect_source(storage, key: str, max_files: int = 40, max_bytes: int = 60_000) -> dict[str, str]:
    """Read-only snapshot of text files to show the AI (secret-looking files and build output are skipped)."""
    out, used = {}, 0
    with tempfile.TemporaryDirectory(prefix="bbx-ai-") as tmp:
        with storage.open(key) as f:
            safe_extract_zip(f, Path(tmp), max_bytes=settings.max_extracted_bytes, max_files=settings.max_zip_files)
        root = find_project_root(Path(tmp))
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                p = Path(dirpath) / name
                rel = p.relative_to(root).as_posix()
                if p.suffix.lower() not in TEXT_EXT or BLOCKED_FILE.search(rel) or len(out) >= max_files:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                if used + len(text) > max_bytes:
                    continue
                used += len(text)
                out[rel] = text
    return out
