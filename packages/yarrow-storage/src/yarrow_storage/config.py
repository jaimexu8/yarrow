from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageSettings(BaseSettings):
    """Object store settings shared by every service that reads or writes files.

    The defaults describe the compose stack (MinIO). On AWS, leave S3_ENDPOINT
    unset so boto3 resolves the regional endpoint, and leave the key pair unset
    so it picks up the task or instance role.
    """

    S3_ENDPOINT: str | None = "http://minio:9000"  # None on real AWS
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = "yarrow-documents"

    USE_LOCAL_STORAGE: bool = False
    LOCAL_STORAGE_DIR: str = "storage"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


storage_settings = StorageSettings()
