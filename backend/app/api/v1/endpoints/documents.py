import hashlib
import logging
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID, uuid4

import filetype
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.locking import is_lock_timeout, lock_timeout_statement
from yarrow_db.models import (
    Batch,
    Document,
    DocumentShare,
    DocumentTag,
    Folder,
    Job,
    Page,
    Region,
    Tag,
    User,
)
from yarrow_db.models.region import RegionImage
from yarrow_storage import ObjectNotFoundError, get_storage, thumbnail_key

from app.core.config import settings
from app.core.database import get_db
from app.core.queue import (
    enqueue_document_processing,
    job_priority,
    revoke_document_processing,
)
from app.core.security import get_current_user
from app.deps import (
    DocumentAccess,
    get_document_access,
    require_edit_access,
    require_owner_access,
    require_read_access,
)
from app.schemas import (
    BatchAcknowledge,
    BatchAcknowledged,
    BatchNotifications,
    BatchProgress,
    BatchSealed,
    BulkDocumentRequest,
    BulkItemResult,
    DocumentDetail,
    DocumentFolderUpdate,
    DocumentOut,
    DocumentProgress,
    DocumentRename,
    DuplicateOf,
    JobOut,
    ModelUsage,
    PageOut,
    UploadAccepted,
    UploadRejected,
    UploadResponse,
)
from app.services.batch_notifications import (
    acknowledge_batch_notifications,
    claim_batch_notifications,
    is_finished,
    is_sealed,
    seal_batch,
    sync_completed_at,
)
from app.services.document_deletion import (
    DocumentDeletionError,
    DocumentRestoreError,
    delete_stored_objects,
    empty_user_trash,
    permanently_delete_trashed,
    purge_expired_documents,
    restore_document,
    soft_delete_document,
)
from app.services.export import content_disposition, display_stem, safe_stem
from app.services.failure_messages import (
    DOCUMENT_FAILED_MESSAGE,
    QUEUE_FAILED_MESSAGE,
    public_error_message,
)
from app.services.job_progress import cancel_open_job_pages, document_progress
from app.services.reprocess import (
    LATEST_JOB_FIRST,
    ReprocessError,
    documents_out,
    reprocess_document,
)
from app.services.upload_sessions import purge_expired_upload_sessions

router = APIRouter()
logger = logging.getLogger(__name__)

# Jobs a user can still cancel (US-42): waiting in the queue, or being processed.
CANCELABLE_JOB_STATES = {"queued", "processing"}
# Every Document.status value, in the order a batch summary reports them.
STATUS_VALUES = ("queued", "processing", "completed", "failed", "canceled")
# Postgres deadlock_detected
DEADLOCK_DETECTED = "40P01"

# Sniffed from the magic bytes, never from the filename or the browser's
# Content-Type. These are exactly the types worker DocumentParser has a loader
# for -- accepting anything else queues a job that can only fail.
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif"}
# Shown to users when a file is rejected (US-37). Keep in sync with the list
# above and with the frontend's ACCEPTED_TYPES in src/lib/uploads.ts.
ACCEPTED_TYPES_TEXT = "PDF, PNG, JPEG and GIF"
MAX_UPLOAD_ID_LENGTH = 64
CONTENT_TYPE_BY_EXTENSION = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
}

# Enough to identify every type above; filetype only reads a header.
SNIFF_BYTES = 8192
READ_CHUNK = 1024 * 1024


async def _earlier_upload(
    db: AsyncSession, owner_id: UUID, upload_id: str
) -> Document | None:
    """This user's earlier upload with the same client id, if any."""
    result = await db.execute(
        select(Document).where(
            Document.owner_id == owner_id,
            Document.client_upload_id == upload_id,
            Document.deleted_at.is_(None),
        )
    )
    return result.scalars().first()


async def _identical_document(
    db: AsyncSession, owner_id: UUID, content_sha256: str
) -> Document | None:
    """This user's earliest document with exactly these bytes, if any (US-79).

    Only the caller's own documents are searched, so another user's identical
    file is never found, let alone named. Trashed documents do not count:
    they are not in the library, and the user threw them away on purpose.
    Documents from before the fingerprint existed have NULL and never match.
    """
    result = await db.execute(
        select(Document)
        .where(
            Document.owner_id == owner_id,
            Document.content_sha256 == content_sha256,
            Document.deleted_at.is_(None),
        )
        .order_by(Document.created_at, Document.id)
        .limit(1)
    )
    return result.scalars().first()


def _duplicate_rejection(filename: str, existing: Document) -> UploadRejected:
    return UploadRejected(
        filename=filename,
        reason=(
            "You already have an identical document in your library: "
            f"{existing.filename}."
        ),
        duplicate_of=DuplicateOf(
            document_id=existing.id,
            filename=existing.filename,
            status=existing.status,
        ),
    )


