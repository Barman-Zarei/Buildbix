"""Dependency vulnerability scan using the public OSV database (https://osv.dev). Needs outbound internet."""
import re
import tempfile
from pathlib import Path

import httpx

from ..core.archive import find_project_root, safe_extract_zip
from ..core.config import settings

PIN_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([A-Za-z0-9._+!-]+)")


class ScanError(RuntimeError):
    pass


def query_osv(queries: list[dict]) -> list[list[str]]:
    try:
        r = httpx.post("https://api.osv.dev/v1/querybatch", json={"queries": queries}, timeout=30)
        r.raise_for_status()
        return [[v["id"] for v in res.get("vulns", [])] for res in r.json().get("results", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        raise ScanError("The vulnerability database could not be reached") from None


def scan_archive(storage, key: str) -> dict:
    pins, unpinned = [], []
    with tempfile.TemporaryDirectory(prefix="bbx-scan-") as tmp:
        with storage.open(key) as f:
            safe_extract_zip(f, Path(tmp), max_bytes=settings.max_extracted_bytes, max_files=settings.max_zip_files)
        req = find_project_root(Path(tmp)) / "requirements.txt"
        lines = req.read_text(errors="replace").splitlines() if req.is_file() else []
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = PIN_RE.match(line)
        (pins if m else unpinned).append(m.groups() if m else line)
    results = query_osv([{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in pins]) if pins else []
    vulnerable = [{"package": n, "version": v, "advisories": ids} for (n, v), ids in zip(pins, results) if ids]
    return {"checked": len(pins), "unpinned": unpinned, "vulnerable": vulnerable,
            "note": "Only exact pins (name==version) can be checked; unpinned packages are not scanned."}
