from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Job, User

from app.core.database import get_db
from app.core.security import get_current_active_admin
from app.schemas.admin import (
    AdminAccount,
    AdminAccountStatus,
    AdminAccountStatusUpdate,
    AdminStats,
    JobStatusCounts,
)

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
            User.is_active,
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
            is_active=bool(row.is_active),
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
    user_count = int((await db.execute(select(func.count(User.id)))).scalar_one())
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
    for job_status, n in job_rows:
        if job_status in counts:
            counts[job_status] = int(n)
    return AdminStats(
        user_count=user_count,
        document_count=document_count,
        storage_used_bytes=storage_used_bytes,
        jobs_by_status=JobStatusCounts(**counts),
    )


@router.patch("/accounts/{account_id}/status", response_model=AdminAccountStatus)
async def update_account_status(
    account_id: UUID,
    update: AdminAccountStatusUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_active_admin),
):
    """Update the status of an admin account.

    An administrator cannot disable itself, but can disable other users (admins) and downgrade itself to a normal user.

    Args:
        account_id (UUID): The ID of the account to update.
        update (AdminAccountStatus): The new status for the account.
        db (AsyncSession, optional): The database session. Defaults to Depends(get_db).
        admin (User, optional): The currently authenticated admin user. Defaults to Depends(get_current_active_admin).

    Raises:
        HTTPException: If the admin tries to disable their own account.
        HTTPException: If the account to update is not found.

    Returns:
        AdminAccountStatus: The updated status of the account.
    """
    if update.is_active is None and update.is_admin is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one account status field must be provided.",
        )

    if account_id == admin.id and update.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot disable your own account.",
        )

    account = await db.get(User, account_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Account not found."
        )

    if update.is_active is not None:
        account.is_active = update.is_active
    if update.is_admin is not None:
        account.is_admin = update.is_admin

    await db.commit()
    return AdminAccountStatus(
        id=account.id,
        is_active=account.is_active,
        is_admin=account.is_admin,
    )
