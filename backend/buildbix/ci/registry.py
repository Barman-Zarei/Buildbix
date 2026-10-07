"""Template registry. A template is a pre-approved workflow file; ONLY the declared {{placeholders}} may vary,
and every value is validated against a whitelist/regex. AI never writes workflow text."""
import re
from dataclasses import dataclass
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "templates"
PLACEHOLDER = re.compile(r"(?<!\$)\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
PATH = r"\.|[A-Za-z0-9_][A-Za-z0-9_./-]{0,79}"


class ParamError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # enum | regex | list_enum
    default: str = ""
    allowed: tuple = ()
    pattern: str = ""
    ai: bool = True  # False = set only by BUILDBIX code, never by AI or API callers


@dataclass(frozen=True)
class Template:
    id: str
    file: str
    params: tuple


_PY = Param("python_version", "enum", "3.11", ("3.10", "3.11", "3.12"))
_WD = Param("workdir", "regex", ".", pattern=PATH)
TEMPLATES = {t.id: t for t in (
    Template("kivy-android", "kivy-android.yml", (
        _PY, _WD, Param("extra_apt", "list_enum", "", ("libffi-dev", "libssl-dev", "libsqlite3-dev", "libjpeg-dev", "libgl1-mesa-dev")),
        Param("license_stdin", "enum", "", ("", "< <(yes)"), ai=False))),
    Template("flutter-android", "flutter-android.yml", (
        Param("flutter_channel", "enum", "stable", ("stable", "beta")), Param("java_version", "enum", "17", ("17", "21")), _WD)),
    Template("web-bundle", "web-bundle.yml", (
        Param("node_version", "enum", "20", ("18", "20", "22")), _WD, Param("out_dir", "regex", "dist", pattern=PATH),
        Param("install_cmd", "enum", "npm install", ("npm ci", "npm install"), ai=False))),
    Template("desktop-bundle", "desktop-bundle.yml", (
        _PY, Param("entry_point", "regex", "main.py", pattern=r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,79}\.py"),
        Param("app_name", "regex", "app", pattern=r"[A-Za-z][A-Za-z0-9_-]{0,39}"))),
)}


def load_text(template_id: str) -> str:
    return (TEMPLATE_DIR / TEMPLATES[template_id].file).read_text(encoding="utf-8")


def status(template_id: str) -> str:
    for line in load_text(template_id).splitlines()[:8]:
        if line.startswith("# STATUS:"):
            return line[len("# STATUS:"):].strip()
    return "UNVERIFIED (no STATUS header)"


def normalize(p: Param, value) -> str:
    if p.kind == "enum":
        v = str(value)
        if v not in p.allowed:
            raise ParamError(f"{p.name}: must be one of {[a for a in p.allowed]}")
        return v
    if p.kind == "regex":
        v = str(value)
        if not re.fullmatch(p.pattern, v) or ".." in v:
            raise ParamError(f"{p.name}: invalid value")
        return v
    if p.kind == "list_enum":
        items = value.split() if isinstance(value, str) else list(value)
        bad = [i for i in items if i not in p.allowed]
        if bad:
            raise ParamError(f"{p.name}: not allowed: {bad}")
        return " ".join(sorted(set(items)))
    raise ParamError("unknown param kind")


def validate_user_params(template_id: str, values: dict) -> dict:
    """For values coming from AI or API callers: only AI-editable params, each strictly validated."""
    by_name = {p.name: p for p in TEMPLATES[template_id].params}
    out = {}
    for k, v in values.items():
        p = by_name.get(k)
        if p is None or not p.ai:
            raise ParamError(f"{k}: not an editable parameter")
        out[k] = normalize(p, v)
    return out


def describe(template_id: str) -> list[dict]:
    return [{"name": p.name, "kind": p.kind, "default": p.default, "allowed": list(p.allowed) or None,
             "pattern": p.pattern or None, "editable": p.ai} for p in TEMPLATES[template_id].params]


def check_templates() -> list[str]:
    """Used by tests and `python -m buildbix.ci.check`: every placeholder must be declared; defaults must render."""
    errors = []
    from .render import render
    for tid, t in TEMPLATES.items():
        text = load_text(tid)
        declared = {p.name for p in t.params}
        for name in set(PLACEHOLDER.findall(text)) - declared:
            errors.append(f"{tid}: placeholder {{{{{name}}}}} has no declared parameter")
        try:
            render(tid)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tid}: default render failed: {e}")
    return errors
