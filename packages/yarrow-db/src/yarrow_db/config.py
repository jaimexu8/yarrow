from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Connection settings shared by every service that talks to Postgres."""

    POSTGRES_USER: str = "yarrow"
    POSTGRES_PASSWORD: str = "yarrow_password"
    POSTGRES_DB: str = "yarrow"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: str = "5432"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def async_url(self) -> str:
        return self._url("postgresql+asyncpg")

    @property
    def sync_url(self) -> str:
        return self._url("postgresql+psycopg2")

    def _url(self, driver: str) -> str:
        return (
            f"{driver}://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


db_settings = DatabaseSettings()
