from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Document, User

from app.core.database import get_db
from app.core.security import get_current_user

_NO_ACCESS = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
)


@dataclass(frozen=True)
class DocumentAccess:
    """A document the caller may read"""

    document: Document

    @property
    def can_edit(self) -> bool:
        # Determines whether a user can edit the document
        # TODO: block edit access for read-only collaborators
        return True


async def get_document_access(
    document_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentAccess:
    """Resolve the document, rejecting anything the caller does not own"""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalars().first()
    if document is None or document.owner_id != current_user.id:
        raise _NO_ACCESS

    # TODO: Extend this function to return document access for shared documents
    return DocumentAccess(document=document)


async def require_read_access(
    access: DocumentAccess = Depends(get_document_access),
) -> DocumentAccess:
    return access


async def require_edit_access(
    access: DocumentAccess = Depends(get_document_access),
) -> DocumentAccess:
    # TODO: block edit access for read-only collaborators
    return access


async def require_owner_access(
    access: DocumentAccess = Depends(get_document_access),
    current_user: User = Depends(get_current_user),
) -> DocumentAccess:
    if access.document.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "detail": "Only the owner can delete this document",
                "code": "NOT_DOCUMENT_OWNER",
            },
        )
    return access


def require_completed(access: DocumentAccess) -> None:
    """Guard mutations and exports on a finished document"""
    if access.document.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": "Document has not finished processing",
                "code": "DOCUMENT_NOT_PROCESSED",
            },
        )
