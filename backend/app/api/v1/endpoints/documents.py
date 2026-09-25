import hashlib
import logging
from uuid import UUID, uuid4

import filetype
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, Job, Page, User
from yarrow_storage import get_storage

from app.core.config import settings
from app.core.database import get_db
from app.core.queue import enqueue_document_processing
from app.core.security import get_current_user
from app.schemas import (
    DocumentDetail,
    DocumentOut,
    UploadAccepted,
    UploadRejected,
    UploadResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Sniffed from the magic bytes, never from the filename or the browser's
# Content-Type. These are exactly the types worker DocumentParser has a loader
# for -- accepting anything else queues a job that can only fail.
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "gif"}
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Accept one or more files, store them, and queue a job for each.

    Bulk by design (backlog #8). One unusable file does not reject the batch:
    each is reported in `accepted` or `rejected` so the caller can show a
    per-file result.
    """
    storage = get_storage()
    accepted: list[UploadAccepted] = []
    rejected: list[UploadRejected] = []
    # Running total, so that several files in one request cannot each pass a
    # quota check that they only collectively exceed.
    used_bytes = current_user.storage_used_bytes or 0

    for upload in files:
        filename = upload.filename or "unnamed"
        size, _sha256, extension = await _measure(upload)

        if size == 0:
            rejected.append(UploadRejected(filename=filename, reason="File is empty"))
            continue
        if extension not in ALLOWED_EXTENSIONS:
            rejected.append(
                UploadRejected(
                    filename=filename,
                    reason=f"Unsupported file type ({extension or 'unrecognized'})",
                )
            )
            continue
        if size > settings.MAX_UPLOAD_BYTES:
            rejected.append(
                UploadRejected(
                    filename=filename,
                    reason=f"File exceeds the {settings.MAX_UPLOAD_BYTES} byte limit",
                )
            )
            continue
        if used_bytes + size > settings.STORAGE_QUOTA_BYTES:
            rejected.append(
                UploadRejected(filename=filename, reason="Storage quota exceeded")
            )
            continue

        document_id = uuid4()
        key = _storage_key(document_id)
        try:
            # Object first: an object with no row is a cheap orphan for the
            # sweeper, whereas a row with no object breaks the job.
            storage.upload_file(upload.file, key)
        except Exception as exc:
            logger.exception(f"Storing {filename} failed")
            rejected.append(
                UploadRejected(filename=filename, reason=f"Could not be stored: {exc}")
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
        item.task_id = enqueue_document_processing(item.job_id)

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
    return list(result.scalars().all())


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalars().first()
    # 404 rather than 403 for someone else's document, so that ids cannot be
    # probed for existence.
    if document is None or document.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    jobs = await db.execute(
        select(Job).where(Job.document_id == document_id).order_by(Job.created_at)
    )
    pages = await db.execute(
        select(Page).where(Page.document_id == document_id).order_by(Page.page_number)
    )
    detail = DocumentDetail.model_validate(document)
    detail.jobs = [j for j in jobs.scalars().all()]
    detail.pages = [p for p in pages.scalars().all()]
    return detail
