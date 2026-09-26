from pydantic_settings import SettingsConfigDict
from yarrow_db.config import DatabaseSettings


class Settings(DatabaseSettings):
    """Backend settings. Postgres fields are inherited from DatabaseSettings."""

    VALKEY_HOST: str = "valkey"
    VALKEY_PORT: str = "6379"
    CELERY_BROKER_URL: str = "redis://valkey:6379/0"

    S3_ENDPOINT: str = "http://minio:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET_NAME: str = "yarrow-documents"
    USE_LOCAL_STORAGE: bool = False

    # The design document (3.1, option 4) bounds user uploads by total bytes,
    # because a file count says nothing about how many pages it holds.
    STORAGE_QUOTA_BYTES: int = 1024 * 1024 * 1024  # 1 GiB per user
    MAX_UPLOAD_BYTES: int = 256 * 1024 * 1024  # 256 MiB per file

    SECRET_KEY: str = "supersecretkey_please_change_in_production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Kept for an admin health view; the backend itself never calls inference.
    # Which endpoint the *worker* uses is set in its own Settings.
    INFERENCE_SERVICE_URL: str = "http://gateway:8080/layout-parsing"
    INFERENCE_API_KEY: str = ""

    # Outbound email (US-1 verification, US-67 reset). SMTP_HOST unset means
    # "no mail server": messages are logged instead, which is what a developer
    # running the backend outside compose gets. Compose points this at Mailpit.
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 1025
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_TLS: bool = False
    EMAIL_FROM: str = "Yarrow <no-reply@yarrow.local>"
    VERIFICATION_CODE_TTL_MINUTES: int = 15
    VERIFICATION_MAX_ATTEMPTS: int = 5
    VERIFICATION_RESEND_COOLDOWN_SECONDS: int = 60
    VERIFICATION_MAX_SENDS_PER_HOUR: int = 5

    NEXT_PUBLIC_API_URL: str = "http://localhost:8000"
    NEXT_PUBLIC_APP_URL: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
