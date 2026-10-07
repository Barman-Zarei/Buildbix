import io
import os
import tempfile
import zipfile

_tmp = tempfile.mkdtemp(prefix="bbx-test-")
os.environ["BUILDBIX_DATA_DIR"] = _tmp
os.environ["BUILDBIX_SECRET_KEY"] = "test-secret-key-for-pytest-only"
os.environ["BUILDBIX_AUTH_RATE_LIMIT"] = "100000"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from buildbix.core.db import SessionLocal, init_db  # noqa: E402
from buildbix.main import app  # noqa: E402

init_db()
_n = {"i": 0}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    with SessionLocal() as s:
        yield s


def make_user(client, prefix="u"):
    _n["i"] += 1
    email = f"{prefix}{_n['i']}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "correct horse battery"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, email


KIVY_FILES = {
    "main.py": "from kivy.app import App\nclass A(App): pass\nA().run()\n",
    "requirements.txt": "kivy==2.3.0\nrequests\n",
    "buildozer.spec": "[app]\ntitle = Demo\npackage.name = demo\npackage.domain = org.example\nversion = 1.0\n"
                      "requirements = python3,kivy,requests\nandroid.permissions = INTERNET\n",
}


def make_zip(files: dict, wrap: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(f"{wrap}/{name}" if wrap else name, content)
    return buf.getvalue()


@pytest.fixture()
def workspace(client):
    h, email = make_user(client)
    ws = client.post("/api/workspaces", json={"name": "Shop"}, headers=h).json()
    return h, email, ws["id"]


@pytest.fixture()
def kivy_project(client, workspace):
    h, _, wid = workspace
    r = client.post(f"/api/workspaces/{wid}/projects", headers=h, data={"name": "demo"},
                    files={"file": ("p.zip", make_zip(KIVY_FILES, wrap="demo-main"), "application/zip")})
    assert r.status_code == 201, r.text
    return h, wid, r.json()
