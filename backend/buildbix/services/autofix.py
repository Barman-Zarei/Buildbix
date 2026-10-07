"""Auto-Fix: deterministic diagnosis + structured, reviewable edits. Nothing is applied without explicit approval.
Edits are restricted to an allow-list (buildozer.spec) and never touch production systems."""
import re
from pathlib import Path

ALLOWED_FILES = {"buildozer.spec"}
MODULE_TO_PACKAGE = {"PIL": "pillow", "cv2": "opencv", "yaml": "pyyaml", "bs4": "beautifulsoup4", "sklearn": "scikit-learn"}


class FixError(ValueError):
    pass


def _spec_modify(text: str, key: str, fn) -> str:
    lines, in_app, app_idx, key_idx = text.splitlines(keepends=True), False, None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("["):
            in_app = s.lower() == "[app]"
            if in_app:
                app_idx = i
            continue
        if in_app and re.match(rf"^{re.escape(key)}\s*=", line):
            key_idx = i
    if app_idx is None:
        raise FixError("buildozer.spec has no [app] section")
    if key_idx is not None:
        k, _, v = lines[key_idx].partition("=")
        lines[key_idx] = f"{k.rstrip()} = {fn(v.strip())}\n"
    else:
        lines.insert(app_idx + 1, f"{key} = {fn('')}\n")
    return "".join(lines)


def _add_to_list(value: str, item: str, upper: bool = False) -> str:
    items = [x.strip() for x in value.split(",") if x.strip()]
    norm = (lambda x: x.upper()) if upper else (lambda x: re.split(r"[<>=~!@\[ ]", x)[0].lower())
    if norm(item) not in {norm(x) for x in items}:
        items.append(item)
    return ",".join(items)


def apply_edit(root: Path, edit: dict) -> None:
    if edit.get("op") == "write_file":  # AI-proposed file content (already user-approved at this point)
        from .aichanges import ChangeError, safe_path
        try:
            rel = safe_path(edit.get("file"))
        except ChangeError as e:
            raise FixError(str(e)) from None
        content = edit.get("content")
        if not isinstance(content, str) or len(content.encode()) > 100_000:
            raise FixError("invalid file content")
        target = (root / rel).resolve()
        if root.resolve() not in target.parents:
            raise FixError("unsafe path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return
    if edit.get("file") not in ALLOWED_FILES:
        raise FixError("edit targets a file that Auto-Fix may not change")
    path = root / edit["file"]
    if not path.is_file():
        raise FixError(f"{edit['file']} not found")
    text, op, value = path.read_text(encoding="utf-8"), edit["op"], str(edit.get("value", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,60}", value):
        raise FixError("unsafe edit value")
    if op == "add_requirement":
        text = _spec_modify(text, "requirements", lambda v: _add_to_list(v, value))
    elif op == "add_permission":
        text = _spec_modify(text, "android.permissions", lambda v: _add_to_list(v, value.upper(), upper=True))
    else:
        raise FixError(f"unknown edit op {op}")
    path.write_text(text, encoding="utf-8")


def _p(code, title, explanation, edits=None):
    return {"code": code, "title": title, "explanation": explanation, "edits": edits or []}


def diagnose_analysis(analysis: dict) -> list[dict]:
    out = []
    for f in analysis.get("findings", []):
        c = f["code"]
        if c == "REQ_NOT_IN_SPEC":
            pk = f.get("data", {}).get("packages", [])
            out.append(_p("add_requirements", "Add missing packages to buildozer.spec",
                          "These packages are in requirements.txt but not in buildozer.spec, so they would be missing in the APK: "
                          + ", ".join(pk), [{"file": "buildozer.spec", "op": "add_requirement", "value": x} for x in pk]))
        elif c == "ANDROID_NO_INTERNET":
            out.append(_p("add_internet_permission", "Add INTERNET permission",
                          "A networking library is used but the app does not request INTERNET, so network calls fail on Android.",
                          [{"file": "buildozer.spec", "op": "add_permission", "value": "INTERNET"}]))
        elif c == "SPEC_NO_PYTHON3":
            out.append(_p("add_python3", "Add python3 to requirements", "buildozer needs python3 in requirements.",
                          [{"file": "buildozer.spec", "op": "add_requirement", "value": "python3"}]))
        elif c == "SPEC_NO_KIVY_REQ":
            out.append(_p("add_kivy", "Add kivy to requirements", "kivy must be listed in buildozer.spec requirements.",
                          [{"file": "buildozer.spec", "op": "add_requirement", "value": "kivy"}]))
    return out


def diagnose_log(log: str) -> list[dict]:
    out, seen = [], set()
    for mod in re.findall(r"ModuleNotFoundError: No module named '([A-Za-z0-9_]+)", log):
        pkg = MODULE_TO_PACKAGE.get(mod, mod.replace("_", "-").lower())
        if pkg not in seen:
            seen.add(pkg)
            out.append(_p("missing_module", f"Add '{pkg}' to buildozer.spec requirements",
                          f"The app imports '{mod}' but it was not packaged. (If the package name differs from the module name, adjust it.)",
                          [{"file": "buildozer.spec", "op": "add_requirement", "value": pkg}]))
    low = log.lower()
    if "licenses have not been accepted" in low or "accept the sdk license" in low or "license not accepted" in low:
        out.append(_p("sdk_license", "Android SDK license not accepted",
                      "Set android.accept_sdk_license = True in buildozer.spec yourself, or start the build with the license confirmation."))
    if "no space left on device" in low:
        out.append(_p("disk_full", "Build ran out of disk space", "Free disk space on the worker (the SDK/NDK cache is several GB) and retry."))
    if "exit code 137" in low or "outofmemoryerror" in low or re.search(r"\bkilled\b", low):
        out.append(_p("out_of_memory", "Build was killed (probably out of memory)", "Raise BUILDBIX_BUILD_MEMORY on the worker (e.g. 8g) and retry."))
    if "docker is not installed" in low or "cannot reach the docker daemon" in low:
        out.append(_p("no_docker", "No Docker on the worker", "Install Docker on the worker machine, or run the build with the GitHub Actions runner."))
    return out
