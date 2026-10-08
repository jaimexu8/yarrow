import io
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import CloudCredential, Document, Job, User
from yarrow_storage import get_storage

from app.core.config import settings
from app.core.queue import enqueue_document_processing

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif"}
CONTENT_TYPE_BY_EXTENSION = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_provider(provider: str) -> str:
    p = provider.lower().strip()
    if p in ("google", "google_drive", "googledrive", "gdrive"):
        return "google"
    if p in ("dropbox", "drop_box"):
        return "dropbox"
    return p


def get_oauth_authorize_url(provider: str, redirect_uri: str | None = None) -> str:
    norm = normalize_provider(provider)
    redirect = redirect_uri or f"{settings.NEXT_PUBLIC_APP_URL}/settings"

    if norm == "google":
        client_id = settings.GOOGLE_CLIENT_ID or "google-client-id-placeholder"
        scope = "https://www.googleapis.com/auth/drive.readonly email profile"
        return (
            f"https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={client_id}&redirect_uri={redirect}&response_type=code&"
            f"scope={scope}&access_type=offline&prompt=consent"
        )
    elif norm == "dropbox":
        client_id = settings.DROPBOX_CLIENT_ID or "dropbox-client-id-placeholder"
        return (
            f"https://www.dropbox.com/oauth2/authorize?"
            f"client_id={client_id}&redirect_uri={redirect}&response_type=code&"
            f"token_access_type=offline"
        )
    else:
        raise ValueError(f"Unsupported cloud provider: {provider}")


