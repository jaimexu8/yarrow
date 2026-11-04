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
