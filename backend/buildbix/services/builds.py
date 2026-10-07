from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import events
from ..models import Build, Project
from ..pipeline import UnsupportedTarget, get_plan
from . import jobs
from .projects import workspace_secret


class BuildRequestError(ValueError):
    pass


def create_build(db: Session, project: Project, target: str, *, actor_id: int | None, trigger: str = "manual",
                 accept_sdk_license: bool = False, ignore_analysis_errors: bool = False, runner: str = "docker") -> Build:
    analysis = project.analysis or {}
    errors = [f["message"] for f in analysis.get("findings", []) if f["severity"] == "error"]
    if errors and not ignore_analysis_errors:
        raise BuildRequestError("Project analysis found blocking problems: " + "; ".join(errors))
    try:
        plan = get_plan(analysis, target, accept_sdk_license, project.ci_params)
    except UnsupportedTarget as e:
        raise BuildRequestError(str(e)) from e
    if runner not in ("docker", "github_actions"):
        raise BuildRequestError("runner must be 'docker' or 'github_actions'")
    if runner == "github_actions":
        if project.source_kind != "git" or not plan.get("workflow_file"):
            raise BuildRequestError("The GitHub Actions runner needs a GitHub project and a target with a workflow")
        if not workspace_secret(db, project.workspace_id, "GITHUB_TOKEN"):
            raise BuildRequestError("Set the workspace secret GITHUB_TOKEN first (Actions + Contents access)")
    if plan.get("runner_only") and runner != plan["runner_only"]:
        raise BuildRequestError(f"This target can only be built with runner '{plan['runner_only']}'")
    plan["runner"] = runner
    if plan["requires_sdk_license"] and not accept_sdk_license:
        raise BuildRequestError(
            "The Android SDK license is not accepted in buildozer.spec. Set android.accept_sdk_license = True "
            "or pass accept_sdk_license=true to confirm you accept it.")
    number = (db.scalar(select(func.max(Build.number)).where(Build.project_id == project.id)) or 0) + 1
    build = Build(project_id=project.id, number=number, target=target, plan=plan, source_key=project.source_key,
                  commit_sha=project.commit_sha, version=(analysis.get("buildozer") or {}).get("version"),
                  trigger=trigger, created_by=actor_id,
                  log_key=f"logs/{project.workspace_id}/{project.id}-{number}.log")
    db.add(build)
    db.commit()
    jobs.enqueue(db, "build", {"build_id": build.id})
    events.publish(db, events.BUILD_QUEUED, workspace_id=project.workspace_id, actor_id=actor_id,
                   payload={"build_id": build.id, "project_id": project.id, "target": target, "trigger": trigger})
    return build
