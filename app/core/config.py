from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://invoice:invoice@localhost:5432/invoice"

    gemini_api_key: str = ""
    cheap_model: str = "gemini-3.5-flash"
    strong_model: str = "gemini-3.5-pro"

    # USD per 1M tokens, <=200k-token prompt tier - see ai.google.dev/gemini-api/docs/pricing
    cheap_model_price_in: float = 0.30
    cheap_model_price_out: float = 2.50
    strong_model_price_in: float = 1.25
    strong_model_price_out: float = 10.00

    confidence_threshold: float = 0.75
    classify_confidence_threshold: float = 0.6  # lower bar: "is this even the right doctype", not "auto-approve"
    max_job_attempts: int = 5
    job_backoff_base_seconds: int = 10
    stale_lock_minutes: int = 10  # a job locked longer than this is assumed crashed and reclaimable
    ocr_text_layer_min_chars: int = 20

    # Phase 10: fired (best-effort, failures logged not raised) when a document becomes `approved`.
    webhook_url: str = ""
    webhook_timeout_seconds: float = 5.0

    # Storage backend - see app/core/storage.py. "local" (default) or "minio".
    storage_backend: str = "local"
    storage_dir: str = "./storage"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "invoice-extractor"
    minio_secure: bool = False

    # Phase 2: upload safety
    max_upload_bytes: int = 15 * 1024 * 1024  # 15 MB
    max_pages: int = 25
    allowed_mime_types: tuple[str, ...] = ("application/pdf", "image/png", "image/jpeg")

    # Origins allowed to call the API from a browser - the Next.js UI in /ui
    # runs on its own dev server/port, so this isn't same-origin.
    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    @property
    def storage_path(self) -> Path:
        path = Path(self.storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
