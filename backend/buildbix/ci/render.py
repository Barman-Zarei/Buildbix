import yaml

from . import registry as R


def derive(template_id: str, analysis: dict, accept_license: bool = False) -> dict:
    """Deterministic, trusted values taken from the project analysis (no AI involved)."""
    out: dict = {}
    if template_id == "kivy-android":
        out["license_stdin"] = "" if (analysis.get("buildozer") or {}).get("sdk_license_accepted") else "< <(yes)"
    elif template_id == "web-bundle":
        node = analysis.get("node") or {}
        if node.get("out_dir"):
            out["out_dir"] = node["out_dir"]
        out["install_cmd"] = "npm ci" if node.get("lockfile") else "npm install"
    elif template_id == "desktop-bundle":
        eps = [e for e in analysis.get("entry_points", []) if e.endswith(".py")]
        if eps:
            out["entry_point"] = eps[0]
    return out


def effective_params(template_id: str, analysis: dict | None = None, overrides: dict | None = None) -> dict:
    values = {p.name: p.default for p in R.TEMPLATES[template_id].params}
    trusted = derive(template_id, analysis or {})
    by_name = {p.name: p for p in R.TEMPLATES[template_id].params}
    values.update({k: R.normalize(by_name[k], v) for k, v in trusted.items()})
    values.update(R.validate_user_params(template_id, overrides or {}))
    return values


def render(template_id: str, overrides: dict | None = None, analysis: dict | None = None) -> str:
    values = effective_params(template_id, analysis, overrides)
    text = R.PLACEHOLDER.sub(lambda m: values[m.group(1)], R.load_text(template_id))
    if "{{" in text.replace("${{", ""):
        raise R.ParamError("unresolved placeholder left in template")
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or "jobs" not in doc:
        raise R.ParamError("rendered workflow is not a valid GitHub Actions workflow")
    return text
