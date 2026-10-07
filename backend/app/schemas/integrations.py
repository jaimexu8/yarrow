from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class OAuthAuthorizeResponse(BaseModel):
    provider: str
    authorization_url: str


class OAuthCallbackRequest(BaseModel):
    provider: str
    code: str
    redirect_uri: str | None = None
    # Optional fields for direct / mock token provision
    access_token: str | None = None
    refresh_token: str | None = None
    account_email: str | None = None
    account_name: str | None = None


class CloudConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    account_email: str | None = None
    account_name: str | None = None
    last_synced_at: datetime | None = None
    created_at: datetime | None = None


class SyncResponse(BaseModel):
    provider: str
    imported_count: int
    files: list[str] = []
    message: str


class SyncStatusOut(BaseModel):
    provider: str
    connected: bool
    account_email: str | None = None
    last_synced_at: datetime | None = None
    is_syncing: bool = False
