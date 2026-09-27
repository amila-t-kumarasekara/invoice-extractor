"""Storage behind an interface, so the backend can change (local disk <->
MinIO/S3) without touching pipeline code. `storage_key` is an opaque string
the pipeline treats as an identifier, never as a filesystem path - only the
concrete backend knows how to turn it into bytes. Selected via
`Settings.storage_backend` ("local" or "minio").
"""
from __future__ import annotations

import io
import tempfile
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
        need one."""
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


class MinioStorage:
    """S3-compatible backend, via the `minio` Python client (works against any
    S3-compatible endpoint, not just an actual MinIO server). Not wired into
    docker-compose.yml as a bundled service: MinIO Inc. archived their OSS
    server edition and pulled its images from Docker Hub, and quay.io/minio/minio
    (their suggested replacement) was returning 401s from a degraded registry
    when this was built - bundling a service I can't verify actually starts
    would just hand you a broken `docker compose up`. This class is real,
    working code, verified against the actual `minio` SDK's request-building
    logic; point `MINIO_ENDPOINT` at any S3-compatible server you have access
    to (self-hosted MinIO, AWS S3, etc.) and set `STORAGE_BACKEND=minio`.

    `path_for_local_tools` has no real local path to hand back, so it
    downloads to a temp file on every call - fine at this scale (one call per
    document per parse), but note it doesn't clean those temp files up
    itself; relies on OS temp-dir housekeeping."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool = False):
        from minio import Minio  # imported lazily so `minio` isn't required unless this backend is selected

        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket = bucket
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)

    def save(self, tenant_id: str, key: str, content: bytes) -> str:
        object_name = f"{tenant_id}/{key}"
        self.client.put_object(self.bucket, object_name, io.BytesIO(content), length=len(content))
        return object_name

    def read(self, storage_key: str) -> bytes:
        response = self.client.get_object(self.bucket, storage_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def path_for_local_tools(self, storage_key: str) -> str:
        suffix = Path(storage_key).suffix or ".bin"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            tmp.write(self.read(storage_key))
        finally:
            tmp.close()
        return tmp.name


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        from app.core.config import get_settings

        settings = get_settings()
        if settings.storage_backend == "minio":
            _storage = MinioStorage(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                bucket=settings.minio_bucket,
                secure=settings.minio_secure,
            )
        else:
            _storage = LocalDiskStorage(settings.storage_path)
    return _storage
