import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import CloudCredential, User

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas.integrations import (
    CloudConnectionOut,
    OAuthAuthorizeResponse,
    OAuthCallbackRequest,
    SyncResponse,
    SyncStatusOut,
)
from app.services.cloud_storage import (
    exchange_code_for_tokens,
    get_oauth_authorize_url,
    normalize_provider,
    periodic_cloud_sync,
    save_or_update_credential,
    sync_credential_documents,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/oauth/{provider}/authorize", response_model=OAuthAuthorizeResponse)
async def get_authorization_url(
    provider: str,
    redirect_uri: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    """Generate the OAuth authorization URL for Google Drive or Dropbox."""
    try:
        norm = normalize_provider(provider)
        auth_url = get_oauth_authorize_url(norm, redirect_uri)
        return OAuthAuthorizeResponse(provider=norm, authorization_url=auth_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.post("/oauth/{provider}/callback", response_model=CloudConnectionOut)
async def oauth_callback(
    provider: str,
    payload: OAuthCallbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Handle OAuth callback, exchange authorization code for tokens, and store credentials."""
    norm = normalize_provider(provider)
    if payload.access_token:
        tokens = {
            "access_token": payload.access_token,
            "refresh_token": payload.refresh_token,
            "account_email": payload.account_email or f"user@{norm}.com",
            "account_name": payload.account_name or f"{norm.capitalize()} Account",
        }
    else:
        tokens = await exchange_code_for_tokens(
            provider=norm,
            code=payload.code,
            redirect_uri=payload.redirect_uri,
        )

    credential = await save_or_update_credential(
        db=db,
        user_id=current_user.id,
        provider=norm,
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        account_email=tokens.get("account_email"),
        account_name=tokens.get("account_name"),
    )

    return credential


@router.get("/oauth/{provider}/callback", response_model=CloudConnectionOut)
async def oauth_callback_get(
    provider: str,
    code: str = Query(...),
    redirect_uri: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Handle GET redirect callback from provider OAuth flow."""
    norm = normalize_provider(provider)
    tokens = await exchange_code_for_tokens(
        provider=norm,
        code=code,
        redirect_uri=redirect_uri,
    )
    credential = await save_or_update_credential(
        db=db,
        user_id=current_user.id,
        provider=norm,
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        account_email=tokens.get("account_email"),
        account_name=tokens.get("account_name"),
    )
    return credential


@router.get("/connections", response_model=list[CloudConnectionOut])
async def list_connections(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all connected cloud storage accounts for the current user."""
    result = await db.execute(
        select(CloudCredential).where(CloudCredential.user_id == current_user.id)
    )
    return list(result.scalars().all())


@router.delete("/connections/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_provider(
    provider: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Disconnect a cloud storage provider and remove its credentials."""
    norm = normalize_provider(provider)
    await db.execute(
        delete(CloudCredential).where(
            CloudCredential.user_id == current_user.id,
            CloudCredential.provider == norm,
        )
    )
    await db.commit()


@router.get("/status", response_model=list[SyncStatusOut])
async def get_sync_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get connection and sync status for each supported provider."""
    result = await db.execute(
        select(CloudCredential).where(CloudCredential.user_id == current_user.id)
    )
    credentials = {c.provider: c for c in result.scalars().all()}

    statuses: list[SyncStatusOut] = []
    for prov in ("google", "dropbox"):
        cred = credentials.get(prov)
        statuses.append(
            SyncStatusOut(
                provider=prov,
                connected=cred is not None,
                account_email=cred.account_email if cred else None,
                last_synced_at=cred.last_synced_at if cred else None,
                is_syncing=False,
            )
        )
    return statuses


@router.post("/{provider}/sync", response_model=SyncResponse)
async def sync_provider(
    provider: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger document import from a specific connected cloud provider."""
    norm = normalize_provider(provider)
    result = await db.execute(
        select(CloudCredential).where(
            CloudCredential.user_id == current_user.id,
            CloudCredential.provider == norm,
        )
    )
    credential = result.scalars().first()
    if not credential:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{norm.capitalize()} account is not connected",
        )

    count, files = await sync_credential_documents(db, credential)
    if count == 0:
        msg = f"No new supported documents found in {norm.capitalize()}."
    else:
        msg = f"Successfully imported {count} document(s) from {norm.capitalize()}."
    return SyncResponse(
        provider=norm,
        imported_count=count,
        files=files,
        message=msg,
    )


@router.post("/sync", response_model=list[SyncResponse])
async def sync_all(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger document import from all connected cloud providers for the user."""
    result = await db.execute(
        select(CloudCredential).where(CloudCredential.user_id == current_user.id)
    )
    credentials = list(result.scalars().all())
    if not credentials:
        return []

    responses: list[SyncResponse] = []
    for cred in credentials:
        count, files = await sync_credential_documents(db, cred)
        responses.append(
            SyncResponse(
                provider=cred.provider,
                imported_count=count,
                files=files,
                message=f"Successfully imported {count} documents from {cred.provider.capitalize()}",
            )
        )
    return responses


@router.post("/periodic-sync")
async def trigger_periodic_sync(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Background task endpoint to periodically fetch documents from all users' connected cloud storage."""
    background_tasks.add_task(periodic_cloud_sync, db)
    return {"message": "Periodic cloud sync scheduled"}
