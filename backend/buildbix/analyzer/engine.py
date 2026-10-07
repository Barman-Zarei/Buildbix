"""Project analyzer. Detectors are small functions that mutate a Context; add a detector to extend."""
import configparser
import json
import os
import re
import tomllib
from collections import Counter
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".buildozer", "build", "dist", "bin",
             ".gradle", "target", ".idea", ".dart_tool"}
MAX_FILES = 20000
LANG_EXT = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
            ".java": "Java", ".kt": "Kotlin", ".dart": "Dart", ".go": "Go", ".rs": "Rust", ".php": "PHP",
            ".cs": "C#", ".c": "C/C++", ".cc": "C/C++", ".cpp": "C/C++", ".h": "C/C++", ".hpp": "C/C++"}
SECRETISH = re.compile(r"(^|/)(\.env(\..+)?|id_rsa|id_ed25519|[^/]+\.(pem|jks|keystore|p12))$", re.I)
ENTRY_CANDIDATES = ["main.py", "app.py", "manage.py", "wsgi.py", "index.js", "server.js", "src/main.ts",
                    "src/index.ts", "src/main.jsx", "src/main.tsx", "lib/main.dart", "main.go", "src/main.rs"]
UNBUILDABLE_ON_ANDROID = {"tensorflow": "no python-for-android recipe", "torch": "no python-for-android recipe",
                          "opencv-python": "use the 'opencv' recipe instead"}
PY_TOOLING = {"kivy", "python3", "pip", "setuptools", "wheel", "cython", "buildozer", "pytest", "black", "flake8",
              "pytest-cov", "mypy", "ruff"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def parse_requirements(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "http://", "https://", "git+")):
            continue
        m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)
        if m:
            out.append(norm(m.group(0)))
    return out


