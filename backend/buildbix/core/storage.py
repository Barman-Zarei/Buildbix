"""Object-storage abstraction. Large blobs (sources, logs, artifacts) never go in the relational DB."""
import hashlib
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

from .config import settings

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,250}$")


class ObjectStorage(ABC):
    @abstractmethod
    def put_file(self, key: str, src: Path) -> tuple[int, str]: ...
    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None: ...
    @abstractmethod
    def append(self, key: str, data: bytes) -> None: ...
    @abstractmethod
    def open(self, key: str) -> BinaryIO: ...
    @abstractmethod
    def read_range(self, key: str, offset: int, limit: int) -> bytes: ...
    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalStorage(ObjectStorage):
    """Filesystem backend. An S3/MinIO backend implements the same interface (append = buffered multipart)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not _KEY_RE.match(key) or ".." in key.split("/"):
            raise ValueError("invalid storage key")
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put_file(self, key, src):
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        h, size = hashlib.sha256(), 0
        with open(src, "rb") as i, open(dst, "wb") as o:
            while chunk := i.read(1 << 20):
                h.update(chunk)
                size += len(chunk)
                o.write(chunk)
        return size, h.hexdigest()

    def put_bytes(self, key, data):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def append(self, key, data):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as f:
            f.write(data)

    def open(self, key):
        return open(self._path(key), "rb")

    def read_range(self, key, offset, limit):
        p = self._path(key)
        if not p.exists():
            return b""
        with open(p, "rb") as f:
            f.seek(offset)
            return f.read(limit)

    def exists(self, key):
        return self._path(key).exists()


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        _storage = LocalStorage(settings.data_dir / "objects")
    return _storage