async def _accepted_for(db: AsyncSession, document: Document) -> UploadAccepted:
    """The upload result of a document that was stored earlier."""
    job = (
        (
            await db.execute(
                select(Job)
                .where(Job.document_id == document.id)
                .order_by(Job.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    return UploadAccepted(
        document_id=document.id,
        job_id=job.id if job else document.id,
        task_id=(job.celery_task_id if job else None) or "",
        filename=document.filename,
        file_size_bytes=document.file_size_bytes,
        status=document.status or "queued",
        message=public_error_message(
            document.error_message, document.status, DOCUMENT_FAILED_MESSAGE
        ),
    )


async def _revoke_tasks(task_ids: list[str]) -> None:
    for task_id in task_ids:
        try:
            await run_in_threadpool(revoke_document_processing, task_id)
        except Exception:
            logger.exception(f"Revoking task {task_id} failed")


async def _delete_storage(keys: list[str], context: str) -> None:
    if not keys:
        return
    orphaned = await run_in_threadpool(delete_stored_objects, get_storage(), keys)
    if orphaned:
        logger.error(
            f"{context} left {len(orphaned)} stored object(s) behind: {orphaned}"
        )


async def _cleanup_expired_trash(db: AsyncSession) -> None:
    """Wipe trash older than 7 days, then drop stored files and queued tasks."""
    outcome = await purge_expired_documents(db)
    await _revoke_tasks(outcome.task_ids)
    await _delete_storage(outcome.storage_keys, "Purging expired trash")


def _megabytes(size: int) -> str:
    return f"{size // (1024 * 1024)} MB"


def _storage_key(document_id: UUID) -> str:
    """Keyed by document id, never by filename.

    A user-supplied name in a key means path traversal, unicode normalization
    and key-length problems; the display name lives in Document.filename.
    """
    return f"documents/{document_id}/original"


async def _measure(upload: UploadFile) -> tuple[int, str, str | None]:
    """Return (size, sha256, sniffed extension), leaving the file rewound.

    Read in chunks rather than with .read(): UploadFile spools large bodies to
    disk, and pulling a 500 MB scan fully into memory to hash it would undo
    that.
    """
    digest = hashlib.sha256()
    size = 0
    header = b""

    await upload.seek(0)
    while chunk := await upload.read(READ_CHUNK):
        if not header:
            header = chunk[:SNIFF_BYTES]
        digest.update(chunk)
        size += len(chunk)
    await upload.seek(0)

    kind = filetype.guess(header)
    return size, digest.hexdigest(), kind.extension if kind else None


async def _get_or_create_batch(
    db: AsyncSession, owner_id: UUID, batch_id: UUID
) -> Batch:
    """The caller's batch with this id, created on first sight (US-31).

    Files are uploaded one request at a time, all carrying the same id, so
    the first request creates the row and the rest find it. The insert
    ignores a conflict rather than checking first, so two requests arriving
    together (even from different users guessing the same id) cannot both
    insert and one of them fail. Whoever lost the race, or anyone sending
    the id of someone else's batch, gets a 404 and never joins it.
    """
    await db.execute(
        pg_insert(Batch)
        .values(id=batch_id, owner_id=owner_id)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    batch = await db.get(Batch, batch_id, populate_existing=True)
    if batch is None or batch.owner_id != owner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found"
        )
    # Every caller is about to add a document to the batch (US-60): it is
    # not finished, the run is sending again until the browser re-seals it,
    # and a claimed but unacknowledged notification had stale counts.
    # notified_at is left alone: an announced batch is not announced again.
    batch.completed_at = None
    batch.sealed_at = None
    batch.claimed_at = None
    return batch


@router.post("/upload", response_model=UploadResponse)
async def upload_documents(
    files: list[UploadFile] = File(...),
    client_upload_ids: list[str] | None = Form(None),
    batch_id: UUID | None = Form(None),
    allow_duplicate: bool = Form(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Accept one or more files, store them, and queue a job for each.

    Bulk by design (backlog #8). One unusable file does not reject the batch:
    each is reported in `accepted` or `rejected` so the caller can show a
    per-file result.

    ``client_upload_ids`` (optional, one per file, in the same order) makes
    retries safe: a file whose id this user has already uploaded returns the
    existing document instead of being stored a second time.

    ``batch_id`` (optional) groups every file of one upload run so their
    progress can be reported together (US-31). Jobs stay one per file.

    A file with exactly the same bytes as one of the caller's own documents
    is refused with ``duplicate_of`` naming that document, and nothing is
    stored (US-79). Sending it again with ``allow_duplicate`` (applies to
    every file of the request; the Upload page sends one file per request)
    stores a separate copy, subject to the usual size and quota checks.
    """
    if client_upload_ids is not None:
        if len(client_upload_ids) != len(files):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Send exactly one client_upload_ids value per file.",
            )
        if len(set(client_upload_ids)) != len(client_upload_ids) or any(
            not 1 <= len(upload_id) <= MAX_UPLOAD_ID_LENGTH
            for upload_id in client_upload_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="client_upload_ids must be unique, non-empty and short.",
            )

    storage = get_storage()
    accepted: list[UploadAccepted] = []
    rejected: list[UploadRejected] = []
    jobs_by_id: dict[UUID, Job] = {}
    documents_by_job: dict[UUID, Document] = {}

    # Re-read the user with the row locked until this request commits, so two
    # uploads at the same moment take turns. Otherwise each could pass the
    # quota check on the same starting total and together exceed it.
    locked = await db.execute(
        select(User)
        .where(User.id == current_user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current_user = locked.scalar_one()
    # Created on the first file this request keeps, so that a request whose
    # files are all rejected leaves no empty batch behind.
    batch: Batch | None = None
    # Running total, so that several files in one request cannot each pass a
    # quota check that they only collectively exceed.
    used_bytes = current_user.storage_used_bytes or 0
    # Files kept earlier in this same request, by fingerprint, so two
    # identical files sent together are caught as well (US-79).
    kept_by_sha256: dict[str, Document] = {}

    for index, upload in enumerate(files):
        filename = upload.filename or "unnamed"
        upload_id = client_upload_ids[index] if client_upload_ids else None

        if upload_id:
            earlier = await _earlier_upload(db, current_user.id, upload_id)
            if earlier is not None:
                # A retry of an upload that already succeeded (its response
                # was lost): report the stored document, store nothing new.
                if batch_id is not None and earlier.batch_id is None:
                    # The first attempt was stored before its batch was known
                    # (or without one); count it in the run being retried.
                    if batch is None:
                        batch = await _get_or_create_batch(
                            db, current_user.id, batch_id
                        )
                    earlier.batch_id = batch.id
                accepted.append(await _accepted_for(db, earlier))
                continue

        size, content_sha256, extension = await _measure(upload)

        if size == 0:
            rejected.append(UploadRejected(filename=filename, reason="File is empty"))
            continue
        if extension not in ALLOWED_EXTENSIONS:
            # Decided from the file's contents, not its name, so a renamed
            # file is rejected too (US-37).
            rejected.append(
                UploadRejected(
                    filename=filename,
                    reason=(
                        "Unsupported file type. Yarrow accepts "
                        f"{ACCEPTED_TYPES_TEXT} files."
                    ),
                )
            )
            continue
        if size > settings.MAX_UPLOAD_BYTES:
            rejected.append(
                UploadRejected(
                    filename=filename,
                    reason=(
                        "File is too large. The limit is "
                        f"{_megabytes(settings.MAX_UPLOAD_BYTES)} per file."
                    ),
                )
            )
            continue
        if not allow_duplicate:
            # After the checks on the file itself (a file that could never be
            # stored is not worth a duplicate warning) and before quota: when
            # the user is near their limit, "you already have this" is the
            # more useful answer, and Skip costs nothing. The user row lock
            # above means a concurrent upload of the same bytes has either
            # committed (and is found here) or not started its checks yet.
            existing = kept_by_sha256.get(content_sha256) or await _identical_document(
                db, current_user.id, content_sha256
            )
            if existing is not None:
                rejected.append(_duplicate_rejection(filename, existing))
                continue
        if used_bytes + size > settings.STORAGE_QUOTA_BYTES:
            rejected.append(
                UploadRejected(
                    filename=filename,
                    reason="Not enough storage left in your account for this file.",
                )
            )
            continue

        if batch_id is not None and batch is None:
            # Refuse someone else's batch before storing anything. The batch
            # itself is only created once this file is safely stored, so a
            # storage failure leaves no empty batch behind.
            existing = await db.get(Batch, batch_id)
            if existing is not None and existing.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found"
                )
        document_id = uuid4()
        key = _storage_key(document_id)
        try:
            # Object first: an object with no row is a cheap orphan for the
            # sweeper, whereas a row with no object breaks the job.
            storage.upload_file(upload.file, key)
        except Exception:
            # The storage error can name buckets, endpoints or access keys, so
            # it goes to the server log only; the user gets a plain reason.
            logger.exception(f"Storing {filename} failed")
            rejected.append(
                UploadRejected(
                    filename=filename,
                    reason="Could not be stored right now. Please try again.",
                    retryable=True,
                )
            )
            continue

        if batch_id is not None and batch is None:
            batch = await _get_or_create_batch(db, current_user.id, batch_id)
        document = Document(
            id=document_id,
            owner_id=current_user.id,
            filename=filename,
            file_size_bytes=size,
            file_type=CONTENT_TYPE_BY_EXTENSION[extension],
            storage_key=key,
            status="queued",
            client_upload_id=upload_id,
            batch_id=batch.id if batch else None,
            content_sha256=content_sha256,
        )
        job = Job(
            id=uuid4(),
            document_id=document_id,
            status="queued",
            current_stage="queued",
            pages_processed=0,
            total_pages=0,
            requested_by_id=current_user.id,
        )
        db.add_all([document, job])
        kept_by_sha256.setdefault(content_sha256, document)
        jobs_by_id[job.id] = job
        documents_by_job[job.id] = document
        used_bytes += size
        accepted.append(
            UploadAccepted(
                document_id=document_id,
                job_id=job.id,
                task_id="",  # filled in after the commit below
                filename=filename,
                file_size_bytes=size,
            )
        )

    if accepted:
        current_user.storage_used_bytes = used_bytes
    # Committed before anything is queued: a worker can pick the message up in
    # milliseconds, and a task that starts before its rows are visible looks up
    # a job that does not exist.
    await db.commit()

    for item in accepted:
        job = jobs_by_id.get(item.job_id)
        if job is None:
            # Re-reported from an earlier upload; it was queued back then.
            continue
        try:
            item.task_id = enqueue_document_processing(
                item.job_id, priority=job_priority(item.file_size_bytes)
            )
        except Exception:
            # The file is already stored and counted, so the upload itself
            # succeeded; only starting processing failed. Record that on the
            # document rather than failing the request: an error here would
            # invite the client to upload again and create a duplicate.
            logger.exception(f"Queueing job {item.job_id} failed")
            job.status = "failed"
            job.error_message = QUEUE_FAILED_MESSAGE
            document = documents_by_job[item.job_id]
            document.status = "failed"
            document.error_message = QUEUE_FAILED_MESSAGE
            item.status = "failed"
            item.message = QUEUE_FAILED_MESSAGE
            continue
        # Persisted so that a queued job can be revoked later (US-42).
        job.celery_task_id = item.task_id
    if accepted:
        await db.commit()

    if not accepted and rejected:
        # Nothing was stored, so the request as a whole did not succeed.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            # mode="json": duplicate_of carries a UUID, which the plain
            # JSONResponse behind HTTPException cannot serialise.
            detail=[item.model_dump(mode="json") for item in rejected],
        )
    return UploadResponse(accepted=accepted, rejected=rejected)


@router.get("/", response_model=list[DocumentOut])
async def list_documents(
    folder_id: str | None = Query(
        default=None,
        description=(
            "Filter owned documents by folder. Pass a folder UUID, or the "
            "string 'null' for unfiled documents. Omit to list all documents "
            "the caller can see, including shares."
        ),
    ),
    tag_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _cleanup_expired_trash(db)
    # Abandoned resumable uploads (US-80) are swept on the same occasion.
    await purge_expired_upload_sessions(db, get_storage)
    if tag_id is not None:
        query = (
            select(Document)
            .join(DocumentTag, DocumentTag.document_id == Document.id)
            .join(Tag, Tag.id == DocumentTag.tag_id)
            .where(
                Document.owner_id == current_user.id,
                Document.deleted_at.is_(None),
                DocumentTag.tag_id == tag_id,
                Tag.owner_id == current_user.id,
            )
        )
        if folder_id and folder_id.lower() == "null":
            query = query.where(Document.folder_id.is_(None))
        elif folder_id:
            try:
                folder_uuid = UUID(folder_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="folder_id must be a UUID or the string 'null'",
                ) from None
            folder = await db.get(Folder, folder_uuid)
            if folder is None or folder.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Folder not found",
                )
            query = query.where(Document.folder_id == folder_uuid)
        result = await db.execute(query.order_by(Document.created_at.desc()))
        return await documents_out(db, list(result.scalars().all()))

    if folder_id is None or folder_id == "":
        shared_subquery = select(DocumentShare.document_id).where(
            DocumentShare.shared_with_user_id == current_user.id
        )
        result = await db.execute(
            select(Document)
            .where(
                Document.deleted_at.is_(None),
                or_(
                    Document.owner_id == current_user.id,
                    Document.id.in_(shared_subquery),
                ),
            )
            .order_by(Document.created_at.desc())
        )
        return await documents_out(db, list(result.scalars().all()))

    # folder filters are for the owner's docs only; shares stay in the
    # unfiltered list.
    query = select(Document).where(
        Document.owner_id == current_user.id,
        Document.deleted_at.is_(None),
    )
    if folder_id.lower() == "null":
        query = query.where(Document.folder_id.is_(None))
    else:
        try:
            folder_uuid = UUID(folder_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="folder_id must be a UUID or the string 'null'",
            ) from None
        folder = await db.get(Folder, folder_uuid)
        if folder is None or folder.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found",
            )
        query = query.where(Document.folder_id == folder_uuid)

    result = await db.execute(query.order_by(Document.created_at.desc()))
    return await documents_out(db, list(result.scalars().all()))


@router.get("/trash", response_model=list[DocumentOut])
async def list_trashed_documents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The current user's trash: deleted_at set and not yet past 7 days."""
    await _cleanup_expired_trash(db)
    result = await db.execute(
        select(Document)
        .where(
            Document.owner_id == current_user.id,
            Document.deleted_at.is_not(None),
        )
        .order_by(Document.deleted_at.desc())
    )
    return await documents_out(db, list(result.scalars().all()))


@router.delete("/trash", status_code=status.HTTP_204_NO_CONTENT)
async def empty_trash_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently delete every document in this user's trash."""
    outcome = await empty_user_trash(db, current_user.id)
    await _revoke_tasks(outcome.task_ids)
    await _delete_storage(outcome.storage_keys, "Emptying trash")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _owned_batch(db: AsyncSession, batch_id: UUID, owner_id: UUID) -> Batch:
    """The caller's batch; anyone else's, or an unknown id, is a 404."""
    batch = await db.get(Batch, batch_id)
    if batch is None or batch.owner_id != owner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found"
        )
    return batch


@router.get("/batches/{batch_id}", response_model=BatchProgress)
async def get_batch_progress(
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-status counts for the files uploaded together in one run (US-31).

    Counted from the documents each time it is asked, so leaving and coming
    back to the Upload page shows where things really are. Trashed documents
    are left out. Another user's batch is a 404, like another user's document.
    """
    batch = await _owned_batch(db, batch_id, current_user.id)

    rows = (
        await db.execute(
            select(Document.status, func.count(), func.max(Document.created_at))
            # A file moved to the trash has left the run as far as the user
            # can see, so it no longer counts toward the batch.
            .where(Document.batch_id == batch_id, Document.deleted_at.is_(None))
            .group_by(Document.status)
        )
    ).all()
    counts = {state: 0 for state in STATUS_VALUES}
    newest = max((row[2] for row in rows if row[2] is not None), default=None)
    for state, count, _newest in rows:
        if state not in counts:
            # Still in the batch, so still in the total. Reported as queued
            # for the user, and logged so a bad or new status value is seen.
            logger.warning(
                f"Batch {batch_id}: {count} document(s) have status {state!r}"
            )
            state = "queued"
        counts[state] += count
    # Detected on read too (US-60), so completed_at is right whoever asks.
    finished = is_finished(counts) and is_sealed(batch, newest)
    await sync_completed_at(db, batch, finished)
    return BatchProgress(
        batch_id=batch.id,
        total=sum(counts.values()),
        created_at=batch.created_at,
        completed_at=batch.completed_at,
        **counts,
    )


@router.post("/batches/{batch_id}/seal", response_model=BatchSealed)
async def seal_upload_batch(
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The browser has sent every file of this run (US-60).

    Only a sealed batch can be announced as finished, so a run whose first
    files finished before the last was sent is not reported early. Sealing
    again is harmless. A file that joins later (a Retry) unseals the batch
    until the browser seals it again.
    """
    batch = await _owned_batch(db, batch_id, current_user.id)
    await seal_batch(db, batch)
    return BatchSealed(batch_id=batch.id, sealed_at=batch.sealed_at)


@router.post("/batches/notifications/claim", response_model=BatchNotifications)
async def claim_batch_completion_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The caller's upload batches that finished and were not announced (US-60).

    Each returned batch is leased to this request for
    ``BATCH_NOTIFICATION_LEASE_SECONDS``, atomically, so another tab checking
    at the same time is not offered it. Acknowledge it after showing it; if
    that never happens, it is offered again when the lease runs out. Only
    the caller's own batches are considered.
    """
    return await claim_batch_notifications(db, current_user.id)


@router.post("/batches/notifications/ack", response_model=BatchAcknowledged)
async def acknowledge_batch_completion_notifications(
    body: BatchAcknowledge,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The browser has shown these notifications: never offer them again (US-60).

    Ids that are not the caller's, unknown, or not currently claimed are
    ignored rather than reported, so the answer reveals nothing about other
    users' batches.
    """
    acknowledged = await acknowledge_batch_notifications(
        db, current_user.id, body.batch_ids
    )
    return BatchAcknowledged(acknowledged=acknowledged)


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Gets document metadata with its jobs and pages"""

    document = access.document
    document_id = document.id

    jobs = await db.execute(
        select(Job).where(Job.document_id == document_id).order_by(Job.created_at)
    )
    pages = await db.execute(
        select(Page).where(Page.document_id == document_id).order_by(Page.page_number)
    )
    page_rows = list(pages.scalars())
    usage = Counter(
        (page.model_name, page.model_version)
        for page in page_rows
        if page.status == "completed"
    )
    base = (await documents_out(db, [document]))[0]
    # Validated one by one so each gets the user-facing error filter (US-11).
    return DocumentDetail(
        **base.model_dump(),
        jobs=[JobOut.model_validate(job) for job in jobs.scalars()],
        pages=[PageOut.model_validate(page) for page in page_rows],
        models=[
            ModelUsage(model_name=name, model_version=version, page_count=count)
            for (name, version), count in usage.most_common()
        ],
    )


@router.get("/{document_id}/progress", response_model=DocumentProgress)
async def get_document_progress(
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    return await document_progress(db, access.document)


@router.patch("/{document_id}", response_model=DocumentOut)
async def rename_document(
    body: DocumentRename,
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
):
    """Rename a document (US-39).

    Only the display name changes: the stored object is keyed by id, so the
    file itself is untouched. An invalid name is rejected with 422 by the
    schema before anything is written, so the old name stays.
    """
    document = access.document
    document.filename = body.filename
    await db.commit()
    await db.refresh(document)
    return (await documents_out(db, [document]))[0]


@router.patch("/{document_id}/folder", response_model=DocumentOut)
async def move_document_to_folder(
    body: DocumentFolderUpdate,
    access: DocumentAccess = Depends(get_document_access),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move an owned document into a folder, or unfile it with null.

    A folder that is missing or belongs to someone else is a 404, so a
    caller cannot tell other people's folders apart from missing ones.
    """
    if access.document.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can move this document",
        )
    document = access.document
    if body.folder_id is not None:
        folder = await db.get(Folder, body.folder_id)
        if folder is None or folder.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found",
            )
    document.folder_id = body.folder_id
    await db.commit()
    await db.refresh(document)
    return (await documents_out(db, [document]))[0]


@router.post("/{document_id}/reprocess", response_model=DocumentOut)
async def reprocess_document_endpoint(
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reprocess a document without uploading it again

    Interrupted, failed or partly failed documents reprocess only the pages
    that are not completed, so the task resumes and finished pages are kept.
    Fully processed (or canceled) documents are processed again in full.
    """
    document_id = access.document.id
    outcome = await reprocess_document(
        db,
        document_id,
        enqueue_document_processing,
        requested_by_id=current_user.id,
    )
    if not outcome.ok:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if outcome.error is ReprocessError.QUEUE_UNAVAILABLE
                else status.HTTP_409_CONFLICT
            ),
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )
    document = await db.get(Document, document_id, populate_existing=True)
    return (await documents_out(db, [document]))[0]


@router.post("/{document_id}/cancel", response_model=DocumentOut)
async def cancel_document_processing(
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a document's processing while it is queued or processing (US-42).

    A finished (completed, failed) or already canceled job is a 409 and nothing
    changes. Rows are locked document first, then job, the same order as the
    worker and document deletion. The worker re-checks the job under the
    document lock before saving results, so a cancel and a finishing worker
    cannot both win: either the results are dropped, or the cancel is refused.
    """
    document_id = access.document.id
    try:
        # Fail fast rather than hang while the worker is saving results.
        await db.execute(lock_timeout_statement())
        document = (
            await db.execute(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        job = (
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
        # A lock timeout, or a deadlock Postgres broke by aborting this
        # request: someone else is changing the document right now.
        deadlock = DEADLOCK_DETECTED in (
            getattr(exc.orig, "sqlstate", None),
            getattr(exc.orig, "pgcode", None),
        )
        if is_lock_timeout(exc) or deadlock:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This document is being updated right now; try again.",
            ) from None
        raise

    if job is None or job.status not in CANCELABLE_JOB_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only documents that haven't finished processing can be canceled.",
        )

    now = datetime.now(UTC).replace(tzinfo=None)
    job.status = "canceled"
    job.current_stage = "canceled"
    job.canceled_by_id = current_user.id
    job.updated_at = now
    await db.execute(cancel_open_job_pages([job.id], now))
    document.status = "canceled"
    document.error_message = None
    task_id = job.celery_task_id
    await db.commit()

    # After the commit, so a worker that gets the message anyway sees the
    # canceled row and skips it. A job that is already processing keeps
    # running until its next check, then discards its results.
    if task_id:
        try:
            # A blocking network call; kept off the event loop so a slow
            # broker cannot stall other requests.
            await run_in_threadpool(revoke_document_processing, task_id)
        except Exception:
            # The cancel already stands; the worker's check covers this.
            logger.exception(f"Revoking task {task_id} failed")

    document = await db.get(Document, document_id, populate_existing=True)
    return (await documents_out(db, [document]))[0]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document_endpoint(
    access: DocumentAccess = Depends(require_owner_access),
    db: AsyncSession = Depends(get_db),
):
    """Move a document to trash for 7 days.

    Extracted data and files stay so it can be restored. Unfinished jobs are
    canceled and their tasks revoked. If processing is saving results for this
    document at that moment, the request answers 409 after a short wait.
    """

    document_id = access.document.id
    outcome = await soft_delete_document(db, document_id)
    if not outcome.ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )

    await _revoke_tasks(outcome.task_ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{document_id}/restore", response_model=DocumentOut)
async def restore_document_endpoint(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Restore a trashed document if it is still within 7 days.

    Older than that is permanently deleted (410). Missing or not owned is 404.
    """
    outcome = await restore_document(db, document_id, current_user.id)
    if outcome.error is DocumentRestoreError.NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    if outcome.error is DocumentRestoreError.NOT_TRASHED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )
    if outcome.error is DocumentRestoreError.EXPIRED:
        await _revoke_tasks(outcome.task_ids)
        await _delete_storage(
            outcome.storage_keys, f"Purging expired document {document_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )
    if outcome.error is DocumentRestoreError.BUSY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )
    document = outcome.document
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    await _cleanup_expired_trash(db)
    return (await documents_out(db, [document]))[0]


@router.delete("/{document_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_document_endpoint(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently wipe a trashed document now, without waiting 7 days."""
    outcome = await permanently_delete_trashed(db, document_id, current_user.id)
    if outcome.error is DocumentDeletionError.NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    if not outcome.ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )

    await _revoke_tasks(outcome.task_ids)
    await _delete_storage(outcome.storage_keys, f"Deleted document {document_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/bulk-reprocess", response_model=list[BulkItemResult])
async def bulk_reprocess_documents(
    body: BulkDocumentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Reprocess several documents at once, reporting each on its own.

    The ids are processed in order, one document per request to the
    single-document reprocess service. A document whose job is still running is
    reported with ``ok: false`` and the rest are processed, so one busy document
    does not stall the batch.
    """
    user_id = current_user.id
    results: list[BulkItemResult] = []
    for document_id in body.document_ids:
        try:
            # Re-read the user each pass: the reprocess service commits per
            # document, which expires the object loaded by get_current_user.
            user = await db.get(User, user_id)
            # Raises 404 (not the caller's) or 403 (read-only share) for a
            # document the user cannot reprocess.
            await get_document_access(document_id, db=db, current_user=user)
            outcome = await reprocess_document(
                db, document_id, enqueue_document_processing, requested_by_id=user_id
            )
        except HTTPException as exc:
            # 404 (not the caller's) or 403 (read-only share): reported per item.
            message = (
                exc.detail
                if isinstance(exc.detail, str)
                else "This document could not be reprocessed."
            )
            results.append(
                BulkItemResult(document_id=document_id, ok=False, message=message)
            )
            continue
        if not outcome.ok:
            results.append(
                BulkItemResult(
                    document_id=document_id, ok=False, message=outcome.detail
                )
            )
        else:
            results.append(BulkItemResult(document_id=document_id, ok=True))
    return results


@router.post("/bulk-delete", response_model=list[BulkItemResult])
async def bulk_delete_documents(
    body: BulkDocumentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Move several documents to trash at once, reporting each on its own.

    Each id goes through the same single-document soft delete. A document that
    a worker is saving results for at that moment is reported with ``ok:
    false`` (code ``DOCUMENT_BUSY``) so it can be retried without blocking the
    rest of the batch.
    """
    user_id = current_user.id
    results: list[BulkItemResult] = []
    for document_id in body.document_ids:
        # Re-read the user each pass: soft_delete_document commits per
        # document, which expires the object loaded by get_current_user.
        user = await db.get(User, user_id)
        try:
            access = await get_document_access(document_id, db=db, current_user=user)
        except HTTPException as exc:
            # A 404 (someone else's or a missing document) is not an error in a
            # bulk request: the user asked to remove a list of things and this
            # one is already gone, so it is reported rather than failing all.
            message = (
                exc.detail
                if isinstance(exc.detail, str)
                else "This document could not be deleted."
            )
            results.append(
                BulkItemResult(document_id=document_id, ok=False, message=message)
            )
            continue
        if access.document.owner_id != user.id:
            results.append(
                BulkItemResult(
                    document_id=document_id,
                    ok=False,
                    message="Only the owner can delete this document",
                )
            )
            continue

        outcome = await soft_delete_document(db, document_id)
        if not outcome.ok:
            results.append(
                BulkItemResult(
                    document_id=document_id, ok=False, message=outcome.detail
                )
            )
            continue

        await _revoke_tasks(outcome.task_ids)
        results.append(BulkItemResult(document_id=document_id, ok=True))
    return results


def _iter_object(handle, chunk_size: int = READ_CHUNK):
    """Yield the object in chunks, always closing the handle"""
    try:
        while chunk := handle.read(chunk_size):
            yield chunk
    finally:
        handle.close()


@router.get("/{document_id}/content")
async def get_document_content(
    access: DocumentAccess = Depends(require_read_access),
):
    """Streams the original uploaded document for the viewer

    Served through the backend rather than as a presigned URL so that the
    document share policies can apply.
    """
    document = access.document
    try:
        handle = await run_in_threadpool(
            get_storage().download_file, document.storage_key
        )
    except ObjectNotFoundError:
        logger.error(
            f"Object missing for document {document.id}: {document.storage_key}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The original file is no longer in storage",
        ) from None

    extension = document.filename.rsplit(".", 1)[-1] if "." in document.filename else ""
    fallback = f"document-{document.id}"
    suffix = f".{extension}" if extension else ""
    filename = f"{safe_stem(document.filename, fallback)}{suffix}"
    unicode_filename = f"{display_stem(document.filename, fallback)}{suffix}"
    return StreamingResponse(
        _iter_object(handle),
        media_type=document.file_type or "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(
                filename, disposition="inline", unicode_filename=unicode_filename
            )
        },
    )


@router.get("/{document_id}/regions/{region_id}/image")
async def get_region_image(
    region_id: UUID,
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Streams the cropped picture of a figure region for the viewer

    Served through the backend for the same reason as the original file.
    """
    # Joined through the page so a region of another document is not found
    image_key = await db.scalar(
        select(RegionImage.image_key)
        .join(Region, RegionImage.region_id == Region.id)
        .join(Page, Region.page_id == Page.id)
        .where(
            Region.id == region_id,
            Page.document_id == access.document.id,
            RegionImage.image_key.is_not(None),
        )
    )
    if image_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This region has no image",
        )

    try:
        handle = await run_in_threadpool(get_storage().download_file, image_key)
    except ObjectNotFoundError:
        logger.error(f"Object missing for region {region_id}: {image_key}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The image is no longer in storage",
        ) from None

    return StreamingResponse(
        _iter_object(handle),
        media_type="image/png",
        # Region ids are new on every reprocess, so a crop never changes
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )


def _thumbnail_not_available() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "detail": "This page has no thumbnail",
            "code": "THUMBNAIL_NOT_AVAILABLE",
        },
    )


@router.get("/{document_id}/pages/{page_number}/thumbnail")
async def get_page_thumbnail(
    page_number: int,
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Streams the WebP thumbnail of a page for the viewer's sidebar.

    While a job is running, a page without a row yet is served from its
    deterministic key, since the worker uploads thumbnails before OCR.
    """
    document_id = access.document.id
    page = (
        await db.execute(
            select(Page.thumbnail_key).where(
                Page.document_id == document_id,
                Page.page_number == page_number,
            )
        )
    ).first()
    if page is not None:
        image_key = page.thumbnail_key
    elif page_number >= 1 and await db.scalar(
        select(Job.id)
        .where(
            Job.document_id == document_id,
            Job.status.in_(("queued", "processing")),
        )
        .limit(1)
    ):
        image_key = thumbnail_key(document_id, page_number)
    else:
        image_key = None
    if image_key is None:
        raise _thumbnail_not_available()

    try:
        handle = await run_in_threadpool(get_storage().download_file, image_key)
    except ObjectNotFoundError:
        raise _thumbnail_not_available() from None

    return StreamingResponse(
        _iter_object(handle),
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/{document_id}/pages/{page_number}/image")
async def get_page_image(
    page_number: int,
    access: DocumentAccess = Depends(require_read_access),
    db: AsyncSession = Depends(get_db),
):
    """Streams the upright page image for a page that was rotated before OCR.

    Served through the backend for the same reason as the original file.
    Returns 404 when the page was not corrected (no stored image).
    """
    image_key = await db.scalar(
        select(Page.page_image_key).where(
            Page.document_id == access.document.id,
            Page.page_number == page_number,
            Page.page_image_key.is_not(None),
        )
    )
    if image_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This page has no corrected image",
        )

    try:
        handle = await run_in_threadpool(get_storage().download_file, image_key)
    except ObjectNotFoundError:
        logger.error(f"Object missing for page {page_number}: {image_key}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The page image is no longer in storage",
        ) from None

    return StreamingResponse(
        _iter_object(handle),
        media_type="image/png",
        # The key is stable per page number; a reprocess rewrites the same
        # key, so it is cached per session but not immutable.
        headers={"Cache-Control": "private, max-age=3600"},
    )
