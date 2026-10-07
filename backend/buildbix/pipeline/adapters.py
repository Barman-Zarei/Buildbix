"""Build adapters. Each adapter turns an analysis into an inspectable build plan. Workflow text always comes from a
pre-approved CI template (ci/templates); adapters only supply validated parameters."""
import hashlib
from typing import Protocol

from ..ci import render as render_workflow

ARTIFACT_NAME = "buildbix-artifacts"


class UnsupportedTarget(ValueError):
    pass


class Adapter(Protocol):
    id: str

    def supports(self, analysis: dict, target: str) -> bool: ...
    def plan(self, analysis: dict, target: str, accept_license: bool, ci_params: dict | None = None) -> dict: ...


def _wf(template_id: str, analysis: dict, ci_params: dict | None) -> str:
    return render_workflow(template_id, (ci_params or {}).get(template_id), analysis)


_APT = ("git zip unzip openjdk-17-jdk python3 python3-pip python3-venv autoconf automake libtool libtool-bin "
        "pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev "
        "build-essential ccache curl ca-certificates")


class KivyAndroidAdapter:
    id, template_id = "kivy-android", "kivy-android"

    def supports(self, analysis, target):
        return target == "android-apk" and "Kivy" in analysis.get("frameworks", []) \
            and "Buildozer" in analysis.get("build_systems", [])

    def plan(self, analysis, target, accept_license, ci_params=None):
        spec_ok = bool((analysis.get("buildozer") or {}).get("sdk_license_accepted"))
        dockerfile = ("FROM ubuntu:22.04\nENV DEBIAN_FRONTEND=noninteractive\n"
                      f"RUN apt-get update && apt-get install -y --no-install-recommends {_APT} && rm -rf /var/lib/apt/lists/*\n"
                      "RUN pip3 install --no-cache-dir buildozer cython\nWORKDIR /work\n")
        return {"adapter": self.id, "template_id": self.template_id, "target": target,
                "image": "buildbix/kivy-android:" + hashlib.sha256(dockerfile.encode()).hexdigest()[:12],
                "dockerfile": dockerfile, "command": "buildozer -v android debug" + ("" if spec_ok else " < <(yes)"),
                "artifact_glob": "bin/*.apk", "requires_network": True, "requires_sdk_license": not spec_ok,
                "sdk_license_accepted_by_user": bool(accept_license), "github_workflow": _wf(self.template_id, analysis, ci_params),
                "workflow_file": "buildbix-android.yml", "artifact_name": ARTIFACT_NAME,
                "steps": [{"id": "extract", "title": "Extract source"}, {"id": "image", "title": "Prepare sandbox image"},
                          {"id": "build", "title": "Run buildozer"}, {"id": "collect", "title": "Collect artifacts + SHA-256"}],
                "notes": ["First build downloads the Android SDK/NDK (several GB); later builds reuse a per-workspace cache."]}


class FlutterAndroidAdapter:
    id, template_id = "flutter-android", "flutter-android"
    IMAGE = "ghcr.io/cirruslabs/flutter:stable"

    def supports(self, analysis, target):
        return target == "android-apk" and "Flutter" in analysis.get("frameworks", []) \
            and bool((analysis.get("flutter") or {}).get("android_dir"))

    def plan(self, analysis, target, accept_license, ci_params=None):
        return {"adapter": self.id, "template_id": self.template_id, "target": target, "image": self.IMAGE, "dockerfile": None,
                "command": "flutter pub get && flutter build apk --release",
                "artifact_glob": "build/app/outputs/flutter-apk/app-release.apk", "requires_network": True,
                "requires_sdk_license": True, "sdk_license_accepted_by_user": bool(accept_license), "experimental": True,
                "github_workflow": _wf(self.template_id, analysis, ci_params), "workflow_file": "buildbix-flutter.yml",
                "artifact_name": ARTIFACT_NAME,
                "steps": [{"id": "build", "title": "flutter build apk --release"}, {"id": "collect", "title": "Collect artifact"}],
                "notes": ["EXPERIMENTAL: not verified. Release APK uses debug signing unless you configure your own keystore.",
                          "The third-party Flutter image may need write access to its SDK folder; if it fails use the GitHub Actions runner."]}


