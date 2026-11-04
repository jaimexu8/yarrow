from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, UploadSession, User

from app.core.config import settings
from app.schemas.auth import StorageLevel, StorageUsage
from app.services.upload_sessions import utcnow


async def _reserved_bytes(db: AsyncSession, owner_id: UUID) -> int:
    total = await db.scalar(
        select(func.coalesce(func.sum(UploadSession.total_size_bytes), 0)).where(
            UploadSession.owner_id == owner_id,
            UploadSession.status == "processing",
            UploadSession.expires_at > utcnow(),
        )
    )
    return int(total or 0)


async def get_storage_usage(db: AsyncSession, user: User) -> StorageUsage:
    used_bytes = int(user.storage_used_bytes or 0)
    reserved = await _reserved_bytes(db, user.id)
    trash_total = await db.scalar(
        select(func.coalesce(func.sum(Document.file_size_bytes), 0)).where(
            Document.owner_id == user.id,
            Document.deleted_at.isnot(None),
        )
    )
    trash_bytes = int(trash_total or 0)
    quota_bytes = settings.STORAGE_QUOTA_BYTES
    remaining_bytes = max(quota_bytes - used_bytes - reserved, 0)
    used_ratio = (used_bytes + reserved) / quota_bytes if quota_bytes > 0 else 1.0

    if used_ratio >= 1.0:
        level: StorageLevel = "full"
    elif used_ratio >= settings.STORAGE_CRITICAL_RATIO:
        level = "critical"
    elif used_ratio >= settings.STORAGE_WARNING_RATIO:
        level = "warning"
    else:
        level = "ok"

    return StorageUsage(
        used_bytes=used_bytes,
        reserved_bytes=reserved,
        trash_bytes=trash_bytes,
        quota_bytes=quota_bytes,
        remaining_bytes=remaining_bytes,
        used_ratio=used_ratio,
        level=level,
        warning_threshold=settings.STORAGE_WARNING_RATIO,
        critical_threshold=settings.STORAGE_CRITICAL_RATIO,
    )
