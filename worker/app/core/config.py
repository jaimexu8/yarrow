import importlib.metadata
import tomllib
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    INFERENCE_SERVICE_URL: str = "http://gateway:8080/layout-parsing"
    INFERENCE_API_KEY: str = ""
    INFERENCE_TIMEOUT_SECONDS: float = 900.0
    INFERENCE_MAX_ATTEMPTS: int = 3
    INFERENCE_RETRY_BACKOFF_SECONDS: float = 2.0
    THUMBNAIL_WIDTH_PX: int = 160
    THUMBNAIL_QUALITY: int = 70
    INFERENCE_MODEL_NAME: str = "unknown"
    INFERENCE_MODEL_VERSION: str = "unknown"

    INGEST_PAGE_BATCH_SIZE: int = 25

    TEXT_LAYER_ENABLED: bool = True

    TEXT_LAYER_MIN_WORDS: int = 12
    TEXT_LAYER_MIN_ALPHA_CHARS: int = 40
    TEXT_LAYER_MIN_ALPHA_RATIO: float = 0.60
    TEXT_LAYER_MAX_PRIVATE_USE_RATIO: float = 0.01
    TEXT_LAYER_MAX_SYMBOL_RATIO: float = 0.05
    TEXT_LAYER_MAX_CONTROL_RATIO: float = 0.01
    TEXT_LAYER_MIN_IN_PAGE_RATIO: float = 0.95
    TEXT_LAYER_WORD_HEIGHT_MIN_PT: float = 6.0
    TEXT_LAYER_WORD_HEIGHT_MAX_PT: float = 40.0

    PAGE_QUEUE_TIMEOUT_SECONDS: int = 1800
    PAGE_STALE_SECONDS: int = 1800
    MAX_PAGE_DELIVERIES: int = 6
    PAGE_COUNTERS_REDIS_URL: str = "redis://valkey:6379/3"

    # Nightly storage sweep. Dry run is on by default so the first run only
    # reports what it would remove. The grace window covers the gap between
    # uploading a page object and committing the row that references it.
    STORAGE_SWEEP_ENABLED: bool = True
    STORAGE_SWEEP_DRY_RUN: bool = True
    STORAGE_SWEEP_GRACE_HOURS: int = 24
    STORAGE_SWEEP_MAX_DELETES: int = 10000
    STORAGE_SWEEP_LOCK_SECONDS: int = 7200
    STORAGE_SWEEP_CRON_HOUR: int = 3

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()


def _pipeline_version() -> str:
    try:
        return importlib.metadata.version("yarrow-worker")
    except importlib.metadata.PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        with pyproject.open("rb") as file:
            return tomllib.load(file)["tool"]["poetry"]["version"]


PIPELINE_VERSION = _pipeline_version()