class ReactWebAdapter:
    id, template_id = "node-web", "web-bundle"

    def supports(self, analysis, target):
        n = analysis.get("node") or {}
        return target == "web-bundle" and bool(n.get("build_script") and n.get("out_dir")) \
            and "Next.js" not in analysis.get("frameworks", [])

    def plan(self, analysis, target, accept_license, ci_params=None):
        out = analysis["node"]["out_dir"]
        install = "npm ci" if analysis["node"].get("lockfile") else "npm install"
        cmd = f"set -e; {install}; npm run build; tar -czf /work/web-bundle.tar.gz -C /work/{out} ."
        return {"adapter": self.id, "template_id": self.template_id, "target": target, "image": "node:20", "dockerfile": None,
                "command": cmd, "artifact_glob": "web-bundle.tar.gz", "requires_network": True, "requires_sdk_license": False,
                "sdk_license_accepted_by_user": False, "experimental": True,
                "github_workflow": _wf(self.template_id, analysis, ci_params), "workflow_file": "buildbix-web.yml",
                "artifact_name": ARTIFACT_NAME,
                "steps": [{"id": "install", "title": install}, {"id": "build", "title": "npm run build"},
                          {"id": "collect", "title": f"Archive {out}/ as web-bundle.tar.gz"}],
                "notes": ["EXPERIMENTAL: not verified against real projects. The 'node:20' tag is not pinned."]}


class PythonDesktopAdapter:
    """Windows/Linux/macOS executables via PyInstaller. GitHub Actions only: Windows/macOS cannot run in Docker on Linux."""
    id, template_id = "python-desktop", "desktop-bundle"

    def supports(self, analysis, target):
        fw = set(analysis.get("frameworks", []))
        return target == "desktop-bundle" and "Python" in analysis.get("languages", []) \
            and any(e.endswith(".py") for e in analysis.get("entry_points", [])) and not fw & {"Kivy", "Django", "Flask", "FastAPI"}

    def plan(self, analysis, target, accept_license, ci_params=None):
        return {"adapter": self.id, "template_id": self.template_id, "target": target, "image": None, "dockerfile": None,
                "command": None, "artifact_glob": None, "requires_network": True, "requires_sdk_license": False,
                "sdk_license_accepted_by_user": False, "experimental": True, "runner_only": "github_actions",
                "github_workflow": _wf(self.template_id, analysis, ci_params), "workflow_file": "buildbix-desktop.yml",
                "artifact_name": ARTIFACT_NAME,
                "steps": [{"id": "build", "title": "pyinstaller on ubuntu, windows and macos runners"},
                          {"id": "collect", "title": "Collect one artifact per OS"}],
                "notes": ["EXPERIMENTAL. Binaries are unsigned: Windows SmartScreen / macOS Gatekeeper will warn users.",
                          "Does not bundle data files or hidden imports automatically; complex apps may need a .spec file."]}


class PythonTestAdapter:
    id = "python-tests"

    def supports(self, analysis, target):
        return target == "run-tests" and "pytest" in analysis.get("tests", []) and "Python" in analysis.get("languages", [])

    def plan(self, analysis, target, accept_license, ci_params=None):
        cmd = ("set -e; export PATH=$PATH:$HOME/.local/bin; "
               "if [ -f requirements.txt ]; then python -m pip install --user -r requirements.txt; fi; "
               "python -m pip install --user pytest; python -m pytest -q")
        return {"adapter": self.id, "target": target, "image": "python:3.12-slim", "dockerfile": None, "command": cmd,
                "artifact_glob": None, "requires_network": True, "requires_sdk_license": False,
                "sdk_license_accepted_by_user": False, "github_workflow": None,
                "steps": [{"id": "install", "title": "pip install requirements + pytest"}, {"id": "test", "title": "pytest -q"}],
                "notes": ["Installs requirements.txt as-is; packages needing system libraries may fail to install."]}


ADAPTERS: list[Adapter] = [KivyAndroidAdapter(), FlutterAndroidAdapter(), ReactWebAdapter(), PythonDesktopAdapter(), PythonTestAdapter()]


def get_plan(analysis: dict, target: str, accept_license: bool = False, ci_params: dict | None = None) -> dict:
    for adapter in ADAPTERS:
        if adapter.supports(analysis, target):
            plan = adapter.plan(analysis, target, accept_license, ci_params)
            plan.setdefault("runner", "docker")
            return plan
    reasons = [t.get("reason") for t in analysis.get("targets", []) if t["id"] == target and t.get("reason")]
    raise UnsupportedTarget(f"Target '{target}' is not supported for this project" + (f": {reasons[0]}" if reasons else ""))
