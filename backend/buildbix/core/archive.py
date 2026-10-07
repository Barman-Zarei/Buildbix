"""Hostile-input-safe archive handling (zip-slip, symlinks, zip bombs)."""
import os
import stat
import zipfile
from pathlib import Path
from typing import BinaryIO


class ArchiveError(ValueError):
    pass


def safe_extract_zip(fileobj: BinaryIO, dest: Path, *, max_bytes: int, max_files: int) -> None:
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(fileobj)
    except zipfile.BadZipFile as e:
        raise ArchiveError("Not a valid zip archive") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise ArchiveError("Archive contains too many files")
        if sum(i.file_size for i in infos) > max_bytes:
            raise ArchiveError("Archive is too large when extracted")
        written = 0
        for info in infos:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ArchiveError(f"Symlinks are not allowed: {info.filename}")
            target = (dest / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise ArchiveError(f"Unsafe path in archive: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 16):
                    written += len(chunk)
                    if written > max_bytes:  # headers can lie; count real bytes
                        raise ArchiveError("Archive is too large when extracted")
                    out.write(chunk)
            if (info.external_attr >> 16) & 0o100:
                os.chmod(target, 0o755)


def find_project_root(extracted: Path) -> Path:
    """GitHub-style zips wrap everything in one top-level folder; descend into it."""
    entries = [p for p in Path(extracted).iterdir() if p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return Path(extracted)


def zip_directory(src: Path, out_path: Path) -> None:
    src = Path(src)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                p = Path(dirpath) / name
                if p.is_symlink():
                    continue
                zf.write(p, p.relative_to(src).as_posix())
