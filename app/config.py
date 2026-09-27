from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://invoice:invoice@localhost:5432/invoice"

    gemini_api_key: str = ""
    cheap_model: str = "gemini-2.5-flash"
    strong_model: str = "gemini-2.5-pro"

    # USD per 1M tokens, ≤200k-token prompt tier - see ai.google.dev/gemini-api/docs/pricing
    cheap_model_price_in: float = 0.30
    cheap_model_price_out: float = 2.50
    strong_model_price_in: float = 1.25
    strong_model_price_out: float = 10.00

    confidence_threshold: float = 0.75
    max_job_attempts: int = 5
    job_backoff_base_seconds: int = 10
    ocr_text_layer_min_chars: int = 20

    storage_dir: str = "./storage"

    @property
    def storage_path(self) -> Path:
        path = Path(self.storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
