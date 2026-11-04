"""Detecting that an upload batch has finished, and announcing it once (US-60).

Finished: the batch's upload run is sealed and every live (not trashed)
document in it is completed, failed or canceled. One failed file does not
keep the batch open; it is reported next to the ones that worked.

Sealed: the files of one run are uploaded one after another, so only the
browser knows when the last one has been sent. The Upload page seals the
batch (``POST /batches/{id}/seal``) when its loop ends; a file joining the
batch later (a Retry) unseals it until that loop ends too. A run whose tab
closed mid-upload is never sealed, so a batch also counts as sealed once no
file has joined it for ``BATCH_SEAL_TIMEOUT_SECONDS``.

Announcing is two-phase, so a lost reply or a tab closed at the wrong moment
does not lose the notification:

1. Claim: ``UPDATE ... SET claimed_at = now WHERE <finished> AND
   notified_at IS NULL AND (claimed_at IS NULL OR <lease expired>)
   RETURNING``. Postgres row locks make concurrent claims (two tabs, two
   devices) get each batch exactly once while the lease holds: the second
   update waits for the first, re-checks the lease condition and skips it.
2. Acknowledge: after showing it, the tab sets ``notified_at`` (only on a
   batch that is still claimed). From then on nobody is offered it again.
   An unacknowledged claim is offered again once its lease expires.

``notified_at`` is never cleared. If a document of an announced batch is
reprocessed, or a file is retried into it, ``completed_at`` goes back to None
while it runs and is set again when it ends, but no second notification is
sent: those are one-document actions, followed on that document's page.
"""

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Batch, Document

from app.core.config import settings
from app.schemas import BatchNotification, BatchNotifications

# Statuses a document never leaves on its own. Anything else -- including a
# value outside the vocabulary -- counts as still being worked on.
FINAL_STATUSES = ("completed", "failed", "canceled")


def _utcnow() -> datetime:
    # Naive UTC, like every other timestamp column (datetime.utcnow defaults).
    return datetime.now(UTC).replace(tzinfo=None)


def is_finished(counts: Mapping[str, int]) -> bool:
    """Whether every one of these documents (status -> number) is final."""
    total = sum(counts.values())
    done = sum(counts.get(state, 0) for state in FINAL_STATUSES)
    return total > 0 and done == total


def is_sealed(
    batch: Batch, newest_upload: datetime | None, now: datetime | None = None
) -> bool:
    """Whether the run has finished sending files: sealed by the browser, or
    no file has joined it for the seal timeout (its tab closed mid-upload)."""
    if batch.sealed_at is not None:
        return True
    if newest_upload is None:
        return False
    now = now or _utcnow()
    timeout = timedelta(seconds=settings.BATCH_SEAL_TIMEOUT_SECONDS)
    return newest_upload <= now - timeout


async def seal_batch(db: AsyncSession, batch: Batch) -> None:
    """Mark the run as done sending files. Idempotent. Commits."""
    if batch.sealed_at is None:
        batch.sealed_at = _utcnow()
        await db.commit()


async def sync_completed_at(db: AsyncSession, batch: Batch, finished: bool) -> None:
    """Record when the batch finished, or clear it if it is running again.

    Only writes when the value changes. Commits.
    """
    if finished and batch.completed_at is None:
        batch.completed_at = _utcnow()
    elif not finished and batch.completed_at is not None:
        # A document went back to the queue (reprocess); not finished now.
        batch.completed_at = None
    else:
        return
    await db.commit()


async def claim_batch_notifications(
    db: AsyncSession, owner_id: UUID
) -> BatchNotifications:
    """Lease and return this user's finished, not yet announced batches.

    Other tabs skip a leased batch until the lease expires; the caller is
    expected to acknowledge each one after showing it. ``pending`` counts the
    user's unannounced batches that still have live files -- still running,
    not sealed yet, or leased but not acknowledged -- so the browser knows
    whether to keep checking. Commits.
    """
    now = _utcnow()
    lease_cutoff = now - timedelta(seconds=settings.BATCH_NOTIFICATION_LEASE_SECONDS)
    seal_cutoff = now - timedelta(seconds=settings.BATCH_SEAL_TIMEOUT_SECONDS)
    live = Document.deleted_at.is_(None)
    claimable = (
        Batch.owner_id == owner_id,
        Batch.notified_at.is_(None),
        or_(Batch.claimed_at.is_(None), Batch.claimed_at <= lease_cutoff),
    )

    active = func.count().filter(Document.status.notin_(FINAL_STATUSES))
    finished = (
        select(Batch.id)
        .join(Document, Document.batch_id == Batch.id)
        .where(*claimable, live)
        .group_by(Batch.id)
        .having(active == 0)
        .having(
            or_(
                func.bool_or(Batch.sealed_at.is_not(None)),
                func.max(Document.created_at) <= seal_cutoff,
            )
        )
    )

    claimed = (
        await db.execute(
            update(Batch)
            # The lease condition is re-checked after waiting on another
            # tab's claim of the same row, so only one of them gets it.
            .where(Batch.id.in_(finished), *claimable)
            .values(
                claimed_at=now,
                completed_at=func.coalesce(Batch.completed_at, now),
            )
            .returning(Batch.id, Batch.created_at, Batch.completed_at)
            .execution_options(synchronize_session="fetch")
        )
    ).all()

    counts: dict[UUID, dict[str, int]] = {
        row.id: {state: 0 for state in FINAL_STATUSES} for row in claimed
    }
    if counts:
        rows = await db.execute(
            select(Document.batch_id, Document.status, func.count())
            .where(Document.batch_id.in_(list(counts)), live)
            .group_by(Document.batch_id, Document.status)
        )
        for batch_id, state, count in rows:
            counts[batch_id][state] = counts[batch_id].get(state, 0) + count

    pending = await db.scalar(
        select(func.count(func.distinct(Document.batch_id)))
        .join(Batch, Batch.id == Document.batch_id)
        .where(Batch.owner_id == owner_id, Batch.notified_at.is_(None), live)
    )
    await db.commit()

    notifications = [
        BatchNotification(
            batch_id=row.id,
            total=sum(counts[row.id].values()),
            completed=counts[row.id]["completed"],
            failed=counts[row.id]["failed"],
            canceled=counts[row.id]["canceled"],
            created_at=row.created_at,
            completed_at=row.completed_at,
        )
        for row in sorted(claimed, key=lambda row: row.created_at or now)
    ]
    return BatchNotifications(notifications=notifications, pending=pending or 0)


async def acknowledge_batch_notifications(
    db: AsyncSession, owner_id: UUID, batch_ids: Iterable[UUID]
) -> list[UUID]:
    """Mark these claimed batches as announced, for good. Commits.

    Only the caller's batches that are currently claimed and not yet
    acknowledged change; anything else (someone else's id, an unknown id, a
    batch whose claim was dropped because a file joined it) is ignored.
    Returns the ids that were acknowledged by this call.
    """
    ids = list(dict.fromkeys(batch_ids))
    if not ids:
        return []
    result = await db.execute(
        update(Batch)
        .where(
            Batch.id.in_(ids),
            Batch.owner_id == owner_id,
            Batch.notified_at.is_(None),
            Batch.claimed_at.is_not(None),
        )
        .values(notified_at=_utcnow())
        .returning(Batch.id)
        .execution_options(synchronize_session="fetch")
    )
    acknowledged = list(result.scalars().all())
    await db.commit()
    return acknowledged
