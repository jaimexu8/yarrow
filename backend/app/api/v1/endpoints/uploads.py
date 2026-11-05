"""Resumable uploads of large files (US-80).

The browser creates a session (declaring the file's name, size and chunk
size), PUTs each chunk by index, and finalizes once every chunk is
confirmed. After a dropped connection or a page reload it GETs the session
and sends only the chunks the server does not list in ``received_chunks``.

Small files keep using the single-request POST /documents/upload; the two
paths end the same way, with one Document and one queued Job, and share the
same type, size and quota rules.
"""

import hashlib
import logging
import math
import tempfile
from io import BytesIO
from uuid import UUID, uuid4

import filetype
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import (
    Batch,
    Document,
    Job,
    UploadChunk,
    UploadSession,
    User,
)
from yarrow_storage import StorageProvider, get_storage

from app.api.v1.endpoints.documents import (
    ACCEPTED_TYPES_TEXT,
    ALLOWED_EXTENSIONS,
    CONTENT_TYPE_BY_EXTENSION,
    SNIFF_BYTES,
    _accepted_for,
    _duplicate_rejection,
    _get_or_create_batch,
    _identical_document,
    _megabytes,
    _storage_key,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.queue import enqueue_document_processing, job_priority
from app.core.security import get_current_user
from app.schemas import (
    ChunkReceipt,
    UploadAccepted,
    UploadFinalize,
    UploadSessionCreate,
    UploadSessionOut,
)
from app.services.failure_messages import QUEUE_FAILED_MESSAGE
from app.services.storage_usage import get_storage_usage
from app.services.upload_sessions import (
    chunk_key,
    delete_objects,
    discard_chunks,
    is_expired,
    new_expiry,
    purge_expired_upload_sessions,
    utcnow,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# The browser picks the chunk size within these bounds. The lower bound keeps
# the first chunk big enough to identify the file type from; the upper one
# bounds what a single request holds in memory.
MIN_CHUNK_BYTES = 64 * 1024
MAX_CHUNK_BYTES = 16 * 1024 * 1024
# Assembled files stay in memory up to this size, then spill to disk.
SPOOL_IN_MEMORY = 8 * 1024 * 1024

UNSUPPORTED_MESSAGE = (
    f"Unsupported file type. Yarrow accepts {ACCEPTED_TYPES_TEXT} files."
)
QUOTA_MESSAGE = "Not enough storage left in your account for this file."
EXPIRED_MESSAGE = (
    "This upload is no longer available to resume. Start the upload again."
)
STORAGE_MESSAGE = "Could not be stored right now. Please try again."
CORRUPT_MESSAGE = "The uploaded file did not arrive intact. Please upload it again."
FILE_MISMATCH_MESSAGE = (
    "This upload was finished with a different file. Upload the file again."
)
FILE_MISMATCH_CODE = "UPLOAD_FILE_MISMATCH"
DUPLICATE_CODE = "UPLOAD_DUPLICATE"
CHECKSUM_MESSAGE = (
    "The uploaded file does not match the checksum it was sent with. "
    "Please upload it again."
)


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _expected_chunk_size(upload: UploadSession, index: int) -> int:
    """Every chunk is chunk_size_bytes, except a shorter last one."""
    if index < upload.chunk_count - 1:
        return upload.chunk_size_bytes
    return upload.total_size_bytes - upload.chunk_size_bytes * (upload.chunk_count - 1)


async def _received(
    db: AsyncSession, session_id: UUID
) -> tuple[list[int], int, list[str]]:
    """Confirmed chunk indexes (ascending), their total size and their
    sha256s (in the same order)."""
    rows = (
        await db.execute(
            select(UploadChunk.chunk_index, UploadChunk.size_bytes, UploadChunk.sha256)
            .where(UploadChunk.session_id == session_id)
            .order_by(UploadChunk.chunk_index)
        )
    ).all()
    return (
        [row.chunk_index for row in rows],
        sum(row.size_bytes for row in rows),
        [row.sha256 for row in rows],
    )


async def _session_out(db: AsyncSession, upload: UploadSession) -> UploadSessionOut:
    indexes, received_bytes, checksums = await _received(db, upload.id)
    if upload.status == "completed":
        # The chunks are discarded once the document exists; the whole file
        # is on the server, and file_sha256 identifies it.
        indexes = list(range(upload.chunk_count))
        received_bytes = upload.total_size_bytes
        checksums = []
    return UploadSessionOut(
        id=upload.id,
        filename=upload.filename,
        total_size_bytes=upload.total_size_bytes,
        chunk_size_bytes=upload.chunk_size_bytes,
        chunk_count=upload.chunk_count,
        received_chunks=indexes,
        received_checksums=checksums,
        received_bytes=received_bytes,
        file_sha256=upload.file_sha256,
        status=upload.status,
        error_message=upload.error_message,
        expires_at=upload.expires_at,
        batch_id=upload.batch_id,
        document_id=upload.document_id,
    )


async def _load(
    db: AsyncSession, session_id: UUID, owner_id: UUID, *, lock: bool = False
) -> UploadSession:
    """The caller's session. Someone else's is a 404, like a document."""
    query = select(UploadSession).where(UploadSession.id == session_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    upload = (await db.execute(query)).scalar_one_or_none()
    if upload is None or upload.owner_id != owner_id:
        raise _error(status.HTTP_404_NOT_FOUND, "Upload not found")
    return upload


def _require_open(upload: UploadSession) -> None:
    """Refuse chunks for a session that can no longer take them."""
    if upload.status == "completed":
        raise _error(status.HTTP_409_CONFLICT, "This upload is already finished.")
    if upload.status == "failed":
        raise _error(
            status.HTTP_400_BAD_REQUEST, upload.error_message or CORRUPT_MESSAGE
        )
    if upload.status == "canceled" or is_expired(upload):
        raise _error(status.HTTP_410_GONE, EXPIRED_MESSAGE)


async def _lock_user(db: AsyncSession, user_id: UUID) -> User:
    """The user row, locked until commit, like the single-request upload."""
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def _fail(
    db: AsyncSession, storage: StorageProvider, upload: UploadSession, reason: str
) -> HTTPException:
    """Mark the session failed for good and drop its chunks.

    For content problems (wrong type, corrupted, wrong checksum) that sending
    the same file again cannot fix by resuming.
    """
    upload.status = "failed"
    upload.error_message = reason
    await db.commit()
    await discard_chunks(db, storage, upload.id)
    return _error(status.HTTP_400_BAD_REQUEST, reason)


def _duplicate_error(filename: str, existing: Document) -> HTTPException:
    """The US-79 duplicate warning for a finalize.

    The body is the single-request upload's ``UploadRejected`` (``reason``,
    ``retryable`` and ``duplicate_of``), plus ``detail`` and ``code`` so it
    reads like every other error of this API. 409, not 400: the file is fine
    and the session stays open, so finalizing again with ``allow_duplicate``
    stores the copy without sending any chunk again.
    """
    rejection = _duplicate_rejection(filename, existing)
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "detail": rejection.reason,
            "code": DUPLICATE_CODE,
            **rejection.model_dump(mode="json"),
        },
    )


async def _check_batch(db: AsyncSession, batch_id: UUID, owner_id: UUID) -> None:
    """Refuse someone else's batch (US-31) without creating one."""
    existing = await db.get(Batch, batch_id)
    if existing is not None and existing.owner_id != owner_id:
        raise _error(status.HTTP_404_NOT_FOUND, "Batch not found")


@router.post("", response_model=UploadSessionOut, status_code=status.HTTP_201_CREATED)
async def create_upload_session(
    payload: UploadSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start a resumable upload. Nothing is stored yet.

    Everything that can be decided from the declared size and name is
    decided here, before the user waits for hundreds of megabytes to
    transfer: the type (from the name for now; the contents are checked on
    the first chunk and again at finalize), the size limit and the quota.
    """
    # Opportunistic cleanup of abandoned uploads, like the trash purge.
    await purge_expired_upload_sessions(db, get_storage)

    if _extension_of(payload.filename) not in ALLOWED_EXTENSIONS:
        raise _error(status.HTTP_400_BAD_REQUEST, UNSUPPORTED_MESSAGE)
    if payload.total_size_bytes > settings.MAX_UPLOAD_BYTES:
        raise _error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "File is too large. The limit is "
            f"{_megabytes(settings.MAX_UPLOAD_BYTES)} per file.",
        )
    if not MIN_CHUNK_BYTES <= payload.chunk_size_bytes <= MAX_CHUNK_BYTES:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            f"Chunk size must be between {MIN_CHUNK_BYTES} and "
            f"{MAX_CHUNK_BYTES} bytes.",
        )
    if payload.batch_id is not None:
        await _check_batch(db, payload.batch_id, current_user.id)

    user = await _lock_user(db, current_user.id)
    usage = await get_storage_usage(db, user)
    if payload.total_size_bytes > usage.remaining_bytes:
        message = QUOTA_MESSAGE
        if usage.reserved_bytes and (
            usage.used_bytes + payload.total_size_bytes <= usage.quota_bytes
        ):
            message += (
                " Uploads you have not finished count toward it until they "
                "finish or expire."
            )
        raise _error(status.HTTP_413_CONTENT_TOO_LARGE, message)

    upload = UploadSession(
        id=uuid4(),
        owner_id=user.id,
        filename=payload.filename,
        total_size_bytes=payload.total_size_bytes,
        chunk_size_bytes=payload.chunk_size_bytes,
        chunk_count=math.ceil(payload.total_size_bytes / payload.chunk_size_bytes),
        expected_sha256=payload.sha256,
        batch_id=payload.batch_id,
        status="processing",
        expires_at=new_expiry(),
    )
    db.add(upload)
    await db.commit()
    return await _session_out(db, upload)


@router.get("/{session_id}", response_model=UploadSessionOut)
async def get_upload_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Which chunks the server has; the browser resumes from this."""
    upload = await _load(db, session_id, current_user.id)
    if upload.status != "completed" and (
        upload.status == "canceled" or is_expired(upload)
    ):
        raise _error(status.HTTP_410_GONE, EXPIRED_MESSAGE)
    return await _session_out(db, upload)


async def _read_body(request: Request, limit: int) -> bytes:
    """The request body, refusing to buffer more than ``limit`` bytes."""
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > limit:
            raise _error(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "This part is larger than the upload declared.",
            )
    return bytes(body)


@router.put("/{session_id}/chunks/{index}", response_model=ChunkReceipt)
async def put_chunk(
    session_id: UUID,
    index: int,
    request: Request,
    x_chunk_sha256: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Store one chunk. Safe to retry.

    The same index with the same bytes is accepted again without storing
    anything (``already_received``), which is what a retry after a timeout
    looks like when the first attempt did arrive. Different bytes for an
    index that is already confirmed are refused with 409, so a confirmed
    chunk is never silently replaced.

    ``X-Chunk-SHA256`` (optional, hex) is compared with what arrived, so a
    chunk damaged in transit is refused rather than stored.
    """
    upload = await _load(db, session_id, current_user.id)
    _require_open(upload)
    if not 0 <= index < upload.chunk_count:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            f"Part {index} does not exist; this upload has {upload.chunk_count} parts.",
        )
    expected = _expected_chunk_size(upload, index)
    body = await _read_body(request, expected)
    if len(body) != expected:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            f"Part {index} arrived incomplete ({len(body)} of {expected} bytes). "
            "Send it again.",
        )
    digest = hashlib.sha256(body).hexdigest()
    if x_chunk_sha256 is not None and x_chunk_sha256.strip().lower() != digest:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            f"Part {index} was damaged in transit. Send it again.",
        )
    quota_left = settings.STORAGE_QUOTA_BYTES - (current_user.storage_used_bytes or 0)
    if upload.total_size_bytes > quota_left:
        # Space ran out since the upload started (other uploads finished).
        raise _error(status.HTTP_413_CONTENT_TOO_LARGE, QUOTA_MESSAGE)

    storage = get_storage()
    # Locked from here to commit: a concurrent retry of the same index, or a
    # finalize, waits instead of racing this one.
    upload = await _load(db, session_id, current_user.id, lock=True)
    _require_open(upload)

    existing = (
        await db.execute(
            select(UploadChunk).where(
                UploadChunk.session_id == upload.id,
                UploadChunk.chunk_index == index,
            )
        )
    ).scalar_one_or_none()
    already_received = existing is not None
    if existing is not None:
        if existing.sha256 != digest:
            raise _error(
                status.HTTP_409_CONFLICT,
                f"Part {index} was already received with different content.",
            )
    else:
        if index == 0:
            # The first part carries the file's signature: refuse a renamed
            # file now rather than after the whole transfer (US-37).
            kind = filetype.guess(body[:SNIFF_BYTES])
            if kind is None or kind.extension not in ALLOWED_EXTENSIONS:
                raise await _fail(db, storage, upload, UNSUPPORTED_MESSAGE)
        key = chunk_key(upload.id, index, digest)
        try:
            await run_in_threadpool(storage.upload_file, BytesIO(body), key)
        except Exception:
            # Storage errors can name buckets or credentials: log only.
            logger.exception(f"Storing part {index} of upload {upload.id} failed")
            raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_MESSAGE)
        db.add(
            UploadChunk(
                session_id=upload.id,
                chunk_index=index,
                size_bytes=len(body),
                sha256=digest,
                storage_key=key,
            )
        )
    # Activity keeps an upload resumable for another retention window.
    upload.expires_at = new_expiry()
    await db.commit()

    indexes, received_bytes, _ = await _received(db, upload.id)
    return ChunkReceipt(
        index=index,
        size_bytes=len(body),
        sha256=digest,
        already_received=already_received,
        received_bytes=received_bytes,
        received_count=len(indexes),
    )


def _assemble(
    storage: StorageProvider, keys: list[str]
) -> tuple[tempfile.SpooledTemporaryFile, int, str, bytes]:
    """Concatenate the chunks in order: (file, size, sha256, header).

    Runs in a thread (storage clients are blocking). The file is rewound and
    must be closed by the caller.
    """
    # Returned open on purpose; the caller closes it.
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_IN_MEMORY)  # noqa: SIM115
    try:
        digest = hashlib.sha256()
        size = 0
        header = b""
        for key in keys:
            data = storage.download_bytes(key)
            if len(header) < SNIFF_BYTES:
                header += data[: SNIFF_BYTES - len(header)]
            digest.update(data)
            spool.write(data)
            size += len(data)
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return spool, size, digest.hexdigest(), header


@router.post("/{session_id}/finalize", response_model=UploadAccepted)
async def finalize_upload(
    session_id: UUID,
    payload: UploadFinalize | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Assemble the chunks into one document and queue it, exactly once.

    The session row stays locked from here to the commit, so two finalize
    requests (a retry after a lost reply, or two tabs) take turns: the second
    finds the session completed and gets the same document back, with
    nothing stored or queued again.

    The optional body carries the browser's sha256 of its whole local file,
    computed chunk by chunk as it sent or re-read them, and the batch (US-31)
    of the run finishing the upload. The hash proves the assembled document
    is the file the user picked, not chunks of two files that happened to
    share a name, size and timestamp. The batch moves a session resumed in
    a later run (after a reload) into that run, so the run on screen is the
    one that counts the document.
    """
    payload = payload or UploadFinalize()
    storage = get_storage()
    upload = await _load(db, session_id, current_user.id, lock=True)

    if upload.status == "completed":
        document = (
            await db.get(Document, upload.document_id) if upload.document_id else None
        )
        if document is None:
            raise _error(
                status.HTTP_410_GONE,
                "This upload finished, but its document has since been deleted.",
            )
        if (
            payload.sha256
            and upload.file_sha256
            and payload.sha256 != upload.file_sha256
        ):
            # Another file under the same name, size and timestamp: it must
            # not be reported as this document.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"detail": FILE_MISMATCH_MESSAGE, "code": FILE_MISMATCH_CODE},
            )
        if payload.batch_id is not None and document.batch_id is None:
            # As with a retried single upload (US-31): a document stored
            # outside any run joins the run being retried. One that already
            # counts in a run stays there, so no run's counts go down.
            batch = await _get_or_create_batch(db, current_user.id, payload.batch_id)
            document.batch_id = batch.id
            await db.commit()
        return await _accepted_for(db, document)
    _require_open(upload)
    batch_id = payload.batch_id or upload.batch_id

    chunks = list(
        await db.scalars(
            select(UploadChunk)
            .where(UploadChunk.session_id == upload.id)
            .order_by(UploadChunk.chunk_index)
        )
    )
    missing = upload.chunk_count - len(chunks)
    if missing:
        raise _error(
            status.HTTP_409_CONFLICT,
            f"{missing} of {upload.chunk_count} parts have not been uploaded yet.",
        )

    try:
        spool, size, digest, header = await run_in_threadpool(
            _assemble, storage, [chunk.storage_key for chunk in chunks]
        )
    except Exception:
        logger.exception(f"Assembling upload {upload.id} failed")
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_MESSAGE)

    document_id = uuid4()
    key = _storage_key(document_id)
    try:
        # Validated again on the assembled bytes, never trusting what was
        # declared at create time or checked per chunk.
        kind = filetype.guess(header)
        extension = kind.extension if kind else None
        if size != upload.total_size_bytes:
            raise await _fail(db, storage, upload, CORRUPT_MESSAGE)
        if upload.expected_sha256 and digest != upload.expected_sha256:
            raise await _fail(db, storage, upload, CHECKSUM_MESSAGE)
        if payload.sha256 and digest != payload.sha256:
            raise await _fail(db, storage, upload, CHECKSUM_MESSAGE)
        if extension not in ALLOWED_EXTENSIONS:
            raise await _fail(db, storage, upload, UNSUPPORTED_MESSAGE)
        if size > settings.MAX_UPLOAD_BYTES:
            raise await _fail(
                db,
                storage,
                upload,
                "File is too large. The limit is "
                f"{_megabytes(settings.MAX_UPLOAD_BYTES)} per file.",
            )
        if batch_id is not None:
            await _check_batch(db, batch_id, current_user.id)
        if not payload.allow_duplicate:
            # Same rule and order as the single-request upload (US-79): after
            # the checks on the file itself, before quota. Checked here too,
            # before the (slow) store, so a duplicate costs no storage write;
            # the authoritative check is repeated under the user lock below.
            existing = await _identical_document(db, current_user.id, digest)
            if existing is not None:
                raise _duplicate_error(upload.filename, existing)
        try:
            # Object first, as in the single-request upload: an object with
            # no row is a cheap orphan, a row with no object breaks the job.
            await run_in_threadpool(storage.upload_file, spool, key)
        except Exception:
            logger.exception(f"Storing finalized upload {upload.id} failed")
            raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_MESSAGE)
    finally:
        spool.close()

    # Taken after the slow assembly, so other uploads by this user are only
    # held up for the quota check and the inserts. Lock order is always
    # session, then user; nothing locks them the other way round.
    user = await _lock_user(db, current_user.id)
    if not payload.allow_duplicate:
        # Under the user lock, like the single-request upload: an identical
        # upload that committed while this one was storing is found now.
        existing = await _identical_document(db, user.id, digest)
        if existing is not None:
            # Built before the rollback, which expires the loaded rows.
            error = _duplicate_error(upload.filename, existing)
            await db.rollback()
            await run_in_threadpool(delete_objects, storage, [key])
            raise error
    used = user.storage_used_bytes or 0
    if used + size > settings.STORAGE_QUOTA_BYTES:
        # Not a problem with the file: the session stays open, so the user
        # can free up space and finalize again within the retention window.
        await db.rollback()
        await run_in_threadpool(delete_objects, storage, [key])
        raise _error(status.HTTP_413_CONTENT_TOO_LARGE, QUOTA_MESSAGE)

    batch = (
        await _get_or_create_batch(db, user.id, batch_id)
        if batch_id is not None
        else None
    )
    document = Document(
        id=document_id,
        owner_id=user.id,
        filename=upload.filename,
        file_size_bytes=size,
        file_type=CONTENT_TYPE_BY_EXTENSION[extension],
        storage_key=key,
        status="queued",
        batch_id=batch.id if batch else None,
        # The US-79 fingerprint, so later uploads of these bytes are warned.
        content_sha256=digest,
    )
    job = Job(
        id=uuid4(),
        document_id=document_id,
        status="queued",
        current_stage="queued",
        pages_processed=0,
        total_pages=0,
        requested_by_id=user.id,
    )
    db.add_all([document, job])
    # Flushed before the session points at it (documents FK).
    await db.flush()
    user.storage_used_bytes = used + size
    upload.status = "completed"
    upload.document_id = document_id
    upload.file_sha256 = digest
    upload.batch_id = batch.id if batch else None
    upload.error_message = None
    # A repeated finalize keeps returning this document for another window.
    upload.expires_at = new_expiry()
    # Committed before queueing: a worker can start within milliseconds.
    await db.commit()

    accepted = UploadAccepted(
        document_id=document_id,
        job_id=job.id,
        task_id="",
        filename=document.filename,
        file_size_bytes=size,
    )
    try:
        accepted.task_id = enqueue_document_processing(
            job.id, priority=job_priority(size)
        )
        job.celery_task_id = accepted.task_id
    except Exception:
        # Stored and counted, so the upload succeeded; only starting
        # processing failed. Same handling as the single-request upload.
        logger.exception(f"Queueing job {job.id} failed")
        job.status = "failed"
        job.error_message = QUEUE_FAILED_MESSAGE
        document.status = "failed"
        document.error_message = QUEUE_FAILED_MESSAGE
        accepted.status = "failed"
        accepted.message = QUEUE_FAILED_MESSAGE
    await db.commit()

    # The temporary chunks are no longer needed.
    await discard_chunks(db, storage, upload.id)
    return accepted


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abort_upload(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Abandon an upload and delete what was sent of it.

    A finished upload's document is not affected; only the session goes.
    """
    storage = get_storage()
    upload = await _load(db, session_id, current_user.id, lock=True)
    keys = list(
        await db.scalars(
            select(UploadChunk.storage_key).where(UploadChunk.session_id == upload.id)
        )
    )
    failed = await run_in_threadpool(delete_objects, storage, keys) if keys else []
    if failed:
        # Keep the rows so the sweeper retries the objects; the session is
        # already unusable.
        upload.status = "canceled"
        upload.expires_at = utcnow()
    else:
        await db.delete(upload)
    await db.commit()
