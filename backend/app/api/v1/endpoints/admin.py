from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Job, User

from app.core.database import get_db
from app.core.security import get_current_active_admin
from app.schemas.admin import AdminAccount, AdminStats, JobStatusCounts

router = APIRouter()

JOB_STATUSES = ("queued", "processing", "completed", "failed", "canceled")


@router.get("/accounts", response_model=list[AdminAccount])
async def list_accounts(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_active_admin),
):
    # every account, with how many documents they own
    stmt = (
        select(
            User.id,
            User.name,
            User.email,
            User.created_at,
            User.is_admin,
            func.count(Document.id).label("document_count"),
        )
        .outerjoin(Document, Document.owner_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        AdminAccount(
            id=row.id,
            name=row.name,
            email=row.email,
            created_at=row.created_at,
            is_admin=bool(row.is_admin),
            document_count=row.document_count,
        )
        for row in rows
    ]


@router.get("/stats", response_model=AdminStats)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_active_admin),
):
    # totals across the whole system, not one user's library
    user_count = int(
        (await db.execute(select(func.count(User.id)))).scalar_one()
    )
    document_count = int(
        (await db.execute(select(func.count(Document.id)))).scalar_one()
    )
    storage_used_bytes = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(Document.file_size_bytes), 0))
            )
        ).scalar_one()
        or 0
    )
    counts = dict.fromkeys(JOB_STATUSES, 0)
    job_rows = (
        await db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status))
    ).all()
    for status, n in job_rows:
        if status in counts:
            counts[status] = int(n)
    return AdminStats(
        user_count=user_count,
        document_count=document_count,
        storage_used_bytes=storage_used_bytes,
        jobs_by_status=JobStatusCounts(**counts),
    )