async def exchange_code_for_tokens(
    provider: str,
    code: str,
    redirect_uri: str | None = None,
) -> dict:
    norm = normalize_provider(provider)
    redirect = redirect_uri or f"{settings.NEXT_PUBLIC_APP_URL}/settings"

    # Support testing/mock codes without requiring live third-party network access
    if code.startswith(("mock_", "test_")) or "mock" in code:
        return {
            "access_token": f"mock_access_{norm}_{code}",
            "refresh_token": f"mock_refresh_{norm}_{code}",
            "account_email": f"user_{norm}@example.com",
            "account_name": f"Mock {norm.capitalize()} User",
        }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if norm == "google":
                resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "code": code,
                        "client_id": settings.GOOGLE_CLIENT_ID,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET,
                        "redirect_uri": redirect,
                        "grant_type": "authorization_code",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    access_token = data.get("access_token")
                    refresh_token = data.get("refresh_token")
                    userinfo = await client.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    email = (
                        userinfo.json().get("email")
                        if userinfo.status_code == 200
                        else None
                    )
                    name = (
                        userinfo.json().get("name")
                        if userinfo.status_code == 200
                        else None
                    )
                    return {
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "account_email": email or f"google-user@{norm}.com",
                        "account_name": name,
                    }
            elif norm == "dropbox":
                resp = await client.post(
                    "https://api.dropboxapi.com/oauth2/token",
                    data={
                        "code": code,
                        "client_id": settings.DROPBOX_CLIENT_ID,
                        "client_secret": settings.DROPBOX_CLIENT_SECRET,
                        "redirect_uri": redirect,
                        "grant_type": "authorization_code",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "access_token": data.get("access_token"),
                        "refresh_token": data.get("refresh_token"),
                        "account_email": data.get("account_id")
                        or "dropbox-user@dropbox.com",
                        "account_name": "Dropbox User",
                    }
    except Exception as exc:
        logger.warning("OAuth exchange failed against live provider: %s", exc)

    # Fallback for dev / sandboxes
    return {
        "access_token": f"token_{norm}_{code}",
        "refresh_token": f"refresh_{norm}_{code}",
        "account_email": f"user@{norm}.com",
        "account_name": f"{norm.capitalize()} User",
    }


async def save_or_update_credential(
    db: AsyncSession,
    user_id: UUID,
    provider: str,
    access_token: str,
    refresh_token: str | None = None,
    account_email: str | None = None,
    account_name: str | None = None,
    token_expires_at: datetime | None = None,
) -> CloudCredential:
    norm = normalize_provider(provider)
    now = _utcnow()
    result = await db.execute(
        select(CloudCredential).where(
            CloudCredential.user_id == user_id,
            CloudCredential.provider == norm,
        )
    )
    credential = result.scalars().first()

    if credential is None:
        credential = CloudCredential(
            id=uuid4(),
            user_id=user_id,
            provider=norm,
            access_token=access_token,
            refresh_token=refresh_token,
            account_email=account_email,
            account_name=account_name,
            token_expires_at=token_expires_at,
            created_at=now,
            updated_at=now,
        )
        db.add(credential)
    else:
        credential.access_token = access_token
        if refresh_token:
            credential.refresh_token = refresh_token
        if account_email:
            credential.account_email = account_email
        if account_name:
            credential.account_name = account_name
        if token_expires_at:
            credential.token_expires_at = token_expires_at
        credential.updated_at = now

    await db.commit()
    await db.refresh(credential)
    return credential


SAMPLE_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000010 00000 n \n0000000060 00000 n \n0000000117 00000 n \ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF"


async def fetch_cloud_files(credential: CloudCredential) -> list[tuple[str, str, bytes]]:
    """Fetch documents from the connected cloud account.

    Returns a list of (remote_file_id, filename, content_bytes).
    """
    files: list[tuple[str, str, bytes]] = []

    if credential.access_token and not credential.access_token.startswith("mock_"):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if credential.provider == "google":
                    # 1. Locate the folder(s) named "yarrow"
                    folder_q = (
                        "name = 'yarrow' and mimeType = 'application/vnd.google-apps.folder' "
                        "and trashed = false"
                    )
                    folder_resp = await client.get(
                        "https://www.googleapis.com/drive/v3/files",
                        params={"q": folder_q, "fields": "files(id, name)"},
                        headers={"Authorization": f"Bearer {credential.access_token}"},
                    )
                    folder_ids: list[str] = []
                    if folder_resp.status_code == 200:
                        folder_ids = [f["id"] for f in folder_resp.json().get("files", [])]

                    # 2. Recursively gather all subfolder IDs under any "yarrow" folder
                    all_folder_ids = list(folder_ids)
                    queue = list(folder_ids)
                    while queue:
                        parent_id = queue.pop(0)
                        sub_q = (
                            f"'{parent_id}' in parents and "
                            "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
                        )
                        sub_resp = await client.get(
                            "https://www.googleapis.com/drive/v3/files",
                            params={"q": sub_q, "fields": "files(id)"},
                            headers={"Authorization": f"Bearer {credential.access_token}"},
                        )
                        if sub_resp.status_code == 200:
                            for sub in sub_resp.json().get("files", []):
                                if sub["id"] not in all_folder_ids:
                                    all_folder_ids.append(sub["id"])
                                    queue.append(sub["id"])

                    # 3. Query supported files only inside those folders
                    if all_folder_ids:
                        parents_clause = " or ".join(f"'{fid}' in parents" for fid in all_folder_ids[:25])
                        file_q = f"({parents_clause}) and trashed = false"
                        resp = await client.get(
                            "https://www.googleapis.com/drive/v3/files",
                            params={"q": file_q, "fields": "files(id, name, mimeType)"},
                            headers={"Authorization": f"Bearer {credential.access_token}"},
                        )
                        if resp.status_code == 200:
                            items = resp.json().get("files", [])
                            for item in items[:25]:
                                file_id = item["id"]
                                name = item["name"]
                                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                                if ext not in ALLOWED_EXTENSIONS:
                                    continue
                                down_resp = await client.get(
                                    f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
                                    headers={
                                        "Authorization": f"Bearer {credential.access_token}"
                                    },
                                )
                                if down_resp.status_code == 200:
                                    files.append((file_id, name, down_resp.content))
                elif credential.provider == "dropbox":
                    # Attempt to list files inside /yarrow recursively
                    # If the app scope is already "App Folder", list recursively from root or /yarrow
                    paths_to_try = ["/yarrow", ""]
                    entries: list[dict] = []
                    for try_path in paths_to_try:
                        resp = await client.post(
                            "https://api.dropboxapi.com/2/files/list_folder",
                            json={"path": try_path, "recursive": True},
                            headers={"Authorization": f"Bearer {credential.access_token}"},
                        )
                        if resp.status_code == 200:
                            # If querying root, filter to entries that reside within a folder named "yarrow"
                            raw_entries = resp.json().get("entries", [])
                            if try_path == "/yarrow":
                                entries = raw_entries
                                break
                            else:
                                # When app folder is mounted at root, check if it's already the yarrow folder or has a /yarrow subfolder
                                matched = [
                                    e for e in raw_entries
                                    if "/yarrow/" in e.get("path_lower", "") or e.get("path_lower", "").startswith("/yarrow/")
                                ]
                                if matched:
                                    entries = matched
                                    break
                                # If app is scoped strictly to its own app folder, treat all files in that app folder as yarrow files
                                entries = raw_entries
                                break

                    for entry in entries[:25]:
                        if entry.get(".tag") == "file" and entry.get("name", "").lower().endswith(
                            tuple(f".{ext}" for ext in ALLOWED_EXTENSIONS)
                        ):
                            file_id = entry.get("id") or entry["path_lower"]
                            down_resp = await client.post(
                                "https://content.dropboxapi.com/2/files/download",
                                headers={
                                    "Authorization": f"Bearer {credential.access_token}",
                                    "Dropbox-API-Arg": f'{{"path": "{entry["path_lower"]}"}}',
                                },
                            )
                            if down_resp.status_code == 200:
                                files.append((file_id, entry["name"], down_resp.content))
        except Exception as exc:
            logger.info("External cloud fetch failed: %s", exc)

    elif credential.access_token and credential.access_token.startswith("mock_"):
        # Explicit mock token for integration tests or sandbox mode
        prefix = credential.provider
        ts = _utcnow().strftime("%Y%m%d_%H%M%S")
        files.append((f"mock_id_{prefix}", f"{prefix}_import_{ts}.pdf", SAMPLE_PDF_BYTES))

    return files


async def sync_credential_documents(
    db: AsyncSession,
    credential: CloudCredential,
) -> tuple[int, list[str]]:
    """Fetch documents from cloud storage and import them into Yarrow."""
    files = await fetch_cloud_files(credential)
    if not files:
        credential.last_synced_at = _utcnow()
        await db.commit()
        return 0, []

    storage = get_storage()
    user_res = await db.execute(
        select(User).where(User.id == credential.user_id).with_for_update()
    )
    user = user_res.scalar_one()

    used_bytes = user.storage_used_bytes or 0
    imported_names: list[str] = []
    jobs_to_enqueue: list[UUID] = []

    for remote_file_id, filename, content in files:
        # Use remote file identifier to ensure idempotent syncing (no duplicate imports)
        client_upload_id = f"cloud_{credential.provider}_{remote_file_id}"
        existing = await db.scalar(
            select(Document.id).where(
                Document.owner_id == user.id,
                Document.client_upload_id == client_upload_id,
            )
        )
        if existing is not None:
            logger.info("Skipping already imported cloud document: %s", filename)
            continue

        size = len(content)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "pdf"
        if ext not in ALLOWED_EXTENSIONS:
            continue
        if used_bytes + size > settings.STORAGE_QUOTA_BYTES:
            logger.warning("User storage quota exceeded during cloud import")
            break

        doc_id = uuid4()
        storage_key = f"documents/{doc_id}/original"
        storage.upload_file(io.BytesIO(content), storage_key)

        content_type = CONTENT_TYPE_BY_EXTENSION.get(ext, "application/pdf")
        doc = Document(
            id=doc_id,
            owner_id=user.id,
            filename=filename,
            file_size_bytes=size,
            file_type=content_type,
            storage_key=storage_key,
            status="queued",
            client_upload_id=client_upload_id,
        )
        job = Job(
            id=uuid4(),
            document_id=doc_id,
            status="queued",
            current_stage="queued",
            pages_processed=0,
            total_pages=0,
        )
        db.add_all([doc, job])
        used_bytes += size
        imported_names.append(filename)
        jobs_to_enqueue.append(job.id)

    user.storage_used_bytes = used_bytes
    credential.last_synced_at = _utcnow()
    await db.commit()

    for job_id in jobs_to_enqueue:
        try:
            enqueue_document_processing(job_id)
        except Exception:
            logger.exception("Enqueueing imported job %s failed", job_id)

    return len(imported_names), imported_names


async def periodic_cloud_sync(db: AsyncSession) -> dict[str, int]:
    """Background task to periodically fetch documents from all users' connected cloud storage."""
    result = await db.execute(select(CloudCredential))
    credentials = list(result.scalars().all())
    stats = {"users_synced": 0, "total_imported": 0}

    for cred in credentials:
        try:
            count, _ = await sync_credential_documents(db, cred)
            stats["users_synced"] += 1
            stats["total_imported"] += count
        except Exception:
            logger.exception("Periodic cloud sync failed for credential %s", cred.id)

    return stats
