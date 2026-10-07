import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analyzer import analyze_directory
from ..core import events
from ..core.archive import find_project_root, safe_extract_zip, zip_directory
from ..core.config import settings
from ..core.db import get_db, utcnow
from ..core.storage import get_storage
from ..models import Build, FixProposal, User
from ..services import autofix
from ..services.projects import new_source_key
from .deps import current_user, project_for

router = APIRouter(tags=["auto-fix"])


def _edits_view(edits: list) -> list:
    """List views never include file bodies (they can be large); GET /fixes/{id} returns everything."""
    return [{**{k: v for k, v in e.items() if k != "content"}, **({"bytes": len(e["content"].encode())} if "content" in e else {})} for e in edits]


def fix_out(f: FixProposal) -> dict:
    return {"id": f.id, "project_id": f.project_id, "build_id": f.build_id, "code": f.code, "title": f.title,
            "explanation": f.explanation, "edits": _edits_view(f.edits), "applicable": bool(f.edits), "status": f.status}


class SuggestIn(BaseModel):
    build_id: int | None = None


@router.post("/projects/{project_id}/fixes/suggest")
def suggest(project_id: int, body: SuggestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Diagnose analysis findings (and a failed build's log). Creates proposals only; changes nothing."""
    p = project_for(db, user, project_id, "developer")
    found = autofix.diagnose_analysis(p.analysis or {})
    if body.build_id is not None:
        b = db.get(Build, body.build_id)
        if not b or b.project_id != p.id:
            raise HTTPException(404, "Build not found")
        found += autofix.diagnose_log(get_storage().read_range(b.log_key, 0, 2 << 20).decode("utf-8", errors="replace"))
    existing = {(f.code, json.dumps(f.edits, sort_keys=True)) for f in db.scalars(
        select(FixProposal).where(FixProposal.project_id == p.id, FixProposal.status == "proposed"))}
    for d in found:
        key = (d["code"], json.dumps(d["edits"], sort_keys=True))
        if key not in existing:
            existing.add(key)
            db.add(FixProposal(project_id=p.id, build_id=body.build_id, **d))
    db.commit()
    return [fix_out(f) for f in db.scalars(select(FixProposal).where(FixProposal.project_id == p.id,
                                                                    FixProposal.status == "proposed").order_by(FixProposal.id))]


@router.get("/projects/{project_id}/fixes")
def list_fixes(project_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = project_for(db, user, project_id)
    return [fix_out(f) for f in db.scalars(select(FixProposal).where(FixProposal.project_id == p.id).order_by(FixProposal.id.desc()))]


def _fix_for(db: Session, user: User, fix_id: int) -> FixProposal:
    f = db.get(FixProposal, fix_id)
    if not f:
        raise HTTPException(404, "Not found")
    project_for(db, user, f.project_id, "developer")
    return f


@router.get("/fixes/{fix_id}")
def get_fix(fix_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    f = _fix_for(db, user, fix_id)
    return {**fix_out(f), "edits": f.edits}  # full content, for review before approving


@router.post("/fixes/{fix_id}/apply")
def apply_fix(fix_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Explicit approval step: patches a NEW snapshot of the project's source; then re-analyzes. Rebuild afterwards."""
    f = _fix_for(db, user, fix_id)
    p = project_for(db, user, f.project_id, "developer")
    if f.status != "proposed":
        raise HTTPException(409, f"Fix already {f.status}")
    if not f.edits:
        raise HTTPException(409, "This suggestion is informational; there is nothing to apply automatically")
    if p.source_kind != "zip":
        raise HTTPException(409, "Git projects are synced from GitHub; apply these edits in your repository: "
                            + json.dumps(_edits_view(f.edits)))
    storage = get_storage()
    try:
        with tempfile.TemporaryDirectory(prefix="bbx-fix-") as tmp:
            with storage.open(p.source_key) as src:
                safe_extract_zip(src, Path(tmp) / "src", max_bytes=settings.max_extracted_bytes, max_files=settings.max_zip_files)
            root = find_project_root(Path(tmp) / "src")
            for e in f.edits:
                autofix.apply_edit(root, e)
            out = Path(tmp) / "patched.zip"
            zip_directory(root, out)
            key = new_source_key(p.workspace_id)
            storage.put_file(key, out)
            analysis = analyze_directory(root)
    except autofix.FixError as e:
        raise HTTPException(422, str(e))
    p.source_key, p.analysis = key, analysis
    f.status, f.applied_at = "applied", utcnow()
    db.commit()
    events.publish(db, "fix.applied", workspace_id=p.workspace_id, actor_id=user.id, payload={"fix_id": f.id, "code": f.code})
    return {"status": "applied", "findings": analysis["findings"]}


@router.post("/fixes/{fix_id}/reject")
def reject_fix(fix_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    f = _fix_for(db, user, fix_id)
    if f.status == "proposed":
        f.status = "rejected"
        db.commit()
    return fix_out(f)
