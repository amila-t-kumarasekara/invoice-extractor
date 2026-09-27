"""Storage behind an interface, so local disk can become S3/MinIO later without
touching pipeline code. `storage_key` is an opaque string the pipeline treats
as an identifier, never as a filesystem path - only the concrete backend knows
how to turn it into bytes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def save(self, tenant_id: str, key: str, content: bytes) -> str:
        """Persist `content`, return the storage_key to record on the document."""
        ...

    def read(self, storage_key: str) -> bytes:
        ...

    def path_for_local_tools(self, storage_key: str) -> str:
        """Return a local filesystem path for tools (PyMuPDF, Tesseract) that
        need one. Raises for backends with no local path (e.g. S3) - those
        would need to download to a temp file first; not needed while the only
        backend is local disk."""
        ...


class LocalDiskStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, tenant_id: str, key: str, content: bytes) -> str:
        tenant_dir = self.root / tenant_id
        tenant_dir.mkdir(parents=True, exist_ok=True)
        full_path = tenant_dir / key
        full_path.write_bytes(content)
        return f"{tenant_id}/{key}"

    def read(self, storage_key: str) -> bytes:
        return (self.root / storage_key).read_bytes()

    def path_for_local_tools(self, storage_key: str) -> str:
        return str(self.root / storage_key)


_storage: LocalDiskStorage | None = None


def get_storage() -> LocalDiskStorage:
    global _storage
    if _storage is None:
        from app.core.config import get_settings

        _storage = LocalDiskStorage(get_settings().storage_path)
    return _storage
