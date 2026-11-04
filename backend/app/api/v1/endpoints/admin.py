import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import and_, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.locking import is_lock_timeout, lock_timeout_statement
from yarrow_db.models import Document, Job, ServiceNode, User

from app.core.database import get_db
from app.core.queue import enqueue_document_processing, revoke_document_processing
from app.core.security import get_current_active_admin
from app.schemas.admin import (
    AdminAccount,
    AdminAccountStatus,
    AdminAccountStatusUpdate,
    AdminJob,
    AdminJobActionResult,
    AdminJobCancelRequest,
    AdminStats,
    JobStatusCounts,
    NodeStatusReport,
)
from app.services.account import delete_account
from app.services.job_progress import cancel_open_job_pages
from app.services.node_health import check_nodes
from app.services.reprocess import (
    ACTIVE_JOB_STATES,
    LATEST_JOB_FIRST,
    ReprocessError,
    is_interrupted,
    reprocess_document,
)

router = APIRouter()
logger = logging.getLogger(__name__)

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
        .outerjoin(
            Document,
            and_(Document.owner_id == User.id, Document.deleted_at.is_(None)),
        )
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
        (
            await db.execute(
                select(func.count(Document.id)).where(Document.deleted_at.is_(None))
            )
        ).scalar_one()
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


def _job_time(value: datetime | None) -> datetime:
    return value or datetime.min


def _latest_key(job: Job) -> tuple[bool, datetime, UUID]:
    return (job.created_at is not None, _job_time(job.created_at), job.id)


CANCEL_REFUSALS = {
    "completed": "This job has already finished, so there is nothing left to cancel.",
    "failed": "This job has already failed, so there is nothing left to cancel.",
    "canceled": "This job has already been canceled.",
}
SUPERSEDED_CANCEL = (
    "A newer job has replaced this one, so it can no longer be canceled."
)
SUPERSEDED_RETRY = "A newer job has replaced this one, so it can no longer be retried."
NOT_FAILED_RETRY = "Only a failed job can be retried, and this one is {status}."


def _cancel_refusal(job: Job, latest: Job | None) -> str | None:
    if job.status not in ACTIVE_JOB_STATES:
        return CANCEL_REFUSALS.get(job.status, "This job can no longer be canceled.")
    if latest is None or latest.id != job.id:
        return SUPERSEDED_CANCEL
    return None


def _retry_refusal(job: Job, latest: Job | None) -> str | None:
    if job.status != "failed":
        return NOT_FAILED_RETRY.format(status=job.status)
    if latest is None or latest.id != job.id:
        return SUPERSEDED_RETRY
    return None


@router.get("/jobs", response_model=list[AdminJob])
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_active_admin),
):
    # jobs from every user, with queue place and why a job failed
    stmt = (
        select(Job, Document, User)
        .join(Document, Job.document_id == Document.id)
        .join(User, Document.owner_id == User.id)
        .where(Document.deleted_at.is_(None))
    )
    rows = [(row[0], row[1], row[2]) for row in (await db.execute(stmt)).all()]

    queued = sorted(
        [row for row in rows if row[0].status == "queued"],
        key=lambda row: (_job_time(row[0].created_at), row[0].id),
    )
    positions = {job.id: index + 1 for index, (job, _, _) in enumerate(queued)}

    processing = [row for row in rows if row[0].status == "processing"]
    rest = [row for row in rows if row[0].status not in ("processing", "queued")]
    processing.sort(key=lambda row: (_job_time(row[0].created_at), row[0].id))
    rest.sort(
        key=lambda row: (_job_time(row[0].updated_at), row[0].id),
        reverse=True,
    )

    latest: dict[UUID, Job] = {}
    for job, _, _ in rows:
        current = latest.get(job.document_id)
        if current is None or _latest_key(job) > _latest_key(current):
            latest[job.document_id] = job
    now = datetime.now(UTC).replace(tzinfo=None)

    out: list[AdminJob] = []
    for job, document, user in processing + queued + rest:
        document_latest = latest[job.document_id]
        out.append(
            AdminJob(
                id=job.id,
                document_id=document.id,
                filename=document.filename,
                owner_id=user.id,
                owner_email=user.email,
                owner_name=user.name,
                status=job.status,
                current_stage=job.current_stage,
                queue_position=positions.get(job.id),
                pages_processed=job.pages_processed,
                total_pages=job.total_pages,
                error_message=job.error_message,
                created_at=job.created_at,
                is_stuck=is_interrupted(
                    {"status": job.status, "updated_at": job.updated_at}, now
                ),
                can_cancel=_cancel_refusal(job, document_latest) is None,
                can_retry=_retry_refusal(job, document_latest) is None,
                canceled_by_id=job.canceled_by_id,
                cancel_reason=job.cancel_reason,
                retry_of_job_id=job.retry_of_job_id,
            )
        )
    return out


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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_active_admin),
):
    # wipe the account and its documents; disable is a separate action
    if account_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account.",
        )

    account = await db.get(User, account_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Account not found."
        )

    outcome = await delete_account(db, account_id)
    if not outcome.ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )


DEADLOCK_DETECTED = "40P01"


def _job_error(status_code: int, detail: str, code: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"detail": detail, "code": code}
    )


def _is_busy(exc: DBAPIError) -> bool:
    deadlock = DEADLOCK_DETECTED in (
        getattr(exc.orig, "sqlstate", None),
        getattr(exc.orig, "pgcode", None),
    )
    return is_lock_timeout(exc) or deadlock


async def _lock_job_for_action(
    db: AsyncSession, job_id: UUID
) -> tuple[Document, Job, Job | None]:
    target = await db.get(Job, job_id)
    if target is None:
        raise _job_error(status.HTTP_404_NOT_FOUND, "Job not found", "JOB_NOT_FOUND")
    document_id = target.document_id
    try:
        await db.execute(lock_timeout_statement())
        document = (
            await db.execute(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        latest = (
            await db.execute(
                select(Job)
                .where(Job.document_id == document_id)
                .order_by(*LATEST_JOB_FIRST)
                .limit(1)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    except DBAPIError as exc:
        await db.rollback()
        if _is_busy(exc):
            raise _job_error(
                status.HTTP_409_CONFLICT,
                "This document is being updated right now; try again.",
                "DOCUMENT_BUSY",
            ) from None
        raise
    if document is None or document.deleted_at is not None:
        raise _job_error(status.HTTP_404_NOT_FOUND, "Job not found", "JOB_NOT_FOUND")
    await db.refresh(target)
    return document, target, latest


@router.post("/jobs/{job_id}/cancel", response_model=AdminJobActionResult)
async def force_cancel_job(
    job_id: UUID,
    body: AdminJobCancelRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_active_admin),
):
    admin_id = admin.id
    document, job, latest = await _lock_job_for_action(db, job_id)
    refusal = _cancel_refusal(job, latest)
    if refusal is not None:
        raise _job_error(status.HTTP_409_CONFLICT, refusal, "JOB_NOT_CANCELABLE")

    now = datetime.now(UTC).replace(tzinfo=None)
    job.status = "canceled"
    job.current_stage = "canceled"
    job.canceled_by_id = admin_id
    job.cancel_reason = body.reason if body is not None else None
    job.updated_at = now
    await db.execute(cancel_open_job_pages([job.id], now))
    document.status = "canceled"
    document.error_message = None
    document_id = document.id
    task_id = job.celery_task_id
    await db.commit()

    if task_id:
        try:
            await run_in_threadpool(revoke_document_processing, task_id, terminate=True)
        except Exception:
            logger.exception(f"Force-revoking task {task_id} failed")

    return AdminJobActionResult(
        job_id=job_id, document_id=document_id, status="canceled"
    )


@router.post(
    "/jobs/{job_id}/retry",
    response_model=AdminJobActionResult,
    status_code=status.HTTP_201_CREATED,
)
async def retry_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_active_admin),
):
    admin_id = admin.id
    document, job, latest = await _lock_job_for_action(db, job_id)
    refusal = _retry_refusal(job, latest)
    if refusal is not None:
        raise _job_error(status.HTTP_409_CONFLICT, refusal, "JOB_NOT_RETRYABLE")

    document_id = document.id
    outcome = await reprocess_document(
        db,
        document_id,
        enqueue_document_processing,
        requested_by_id=admin_id,
        retry_of_job_id=job_id,
    )
    if outcome.error is ReprocessError.QUEUE_UNAVAILABLE:
        raise _job_error(
            status.HTTP_503_SERVICE_UNAVAILABLE, outcome.detail, "QUEUE_UNAVAILABLE"
        )
    if not outcome.ok:
        raise _job_error(
            status.HTTP_409_CONFLICT,
            "Only the latest failed job of a document can be retried.",
            "JOB_NOT_RETRYABLE",
        )

    return AdminJobActionResult(
        job_id=job_id,
        document_id=document_id,
        status="failed",
        new_job_id=outcome.job_id,
        new_job_status="queued",
        scope=outcome.scope,
        pages=outcome.pages,
    )


@router.get("/nodes", response_model=NodeStatusReport)
async def get_node_status(
    refresh: bool = False,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_active_admin),
):
    return await check_nodes(db, refresh=refresh)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_node(
    node_id: UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_active_admin),
):
    node = await db.get(ServiceNode, node_id)
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"detail": "Node not found", "code": "NODE_NOT_FOUND"},
        )
    if node.kind == "inference":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "Inference servers come from configuration and "
                "cannot be removed here",
                "code": "NODE_CONFIGURED",
            },
        )
    if node.status != "down":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "Only a node that is down can be removed",
                "code": "NODE_NOT_DOWN",
            },
        )
    await db.delete(node)
    await db.commit()
