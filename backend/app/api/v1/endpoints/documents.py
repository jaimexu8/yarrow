import hashlib
import logging
from uuid import UUID, uuid4

import filetype
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Job, Page, User
from yarrow_storage import ObjectNotFoundError, get_storage

from app.core.config import settings
from app.core.database import get_db
from app.core.queue import enqueue_document_processing, revoke_document_processing
from app.core.security import get_current_user
from app.deps import (
    DocumentAccess,
    require_edit_access,
    require_owner_access,
    require_read_access,
)
from app.schemas import (
    DocumentDetail,
    DocumentOut,
    DocumentRename,
    JobOut,
    PageOut,
    UploadAccepted,
    UploadRejected,
    UploadResponse,
)
from app.services.document_deletion import delete_document, delete_stored_objects
from app.services.export import content_disposition, display_stem, safe_stem
from app.services.failure_messages import (
    DOCUMENT_FAILED_MESSAGE,
    QUEUE_FAILED_MESSAGE,
    public_error_message,
)
from app.services.reprocess import (
    ReprocessError,
    documents_out,
    reprocess_document,
    reprocess_info_for,
)

router = APIRouter()
logger = logging.getLogger(__name__)

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


async def _find_earlier_upload(
    db: AsyncSession, owner_id: UUID, upload_id: str
) -> UploadAccepted | None:
    """The result of this user's earlier upload with the same client id."""
    result = await db.execute(
        select(Document).where(
            Document.owner_id == owner_id, Document.client_upload_id == upload_id
        )
    )
    document = result.scalars().first()
    if document is None:
        return None
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


@router.post("/upload", response_model=UploadResponse)
async def upload_documents(
    files: list[UploadFile] = File(...),
    client_upload_ids: list[str] | None = Form(None),
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
    """
    if client_upload_ids is not None:
        if len(client_upload_ids) != len(files):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Send exactly one client_upload_ids value per file.",
            )
        if len(set(client_upload_ids)) != len(client_upload_ids) or any(
            not 1 <= len(upload_id) <= MAX_UPLOAD_ID_LENGTH
            for upload_id in client_upload_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
    # Running total, so that several files in one request cannot each pass a
    # quota check that they only collectively exceed.
    used_bytes = current_user.storage_used_bytes or 0

    for index, upload in enumerate(files):
        filename = upload.filename or "unnamed"
        upload_id = client_upload_ids[index] if client_upload_ids else None

        if upload_id:
            earlier = await _find_earlier_upload(db, current_user.id, upload_id)
            if earlier is not None:
                # A retry of an upload that already succeeded (its response
                # was lost): report the stored document, store nothing new.
                accepted.append(earlier)
                continue

        size, _sha256, extension = await _measure(upload)

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
        if used_bytes + size > settings.STORAGE_QUOTA_BYTES:
            rejected.append(
                UploadRejected(
                    filename=filename,
                    reason="Not enough storage left in your account for this file.",
                )
            )
            continue

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

        document = Document(
            id=document_id,
            owner_id=current_user.id,
            filename=filename,
            file_size_bytes=size,
            file_type=CONTENT_TYPE_BY_EXTENSION[extension],
            storage_key=key,
            status="queued",
            client_upload_id=upload_id,
        )
        job = Job(
            id=uuid4(),
            document_id=document_id,
            status="queued",
            current_stage="queued",
            pages_processed=0,
            total_pages=0,
        )
        db.add_all([document, job])
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
            item.task_id = enqueue_document_processing(item.job_id)
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
            detail=[item.model_dump() for item in rejected],
        )
    return UploadResponse(accepted=accepted, rejected=rejected)


@router.get("/", response_model=list[DocumentOut])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Document)
        .where(Document.owner_id == current_user.id)
        .order_by(Document.created_at.desc())
    )
    return await documents_out(db, list(result.scalars().all()))


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
    detail = DocumentDetail.model_validate(document)
    detail.reprocess = (await reprocess_info_for(db, [document]))[document.id]
    # Validated one by one so each gets the user-facing error filter (US-11).
    detail.jobs = [JobOut.model_validate(job) for job in jobs.scalars()]
    detail.pages = [PageOut.model_validate(page) for page in pages.scalars()]
    return detail


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


@router.post("/{document_id}/reprocess", response_model=DocumentOut)
async def reprocess_document_endpoint(
    access: DocumentAccess = Depends(require_edit_access),
    db: AsyncSession = Depends(get_db),
):
    """Reprocess a document without uploading it again
    
    Interrupted, failed or partly failed documents reprocess only the pages
    that are not completed, so the task resumes and finished pages are kept.
    Fully processed (or canceled) documents are processed again in full.
    """
    document_id = access.document.id
    outcome = await reprocess_document(db, document_id, enqueue_document_processing)
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
):
    """Cancel a document's queued processing job (US-42).

    Only a queued job can be canceled; once a worker has started it, or it has
    finished, this is a 409 and nothing changes. The job row is locked, and the
    worker locks the same row before starting, so a cancel and a worker
    picking the job up at the same moment cannot both succeed.
    """
    document = access.document
    job = (
        await db.execute(
            select(Job)
            .where(Job.document_id == document.id)
            .order_by(Job.created_at.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if job is None or job.status != "queued":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only documents waiting in the queue can be canceled.",
        )

    job.status = "canceled"
    document.status = "canceled"
    document.error_message = None
    task_id = job.celery_task_id
    await db.commit()

    # After the commit, so a worker that gets the message anyway sees the
    # canceled row and skips it.
    if task_id:
        try:
            # A blocking network call; kept off the event loop so a slow
            # broker cannot stall other requests.
            await run_in_threadpool(revoke_document_processing, task_id)
        except Exception:
            # The cancel already stands; the worker's check covers this.
            logger.exception(f"Revoking task {task_id} failed")

    await db.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document_endpoint(
    access: DocumentAccess = Depends(require_owner_access),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a document, its extracted data and its jobs.

    Allowed in any processing state. A worker that runs it anyway finds its
    job gone. If processing is saving results for this document at that moment,
    the request answers 409 after a short wait instead of hanging, and can
    simply be retried.
    """

    document_id = access.document.id
    outcome = await delete_document(db, document_id)
    if not outcome.ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": outcome.detail, "code": outcome.error.value},
        )

    for task_id in outcome.task_ids:
        try:
            await run_in_threadpool(revoke_document_processing, task_id)
        except Exception:
            logger.exception(f"Revoking task {task_id} failed")

    if outcome.storage_keys:
        orphaned = await run_in_threadpool(
            delete_stored_objects, get_storage(), outcome.storage_keys
        )
        if orphaned:
            logger.error(
                f"Deleted document {document_id} left "
                f"{len(orphaned)} stored object(s) behind: {orphaned}"
            )

    return Response(status_code=status.HTTP_204_NO_CONTENT)

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