class Context:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.files: list[str] = []
        self.res: dict = {"analyzer_version": 1, "languages": [], "frameworks": [], "build_systems": [],
                          "package_managers": [], "entry_points": [], "dependencies": {}, "tests": [],
                          "findings": [], "targets": [], "buildozer": None, "node": None, "flutter": None}
        self._walk()

    def _walk(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                self.files.append((Path(dirpath) / f).relative_to(self.root).as_posix())
                if len(self.files) >= MAX_FILES:
                    return

    def has(self, rel: str) -> bool:
        return (self.root / rel).is_file()

    def read(self, rel: str, limit: int = 256 * 1024) -> str:
        try:
            return (self.root / rel).read_text(encoding="utf-8", errors="replace")[:limit]
        except OSError:
            return ""

    def add(self, key: str, value: str) -> None:
        if value not in self.res[key]:
            self.res[key].append(value)

    def finding(self, severity: str, code: str, message: str, data: dict | None = None) -> None:
        f = {"severity": severity, "code": code, "message": message}
        if data:
            f["data"] = data  # machine-readable detail used by Auto-Fix
        self.res["findings"].append(f)


# ---------------------------------------------------------------- detectors
def detect_languages(c: Context) -> None:
    counts = Counter(LANG_EXT[Path(f).suffix.lower()] for f in c.files if Path(f).suffix.lower() in LANG_EXT)
    for lang, _ in counts.most_common():
        c.add("languages", lang)


def detect_python(c: Context) -> None:
    deps: set[str] = set()
    if c.has("requirements.txt"):
        c.add("package_managers", "pip")
        deps |= set(parse_requirements(c.read("requirements.txt")))
    if c.has("pyproject.toml"):
        try:
            data = tomllib.loads(c.read("pyproject.toml"))
            deps |= {norm(re.split(r"[<>=~!\[; ]", d)[0]) for d in data.get("project", {}).get("dependencies", [])}
            poetry = data.get("tool", {}).get("poetry")
            if poetry is not None:
                c.add("package_managers", "Poetry")
                c.add("build_systems", "Poetry")
                deps |= {norm(k) for k in poetry.get("dependencies", {}) if k.lower() != "python"}
        except tomllib.TOMLDecodeError:
            c.finding("error", "PYPROJECT_INVALID", "pyproject.toml is not valid TOML")
    if "Python" in c.res["languages"] or deps:
        c.res["dependencies"]["python"] = sorted(deps)
        if not (c.has("requirements.txt") or c.has("pyproject.toml")):
            c.finding("warning", "PY_NO_DEPS_FILE", "No requirements.txt or pyproject.toml found")
    for dep, fw in (("django", "Django"), ("flask", "Flask"), ("fastapi", "FastAPI")):
        if dep in deps:
            c.add("frameworks", fw)
    if "pytest" in deps or any(f.startswith("tests/") or re.search(r"(^|/)test_[^/]*\.py$", f) for f in c.files):
        c.add("tests", "pytest")


def detect_node(c: Context) -> None:
    if not c.has("package.json"):
        return
    try:
        pkg = json.loads(c.read("package.json"))
    except json.JSONDecodeError:
        c.finding("error", "PACKAGE_JSON_INVALID", "package.json is not valid JSON")
        return
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    c.res["dependencies"]["node"] = sorted(deps)
    out_dir = "dist" if "vite" in deps else ("build" if "react-scripts" in deps else None)
    c.res["node"] = {"build_script": "build" in pkg.get("scripts", {}), "out_dir": out_dir, "lockfile": c.has("package-lock.json")}
    c.add("frameworks", "Node.js")
    if c.has("pnpm-lock.yaml"):
        c.add("package_managers", "pnpm")
    elif c.has("yarn.lock"):
        c.add("package_managers", "yarn")
    else:
        c.add("package_managers", "npm")
        if not c.has("package-lock.json"):
            c.finding("warning", "NODE_NO_LOCKFILE", "No lockfile found; builds will not be reproducible")
    for dep, fw in (("react", "React"), ("next", "Next.js"), ("vue", "Vue"), ("vite", "Vite"), ("electron", "Electron")):
        if dep in deps:
            c.add("frameworks", fw)
    test = pkg.get("scripts", {}).get("test", "")
    if test and "no test specified" not in test:
        c.add("tests", "npm test")


def detect_others(c: Context) -> None:
    if c.has("pubspec.yaml") and re.search(r"sdk:\s*flutter", c.read("pubspec.yaml")):
        c.add("frameworks", "Flutter"); c.add("build_systems", "Flutter"); c.add("package_managers", "pub")
        c.res["flutter"] = {"android_dir": any(f.startswith("android/") for f in c.files)}
        if any(f.startswith("test/") for f in c.files):
            c.add("tests", "flutter test")
    if c.has("Cargo.toml"):
        c.add("build_systems", "Cargo"); c.add("package_managers", "cargo"); c.add("tests", "cargo test")
    if c.has("go.mod"):
        c.add("build_systems", "Go modules"); c.add("tests", "go test")
    if c.has("pom.xml"):
        c.add("build_systems", "Maven")
    if c.has("build.gradle") or c.has("build.gradle.kts"):
        c.add("build_systems", "Gradle")
    if c.has("Dockerfile"):
        c.add("build_systems", "Docker")
    if c.has("CMakeLists.txt"):
        c.add("build_systems", "CMake")
    if c.has("composer.json") and "laravel/framework" in c.read("composer.json"):
        c.add("frameworks", "Laravel")
    if any(f.endswith((".csproj", ".sln")) for f in c.files):
        c.add("frameworks", ".NET")


def _imports_kivy(c: Context) -> bool:
    n = 0
    for rel in c.files:
        if rel.endswith(".py"):
            n += 1
            if n > 300:
                break
            if re.search(r"^\s*(from|import)\s+kivy\b", c.read(rel, 65536), re.M):
                return True
    return False


def _req_names(value: str) -> list[str]:
    return [norm(re.split(r"[<>=~!@\[ ]", r.strip())[0]) for r in value.split(",") if r.strip()]


def detect_kivy(c: Context) -> None:
    py_deps = set(c.res["dependencies"].get("python", []))
    has_spec = c.has("buildozer.spec")
    spec_reqs: list[str] = []
    app = None
    if has_spec:
        c.add("build_systems", "Buildozer")
        cp = configparser.ConfigParser(interpolation=None, strict=False, allow_no_value=True)
        try:
            cp.read_string(c.read("buildozer.spec"))
            app = cp["app"] if cp.has_section("app") else None
            if app is None:
                c.finding("error", "SPEC_NO_APP_SECTION", "buildozer.spec has no [app] section")
        except configparser.Error as e:
            c.finding("error", "SPEC_INVALID", f"buildozer.spec cannot be parsed: {e.message[:120]}")
    get = (lambda k: (app.get(k) or "").strip() or None) if app is not None else (lambda k: None)
    if app is not None:
        spec_reqs = _req_names(get("requirements") or "")
    is_kivy = bool({"kivy", "kivymd"} & py_deps) or "kivy" in spec_reqs or _imports_kivy(c)
    if not is_kivy:
        return
    c.add("frameworks", "Kivy")
    if not has_spec:
        c.finding("error", "KIVY_NO_SPEC", "Kivy project without buildozer.spec; one must be created to build for Android")
        return
    if app is None:
        return
    info = {"package": None, "version": get("version"), "sdk_license_accepted":
            (get("android.accept_sdk_license") or "").lower() == "true"}
    for key in ("title", "package.name", "package.domain"):
        if not get(key):
            c.finding("error", "SPEC_MISSING_KEY", f"buildozer.spec is missing '{key}'")
    name, domain = get("package.name"), get("package.domain")
    if name and not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        c.finding("error", "SPEC_BAD_PACKAGE_NAME", "package.name must be lowercase letters, digits, underscores")
    if domain and not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", domain):
        c.finding("error", "SPEC_BAD_DOMAIN", "package.domain must look like 'org.example'")
    if name and domain:
        info["package"] = f"{domain}.{name}"
    if not get("version") and not (get("version.regex") and get("version.filename")):
        c.finding("error", "SPEC_NO_VERSION", "Set 'version' (or version.regex + version.filename) in buildozer.spec")
    if "kivy" not in spec_reqs:
        c.finding("error", "SPEC_NO_KIVY_REQ", "'kivy' is not listed in buildozer.spec requirements")
    if "python3" not in spec_reqs:
        c.finding("warning", "SPEC_NO_PYTHON3", "'python3' is not listed in buildozer.spec requirements")
    missing = sorted(d for d in py_deps if d not in spec_reqs and d not in PY_TOOLING)
    if missing:
        c.finding("warning", "REQ_NOT_IN_SPEC", "In requirements.txt but not in buildozer.spec (will be missing in the APK): "
                  + ", ".join(missing), data={"packages": missing})
    for bad, why in UNBUILDABLE_ON_ANDROID.items():
        if bad in py_deps or bad in spec_reqs:
            c.finding("warning", "ANDROID_INCOMPATIBLE_DEP", f"'{bad}': {why}")
    src = (get("source.dir") or ".").strip("/") or "."
    if not c.has("main.py" if src == "." else f"{src}/main.py"):
        c.finding("error", "SPEC_NO_MAIN", f"main.py not found in source.dir '{src}'")
    api, minapi = get("android.api"), get("android.minapi")
    try:
        if api and int(api) < 33:
            c.finding("warning", "ANDROID_API_OLD", f"android.api={api} is old; Google Play may reject it (check current requirements)")
        if api and minapi and int(minapi) > int(api):
            c.finding("error", "ANDROID_MINAPI_GT_API", "android.minapi is greater than android.api")
    except ValueError:
        c.finding("error", "ANDROID_API_NOT_INT", "android.api / android.minapi must be integers")
    network_libs = {"requests", "urllib3", "httpx", "aiohttp", "websockets", "websocket-client"}
    if network_libs & set(spec_reqs + sorted(py_deps)) and "INTERNET" not in (get("android.permissions") or "").upper():
        c.finding("warning", "ANDROID_NO_INTERNET", "Networking library used but android.permissions lacks INTERNET")
    c.res["buildozer"] = info


def detect_suspicious(c: Context) -> None:
    for f in c.files:
        if SECRETISH.search(f) and not f.endswith((".example", ".sample", ".template")):
            c.finding("warning", "SECRET_FILE", f"'{f}' looks like a secret/key file; keep it out of the source archive")
    for ep in ENTRY_CANDIDATES:
        if c.has(ep):
            c.add("entry_points", ep)


def detect_targets(c: Context) -> None:
    fw, bs, t = set(c.res["frameworks"]), set(c.res["build_systems"]), c.res["targets"]
    if "Kivy" in fw:
        ok = "Buildozer" in bs
        t.append({"id": "android-apk", "label": "Android APK", "framework": "Kivy", "supported": ok,
                  "reason": None if ok else "buildozer.spec is required"})
        t.append({"id": "android-aab", "label": "Android AAB", "framework": "Kivy", "supported": False,
                  "reason": "Planned: needs a verified release-signing flow"})
    if "Flutter" in fw:
        ok = bool((c.res["flutter"] or {}).get("android_dir"))
        t.append({"id": "android-apk", "label": "Android APK", "framework": "Flutter", "supported": ok,
                  "experimental": True, "reason": None if ok else "android/ directory not found"})
    node = c.res["node"]
    if "Next.js" in fw:
        t.append({"id": "web-bundle", "label": "Web production bundle", "framework": "Next.js", "supported": False,
                  "reason": "Next.js needs a server/export adapter (planned)"})
    elif node and fw & {"React", "Vite", "Vue"}:
        ok = bool(node["build_script"] and node["out_dir"])
        reason = None if ok else ("no 'build' script in package.json" if not node["build_script"]
                                  else "only Vite and react-scripts output folders are known")
        t.append({"id": "web-bundle", "label": "Web production bundle", "framework": "JS", "supported": ok,
                  "experimental": True, "reason": reason})
    if "Docker" in bs or fw & {"FastAPI", "Flask", "Django"}:
        t.append({"id": "docker-image", "label": "Docker image", "framework": "Docker", "supported": False,
                  "reason": "Docker adapter planned"})
    if "Python" in c.res["languages"] and any(e.endswith(".py") for e in c.res["entry_points"]) \
            and not fw & {"Kivy", "Django", "Flask", "FastAPI"}:
        t.append({"id": "desktop-bundle", "label": "Desktop executables (Windows/Linux/macOS)", "framework": "Python",
                  "supported": True, "experimental": True, "runner_only": "github_actions", "reason": None})
    if "pytest" in c.res["tests"] and "Python" in c.res["languages"]:
        t.append({"id": "run-tests", "label": "Run tests (pytest)", "framework": "Python", "supported": True, "reason": None})


DETECTORS = [detect_languages, detect_python, detect_node, detect_others, detect_kivy, detect_suspicious, detect_targets]


def analyze_directory(root: Path) -> dict:
    c = Context(root)
    for d in DETECTORS:
        d(c)
    return c.res
