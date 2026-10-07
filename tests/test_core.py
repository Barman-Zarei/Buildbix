import io
import zipfile

import pytest

from buildbix.automation.schedule import ScheduleError, next_run, parse_schedule
from buildbix.core.archive import ArchiveError, safe_extract_zip
from buildbix.core.ratelimit import RateLimiter
from buildbix.core.redaction import Redactor
from buildbix.core.storage import LocalStorage
from buildbix.ai.permissions import authorize
from datetime import datetime


def test_redactor_masks_known_and_pattern_secrets():
    r = Redactor(["hunter2-supersecret"])
    assert "hunter2" not in r("password is hunter2-supersecret ok")
    assert r("API_KEY=abc123xyz") == "API_KEY=***"
    assert "ghp_" not in r("token ghp_" + "a" * 30)
    assert "pw" not in r("clone https://user:pw@github.com/x/y")


def test_zip_slip_and_symlink_rejected(tmp_path):
    for name in ("../evil.txt", "/etc/evil.txt"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(name, "x")
        buf.seek(0)
        with pytest.raises(ArchiveError):
            safe_extract_zip(buf, tmp_path / "o", max_bytes=1000, max_files=10)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        zi = zipfile.ZipInfo("link")
        zi.external_attr = (0o120777) << 16
        z.writestr(zi, "/etc/passwd")
    buf.seek(0)
    with pytest.raises(ArchiveError):
        safe_extract_zip(buf, tmp_path / "o2", max_bytes=1000, max_files=10)


def test_zip_bomb_limit(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.txt", "0" * 100_000)
    buf.seek(0)
    with pytest.raises(ArchiveError):
        safe_extract_zip(buf, tmp_path / "o", max_bytes=1000, max_files=10)


def test_storage_blocks_traversal(tmp_path):
    s = LocalStorage(tmp_path)
    for bad in ("../x", "a/../../x", "/abs", ""):
        with pytest.raises(ValueError):
            s.put_bytes(bad, b"x")
    s.put_bytes("ok/file.txt", b"hi")
    assert s.read_range("ok/file.txt", 1, 10) == b"i"


def test_schedule_parser():
    assert parse_schedule("Every day at 03:00") == {"kind": "daily", "hour": 3, "minute": 0}
    assert parse_schedule("every 6 hours")["minutes"] == 360
    assert next_run(parse_schedule("every day at 03:00"), datetime(2026, 10, 6, 4, 0)) == datetime(2026, 10, 7, 3, 0)
    for bad in ("whenever", "every day at 25:00", "every 1 minutes"):
        with pytest.raises(ScheduleError):
            parse_schedule(bad)


def test_rate_limiter():
    rl = RateLimiter(2, 60)
    assert rl.check("a") and rl.check("a") and not rl.check("a") and rl.check("b")


def test_ai_permissions():
    assert authorize("analyze_project", user_approved=False)
    assert not authorize("modify_project_files", user_approved=False)
    assert authorize("modify_project_files", user_approved=True)
    assert not authorize("delete_database", user_approved=True)
    assert authorize("delete_database", user_approved=True, user_confirmed_high_risk=True)
    assert not authorize("unknown_action", user_approved=True)
