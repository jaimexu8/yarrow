from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    INFERENCE_SERVICE_URL: str = "http://gateway:8080/layout-parsing"
    INFERENCE_API_KEY: str = ""
    INFERENCE_TIMEOUT_SECONDS: float = 900.0
    INFERENCE_MAX_ATTEMPTS: int = 3
    INFERENCE_RETRY_BACKOFF_SECONDS: float = 2.0

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
