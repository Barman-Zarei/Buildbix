import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analyzer import analyze_directory
from ..core import events
from ..core.archive import find_project_root, safe_extract_zip
from ..core.config import settings
from ..core.crypto import decrypt
from ..core.storage import ObjectStorage, get_storage
from ..models import Project, Secret
from . import git as gitsvc


def analyze_archive(storage: ObjectStorage, key: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="bbx-an-") as tmp:
        with storage.open(key) as f:
            safe_extract_zip(f, Path(tmp), max_bytes=settings.max_extracted_bytes, max_files=settings.max_zip_files)
        return analyze_directory(find_project_root(Path(tmp)))


def new_source_key(workspace_id: int) -> str:
    return f"sources/{workspace_id}/{uuid.uuid4().hex}.zip"


def workspace_secret(db: Session, workspace_id: int, name: str, environment: str = "production") -> str | None:
    s = db.scalar(select(Secret).where(Secret.workspace_id == workspace_id, Secret.name == name,
                                       Secret.environment == environment))
    return decrypt(s.value_enc) if s else None


def sync_project_from_git(db: Session, project: Project, storage: ObjectStorage | None = None) -> None:
    storage = storage or get_storage()
    token = workspace_secret(db, project.workspace_id, "GITHUB_TOKEN")
    with tempfile.TemporaryDirectory(prefix="bbx-git-") as tmp:
        sha = gitsvc.clone_to_archive(project.repo_url, project.branch, token, Path(tmp))
        key = new_source_key(project.workspace_id)
        storage.put_file(key, Path(tmp) / "source.zip")
    project.source_key, project.commit_sha = key, sha
    project.analysis = analyze_archive(storage, key)
    db.commit()
    events.publish(db, events.PROJECT_UPDATED, workspace_id=project.workspace_id,
                   payload={"project_id": project.id, "commit": sha})
