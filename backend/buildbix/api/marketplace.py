import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ci import TEMPLATES, status
from ..core.archive import zip_directory
from ..core.db import get_db
from ..core.storage import get_storage
from ..models import Project, User
from ..services import projects as psvc
from ..templates.starters import STARTERS
from .deps import current_user, require_member
from .projects import _save_project

router = APIRouter(tags=["marketplace"])


@router.get("/marketplace")
def catalog(_user: User = Depends(current_user)):
    """Built-in catalog only. Publishing, ratings and third-party items are not implemented."""
    items = [{"id": f"starter:{k}", "type": "starter", "title": v["title"], "version": "1", "verified": False,
              "compatible_targets": v["targets"]} for k, v in STARTERS.items()]
    items += [{"id": f"ci:{t}", "type": "ci-template", "title": t, "version": "1", "verified": status(t).startswith("VERIFIED"),
               "status": status(t)} for t in TEMPLATES]
    return items


class StarterIn(BaseModel):
    starter: str
    name: str = Field(min_length=1, max_length=100)


def create_zip_project(db: Session, user: User, workspace_id: int, name: str, files: dict[str, str]) -> dict:
    storage, key = get_storage(), psvc.new_source_key(workspace_id)
    with tempfile.TemporaryDirectory(prefix="bbx-gen-") as tmp:
        root = Path(tmp) / "src"
        for rel, text in files.items():
            dest = (root / rel).resolve()
            if root.resolve() not in dest.parents:
                raise HTTPException(422, "unsafe path")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        out = Path(tmp) / "src.zip"
        zip_directory(root, out)
        storage.put_file(key, out)
    analysis = psvc.analyze_archive(storage, key)
    return _save_project(db, user, Project(workspace_id=workspace_id, name=name.strip(), source_kind="zip", source_key=key,
                                           analysis=analysis, created_by=user.id))


@router.post("/workspaces/{workspace_id}/projects/from-starter", status_code=201)
def from_starter(workspace_id: int, body: StarterIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_member(db, user, workspace_id, "developer")
    s = STARTERS.get(body.starter)
    if not s:
        raise HTTPException(404, "Unknown starter")
    return create_zip_project(db, user, workspace_id, body.name, s["files"])
