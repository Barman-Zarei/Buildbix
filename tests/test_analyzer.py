from buildbix.analyzer import analyze_directory
from buildbix.pipeline import UnsupportedTarget, get_plan
import pytest
from conftest import KIVY_FILES


def write(tmp_path, files):
    for n, c in files.items():
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c)
    return tmp_path


def codes(res):
    return {f["code"] for f in res["findings"]}


def test_kivy_project_detected(tmp_path):
    res = analyze_directory(write(tmp_path, KIVY_FILES))
    assert "Kivy" in res["frameworks"] and "Buildozer" in res["build_systems"] and "Python" in res["languages"]
    assert res["buildozer"]["package"] == "org.example.demo"
    assert not [f for f in res["findings"] if f["severity"] == "error"]
    assert next(t for t in res["targets"] if t["id"] == "android-apk")["supported"]


def test_kivy_without_spec_is_blocked(tmp_path):
    res = analyze_directory(write(tmp_path, {"main.py": "import kivy\n"}))
    assert "KIVY_NO_SPEC" in codes(res)
    with pytest.raises(UnsupportedTarget):
        get_plan(res, "android-apk")


def test_spec_problems_found(tmp_path):
    files = dict(KIVY_FILES)
    files["buildozer.spec"] = ("[app]\ntitle = D\npackage.name = My-App\npackage.domain = org.x\n"
                               "requirements = python3\nandroid.api = 28\nandroid.minapi = 30\n")
    files["requirements.txt"] = "kivy\nrequests\ntensorflow\n"
    c = codes(analyze_directory(write(tmp_path, files)))
    assert {"SPEC_BAD_PACKAGE_NAME", "SPEC_NO_VERSION", "SPEC_NO_KIVY_REQ", "REQ_NOT_IN_SPEC",
            "ANDROID_INCOMPATIBLE_DEP", "ANDROID_API_OLD", "ANDROID_MINAPI_GT_API"} <= c


def test_other_stacks_and_suspicious_files(tmp_path):
    res = analyze_directory(write(tmp_path, {
        "package.json": '{"dependencies":{"react":"18","vite":"5"},"scripts":{"test":"vitest"}}',
        ".env": "SECRET=1", ".env.example": "SECRET=", "app/key.pem": "x",
        "requirements.txt": "fastapi\npytest\n", "api.py": "x = 1\n"}))
    assert {"React", "Vite", "FastAPI", "Node.js"} <= set(res["frameworks"])
    assert {"npm test", "pytest"} <= set(res["tests"])
    msgs = " ".join(f["message"] for f in res["findings"] if f["code"] == "SECRET_FILE")
    assert ".env'" in msgs and "key.pem" in msgs and ".env.example" not in msgs
    supported = {t["id"] for t in res["targets"] if t["supported"]}
    assert supported == {"run-tests"}  # web-bundle has no build script -> not offered
    assert any(t["id"] == "web-bundle" and "build" in t["reason"] for t in res["targets"])


def test_plan_is_inspectable_and_license_aware(tmp_path):
    res = analyze_directory(write(tmp_path, KIVY_FILES))
    plan = get_plan(res, "android-apk", accept_license=True)
    assert plan["requires_sdk_license"] and "buildozer" in plan["command"]
    assert "upload-artifact" in plan["github_workflow"] and plan["dockerfile"].startswith("FROM")
