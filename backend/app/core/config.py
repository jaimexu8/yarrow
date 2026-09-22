from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    POSTGRES_USER: str = "yarrow"
    POSTGRES_PASSWORD: str = "yarrow_password"
    POSTGRES_DB: str = "yarrow"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: str = "5432"

    VALKEY_HOST: str = "valkey"
    VALKEY_PORT: str = "6379"
    CELERY_BROKER_URL: str = "redis://valkey:6379/0"

    S3_ENDPOINT: str = "http://rustfs:9000"
    S3_ACCESS_KEY: str = "rustfsadmin"
    S3_SECRET_KEY: str = "rustfsadmin"
    S3_BUCKET_NAME: str = "yarrow-documents"
    USE_LOCAL_STORAGE: bool = False

    SECRET_KEY: str = "supersecretkey_please_change_in_production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    INFERENCE_SERVICE_URL: str = "http://inference:8000"
    INFERENCE_API_KEY: str = "mock_key"
    USE_MOCK_INFERENCE: bool = True

    NEXT_PUBLIC_API_URL: str = "http://localhost:8000"
    NEXT_PUBLIC_APP_URL: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

